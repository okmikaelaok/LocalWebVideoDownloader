"""Own the local WebView2 child process and validate its bounded capture messages."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO

from localwebvideodownloader.browser_capture import MAX_NATIVE_MESSAGE_BYTES, CapturedSource
from localwebvideodownloader.errors import AppError, DependencyError, ValidationError
from localwebvideodownloader.security import validate_web_url

_BROWSER_ERRORS = {
    "runtime_missing": (
        "Microsoft Edge WebView2 Runtime is missing. Install it, then reopen the capture browser."
    ),
    "initialization_failed": (
        "The capture browser could not initialize. Close it and try again; "
        "check the WebView2 Runtime installation if this continues."
    ),
    "protocol_error": (
        "The capture browser returned an invalid startup message. "
        "Rebuild it with scripts/setup-desktop.ps1."
    ),
    "capture_too_large": (
        "That capture exceeds the message size limit. "
        "Select another source or inspect the page URL."
    ),
}


def browser_executable() -> Path:
    project = Path(__file__).resolve().parents[2]
    executable = (
        project
        / "desktop"
        / "CaptureBrowser"
        / "bin"
        / "Release"
        / "net8.0-windows"
        / "LocalWebVideoDownloader.exe"
    )
    if os.name != "nt":
        raise DependencyError("The WebView2 capture browser currently requires Windows.")
    if not executable.is_file():
        raise DependencyError(
            "The capture browser has not been built. Run .\\scripts\\setup-desktop.ps1 first."
        )
    return executable


def read_browser_event(stream: BinaryIO) -> tuple[str, object] | None:
    line = stream.readline(MAX_NATIVE_MESSAGE_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_NATIVE_MESSAGE_BYTES or not line.endswith(b"\n"):
        raise ValidationError("The capture browser returned an oversized or incomplete message.")
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("The capture browser returned invalid JSON.") from exc
    if (
        not isinstance(value, Mapping)
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValidationError("The capture browser message version is unsupported.")
    event = value.get("event")
    if event == "capture":
        try:
            return "capture", CapturedSource.from_mapping(value.get("capture"))
        except AppError as exc:
            return "error", exc
    if event == "ready":
        return "ready", None
    if event == "error":
        code = value.get("code")
        message = _BROWSER_ERRORS.get(code) if isinstance(code, str) else None
        return "error", ValidationError(message or "The capture browser reported an error.")
    raise ValidationError("The capture browser sent an unknown message.")


class BrowserSession:
    """One browser window, with sensitive URLs carried only by anonymous pipes."""

    def __init__(self, notify: Callable[[str, object], None]) -> None:
        self.notify = notify
        self._process: subprocess.Popen[bytes] | None = None
        self._closing = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def run(self, initial_url: str | None = None) -> None:
        process = None
        timer = None
        reported_error = False
        try:
            url = validate_web_url(initial_url) if initial_url else None
            startup = (
                json.dumps({"version": 1, "initial_url": url}, ensure_ascii=False).encode("utf-8")
                + b"\n"
            )
            if len(startup) > MAX_NATIVE_MESSAGE_BYTES:
                raise ValidationError("The capture browser startup URL is too long.")
            with self._lock:
                if self._closing.is_set():
                    return
                process = subprocess.Popen(
                    [str(browser_executable()), "--capture-stdio"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._process = process
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(startup)
                process.stdin.flush()
            timer = threading.Timer(40, self._startup_timeout)
            timer.daemon = True
            timer.start()
            while not self._closing.is_set():
                try:
                    event = read_browser_event(process.stdout)
                except AppError as exc:
                    # Framing/schema failures terminate this child connection.
                    reported_error = True
                    self.notify("error", exc)
                    break
                if event is None:
                    break
                name, value = event
                if name == "ready":
                    self._ready.set()
                    timer.cancel()
                if name == "error":
                    reported_error = True
                if not self._closing.is_set():
                    self.notify(name, value)
            if not self._closing.is_set() and not self._ready.is_set() and not reported_error:
                raise DependencyError(
                    "The capture browser exited before starting. Run scripts/setup-desktop.ps1 "
                    "and check the .NET Desktop and WebView2 runtimes."
                )
        except AppError as exc:
            self.notify("error", exc)
        except OSError:
            if not self._closing.is_set():
                self.notify(
                    "error",
                    DependencyError(
                        "The capture browser process could not be started or contacted."
                    ),
                )
        finally:
            if timer:
                timer.cancel()
            self.close()
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.stdout:
                    process.stdout.close()
            self.notify("closed", None)

    def _startup_timeout(self) -> None:
        if not self._ready.is_set() and not self._closing.is_set():
            self.notify(
                "error",
                DependencyError(
                    "The capture browser took too long to start. "
                    "Close other capture windows and try again."
                ),
            )
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
            process = self._process
            if process is None or process.poll() is not None:
                return
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.write(b'{"version":1,"command":"close"}\n')
                    process.stdin.flush()
                    process.stdin.close()
            except OSError:
                pass

            # Ensure a wedged UI cannot leave an orphan child after its parent closes.
            def reap() -> None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

            # Keep Python alive for this bounded grace period if the GUI is already closing.
            threading.Thread(target=reap, daemon=False).start()
