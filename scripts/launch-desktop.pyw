"""Start the desktop service without a console and report startup failures visibly."""

import ctypes
import sys
import traceback
from pathlib import Path


def launch() -> int:
    project = Path(__file__).resolve().parents[1]
    log_path = project / "build" / "desktop-launch.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            # pythonw has no standard streams; retain diagnostics for startup failures.
            sys.stdout = sys.stderr = log
            try:
                from localwebvideodownloader.desktop import main

                result = main()
            except Exception:
                traceback.print_exc()
                result = 3
            finally:
                sys.stdout = sys.stderr = None
        if result:
            ctypes.windll.user32.MessageBoxW(
                None,
                "LocalWebVideoDownloader could not start or exited with an error.\n\n"
                "Run scripts\\setup-desktop.ps1 if setup is incomplete.\n"
                f"Details: {log_path}",
                "LocalWebVideoDownloader",
                0x10,
            )
        return result
    except OSError as exc:
        ctypes.windll.user32.MessageBoxW(
            None, str(exc), "LocalWebVideoDownloader", 0x10
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(launch())
