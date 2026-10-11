"""Chrome native-messaging bridge for opening a browser capture in the GUI."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

from localwebvideodownloader.browser_capture import MAX_NATIVE_MESSAGE_BYTES, CapturedSource
from localwebvideodownloader.errors import AppError, ValidationError
from localwebvideodownloader.security import redact_sensitive_text


def read_native_message(stream: BinaryIO) -> object:
    header = stream.read(4)
    if not header:
        raise EOFError
    if len(header) != 4:
        raise ValidationError("The native message header is incomplete.")
    (size,) = struct.unpack("=I", header)
    if size <= 0 or size > MAX_NATIVE_MESSAGE_BYTES:
        raise ValidationError("The native message is too large.")
    payload = stream.read(size)
    if len(payload) != size:
        raise ValidationError("The native message body is incomplete.")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("The native message is invalid JSON.") from exc


def write_native_message(stream: BinaryIO, message: object) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_NATIVE_MESSAGE_BYTES:
        raise ValidationError("The native response is too large.")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def _desktop_python() -> str:
    executable = Path(sys.executable)
    if os.name == "nt":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return str(executable)


def launch_desktop_capture(capture: CapturedSource) -> None:
    payload = json.dumps(capture.to_child_dict(), ensure_ascii=False).encode("utf-8")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [_desktop_python(), "-m", "localwebvideodownloader.browser_launch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        if process.stdin is None:
            raise OSError("The desktop process did not provide an input pipe.")
        process.stdin.write(payload)
        process.stdin.close()
    except OSError as exc:
        raise ValidationError(
            "The desktop application could not be opened.",
            diagnostic=redact_sensitive_text(exc),
        ) from exc


def handle_message(message: object) -> dict[str, object]:
    capture = CapturedSource.from_native_message(message)
    launch_desktop_capture(capture)
    return {
        "ok": True,
        "message": "Opened the selected source in LocalWebVideoDownloader.",
        "kind": capture.kind,
    }


def main() -> int:
    try:
        message = read_native_message(sys.stdin.buffer)
        response = handle_message(message)
    except EOFError:
        return 0
    except AppError as exc:
        response = {"ok": False, "error": exc.message}
    except Exception as exc:  # defensive protocol boundary
        response = {
            "ok": False,
            "error": "The browser capture could not be opened safely.",
            "diagnostic": redact_sensitive_text(exc),
        }
    write_native_message(sys.stdout.buffer, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
