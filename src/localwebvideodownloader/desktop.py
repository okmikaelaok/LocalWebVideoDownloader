"""Launch one native window; keep downloads in a local Python service."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import suppress
from typing import Any

from localwebvideodownloader.desktop_service import DesktopController, encode_command, read_event
from localwebvideodownloader.errors import AppError
from localwebvideodownloader.webview_browser import browser_executable


def main() -> int:
    process = None
    controller = None
    ready = threading.Event()
    timeout = None
    write_lock = threading.Lock()
    try:
        process = subprocess.Popen(
            [str(browser_executable()), "--desktop-stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({"version": 1, "initial_url": None}).encode() + b"\n")
        process.stdin.flush()

        def send(command: str, **values: Any) -> None:
            payload = encode_command(command, **values)
            with write_lock:
                if process.poll() is not None:
                    return
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except OSError:
                    if controller:
                        # Do not acquire the controller lock while holding the pipe lock.
                        controller.closed.set()
                        controller.cancel_event.set()

        controller = DesktopController(send)

        def startup_timeout() -> None:
            if not ready.is_set() and process.poll() is None:
                process.kill()

        timeout = threading.Timer(40, startup_timeout)
        timeout.daemon = True
        timeout.start()
        while (message := read_event(process.stdout)) is not None:
            if message["event"] == "ready":
                ready.set()
                timeout.cancel()
            controller.handle(message)
        if not ready.is_set():
            print(
                "LocalWebVideoDownloader could not start. Run scripts/setup-desktop.ps1.",
                file=sys.stderr,
            )
            return 3
        return process.wait(timeout=5)
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    except (OSError, subprocess.TimeoutExpired):
        print(
            "The desktop connection closed unexpectedly. Reopen the app and try again.",
            file=sys.stderr,
        )
        return 3
    finally:
        if timeout:
            timeout.cancel()
        if controller:
            # Signal cancellation without waiting on the controller lock. A
            # worker may hold it while a full pipe is waiting for the child.
            controller.signal_close()
        if process:
            # Reap the child before closing its buffered pipes. A worker can be
            # inside a pipe write when the UI stops reading; closing stdin first
            # would then wait indefinitely for the buffer lock.
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            if controller:
                controller.close()
            if controller and controller.worker:
                controller.worker.join(timeout=2)
            if process.stdin:
                with suppress(OSError):
                    process.stdin.close()
            if process.stdout:
                process.stdout.close()
        elif controller:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
