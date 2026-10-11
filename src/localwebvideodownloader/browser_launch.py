"""Receive one validated browser capture over stdin and launch the desktop GUI."""

from __future__ import annotations

import json
import sys

from localwebvideodownloader.browser_capture import MAX_NATIVE_MESSAGE_BYTES, CapturedSource
from localwebvideodownloader.errors import AppError, ValidationError
from localwebvideodownloader.gui import main as gui_main


def read_capture(stream: object | None = None) -> CapturedSource:
    source = stream or sys.stdin.buffer
    payload = source.read(MAX_NATIVE_MESSAGE_BYTES + 1)
    if len(payload) > MAX_NATIVE_MESSAGE_BYTES:
        raise ValidationError("The desktop capture message is too large.")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("The desktop capture message is invalid JSON.") from exc
    return CapturedSource.from_child_message(message)


def main() -> int:
    try:
        capture = read_capture()
    except AppError:
        return 2
    return gui_main(
        initial_url=capture.url,
        referer_url=capture.referer_url,
        auto_inspect=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
