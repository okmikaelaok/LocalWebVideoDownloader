"""Validated downloader service for the unified native UI, using anonymous pipes.

Only native controls send commands. Web pages have no access to this protocol.
Downloads reference inspected items instead of accepting arbitrary media objects.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from localwebvideodownloader.browser_capture import CapturedSource
from localwebvideodownloader.errors import AppError, InspectionError, ValidationError
from localwebvideodownloader.filenames import suggest_output_name, validate_output_name
from localwebvideodownloader.models import DownloadRequest, InspectionResult, ProgressEvent
from localwebvideodownloader.security import redact_sensitive_text, validate_web_url
from localwebvideodownloader.ytdlp_backend import YtDlpBackend, get_capabilities

MAX_DESKTOP_MESSAGE_BYTES = 1024 * 1024


def encode_command(command: str, **values: Any) -> bytes:
    payload = (
        json.dumps(
            {"version": 1, "command": command, **values}, ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        + b"\n"
    )
    if len(payload) > MAX_DESKTOP_MESSAGE_BYTES:
        raise ValidationError("The metadata is too large. Inspect one video or source at a time.")
    return payload


def read_event(stream: BinaryIO) -> dict[str, Any] | None:
    payload = stream.readline(MAX_DESKTOP_MESSAGE_BYTES + 1)
    if not payload:
        return None
    if len(payload) > MAX_DESKTOP_MESSAGE_BYTES or not payload.endswith(b"\n"):
        raise ValidationError("The desktop returned an oversized or incomplete message.")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("The desktop returned an invalid message.") from exc
    if (
        not isinstance(message, dict)
        or type(message.get("version")) is not int
        or message["version"] != 1
        or not isinstance(message.get("event"), str)
        or message["event"] not in {"ready", "inspect", "download", "cancel"}
    ):
        raise ValidationError("The desktop message version or action is unsupported.")
    return message


def _text(value: object, field: str, maximum: int = 8192) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValidationError(f"The {field} is invalid.")
    return value


def _index(value: object, length: int, field: str) -> int:
    if type(value) is not int or not 0 <= value < length:
        raise ValidationError(f"Choose a valid {field} from the inspected video.")
    return value


class DesktopController:
    def __init__(
        self,
        send: Callable[..., None],
        *,
        backend_factory: Callable[..., YtDlpBackend] = YtDlpBackend,
    ) -> None:
        self.send = send
        self.backend_factory = backend_factory
        self.inspection: InspectionResult | None = None
        self.backend: YtDlpBackend | None = None
        self.busy: str | None = None
        self.pending_inspection: dict[str, Any] | None = None
        self.cancel_event = threading.Event()
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self._last_progress = 0.0

    def handle(self, message: Mapping[str, Any]) -> None:
        with self.lock:
            if self.closed.is_set():
                return
            try:
                event = message.get("event")
                if event == "ready":
                    capabilities = get_capabilities()
                    self.send(
                        "init",
                        default_output_directory=str(Path(__file__).resolve().parents[2] / "VideosDownloaded"),
                        capabilities={
                            "yt_dlp_available": capabilities.yt_dlp_available,
                            "ffmpeg_available": bool(capabilities.ffmpeg_path),
                            "ffprobe_available": bool(capabilities.ffprobe_path),
                        },
                    )
                elif event == "cancel":
                    self.pending_inspection = None
                    self.cancel_event.set()
                elif event == "inspect":
                    url = validate_web_url(_text(message.get("url"), "URL"))
                    capture = message.get("capture")
                    if capture is not None and not isinstance(capture, Mapping):
                        raise ValidationError("The selected capture is invalid.")
                    request = {"url": url, "capture": capture}
                    if self.busy:
                        self.pending_inspection = request
                        self.send(
                            "busy",
                            operation=self.busy,
                            message="Source queued; inspection will follow the current operation.",
                        )
                    else:
                        self._inspect(request)
                elif event == "download":
                    if self.busy:
                        raise ValidationError("Wait for the current operation to finish.")
                    self._download(message)
                else:
                    raise ValidationError("The desktop action is unsupported.")
            except AppError as exc:
                self.send("error", operation="protocol", message=exc.message)
                self.send("busy", operation=self.busy, message="")

    def _start(self, operation: str, work: Callable[[], None]) -> None:
        self.busy = operation
        self.cancel_event = threading.Event()
        self.send("busy", operation=operation, message=f"Starting {operation}...")

        def run() -> None:
            try:
                work()
            except AppError as exc:
                if not self.closed.is_set():
                    self.send("error", operation=operation, message=exc.message)
            except Exception:
                if not self.closed.is_set():
                    self.send(
                        "error",
                        operation=operation,
                        message="The operation failed. Inspect the source again and retry.",
                    )
            finally:
                with self.lock:
                    self.busy = None
                    if not self.closed.is_set():
                        self.send("busy", operation=None, message="")
                        pending, self.pending_inspection = self.pending_inspection, None
                        if pending is not None:
                            self._inspect(pending)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _inspect(self, request: Mapping[str, Any]) -> None:
        self.inspection = None
        self.backend = None

        def work() -> None:
            capture = request.get("capture")
            selected = CapturedSource.from_mapping(capture) if capture is not None else None
            if selected is not None and selected.url != request["url"]:
                raise ValidationError("The selected source and its capture do not match.")
            backend = self.backend_factory(referer_url=selected.referer_url if selected else None)
            result = backend.inspect(request["url"])
            if not result.items:
                raise InspectionError(
                    "No supported video was found. Try the Browser tab and play it."
                )
            items = [
                {**item.to_public_dict(), "suggested_name": suggest_output_name(item.title)}
                for item in result.items
            ]
            if not self.closed.is_set():
                encode_command("inspection_complete", items=items)
                self.backend = backend
                self.inspection = result
                self.send("inspection_complete", items=items)

        self._start("inspection", work)

    def _download(self, message: Mapping[str, Any]) -> None:
        if self.inspection is None or self.backend is None:
            raise ValidationError("Inspect a video before downloading it.")
        item = self.inspection.items[
            _index(message.get("item_index"), len(self.inspection.items), "video")
        ]
        indices = message.get("caption_indices", [])
        if not isinstance(indices, list) or len(indices) > len(item.captions):
            raise ValidationError("The caption selection is invalid.")
        selected = [_index(value, len(item.captions), "caption") for value in indices]
        if len(set(selected)) != len(selected):
            raise ValidationError("Choose each caption only once.")
        captions = tuple(item.captions[index] for index in selected)
        if len({track.language.casefold() for track in captions}) != len(captions):
            raise ValidationError("Choose only one caption variant per language.")
        embed = message.get("embed_captions", False)
        if type(embed) is not bool or (embed and not captions):
            raise ValidationError("Select captions before enabling caption embedding.")
        audio = message.get("audio_format_id")
        if audio is not None:
            audio = _text(audio, "audio selection", 256)
        request = DownloadRequest(
            item=item,
            output_directory=Path(_text(message.get("output_directory"), "output folder")),
            format_id=_text(message.get("format_id", "best"), "quality selection", 256),
            audio_format_id=audio,
            captions=captions,
            embed_captions=embed,
            output_name=validate_output_name(message.get("output_name")),
        )
        backend = self.backend

        def work() -> None:
            self._last_progress = 0.0
            result = backend.download(
                request, progress=self._progress, cancel_event=self.cancel_event
            )
            if not self.closed.is_set():
                self.send(
                    "download_complete",
                    files=[str(path) for path in result.files],
                    existing_files=[str(path) for path in result.existing_files],
                    validated=result.validated,
                )

        self._start("download", work)

    def _progress(self, event: ProgressEvent) -> None:
        if self.closed.is_set():
            return
        now = time.monotonic()
        if event.phase == "downloading" and now - self._last_progress < 0.1:
            return
        self._last_progress = now
        self.send(
            "progress",
            phase=event.phase,
            fraction=event.fraction,
            downloaded_bytes=event.downloaded_bytes,
            total_bytes=event.total_bytes,
            speed_bytes_per_second=event.speed_bytes_per_second,
            eta_seconds=event.eta_seconds,
            message=redact_sensitive_text(event.message) if event.message else None,
        )

    def signal_close(self) -> None:
        """Cancel work without waiting for a possibly blocked pipe sender."""

        self.closed.set()
        self.cancel_event.set()

    def close(self) -> None:
        self.signal_close()
        with self.lock:
            self.pending_inspection = None
