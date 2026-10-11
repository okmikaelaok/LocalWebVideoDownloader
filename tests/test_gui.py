from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import tkinter as tk

    from localwebvideodownloader.gui import DownloaderWindow
except ImportError:
    tk = None

from localwebvideodownloader.models import InspectionResult, MediaFormat, MediaItem


@unittest.skipIf(tk is None, "Tkinter is not installed")
class LegacyGuiNameTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        self.root.withdraw()
        self.app = DownloaderWindow(self.root)
        self.items = (
            MediaItem(
                key="one",
                title="First: video?",
                media_id="one",
                source_url="https://example.test/one",
                formats=(MediaFormat(format_id="one", video_codec="avc1", audio_codec="aac"),),
            ),
            MediaItem(
                key="two",
                title="Second video",
                media_id="two",
                source_url="https://example.test/two",
            ),
        )
        self.app._handle_inspection_complete(
            InspectionResult(requested_url="https://example.test", items=self.items)
        )

    def tearDown(self) -> None:
        for callback in self.root.tk.call("after", "info"):
            # ttk's progress animation is a Tcl command list, not a Python callback.
            self.root.tk.call("after", "cancel", callback)
        self.root.destroy()

    def test_brand_name_and_capture_browser_is_not_opened_by_default(self) -> None:
        self.assertEqual(self.root.title(), "LocalWebVideoDownloader")
        self.assertIsNone(self.app.browser_session)

    def test_suggested_name_resets_when_selected_media_changes(self) -> None:
        self.assertEqual(self.app.output_name_var.get(), "First_ video_")
        self.app.output_name_var.set("My edit")
        self.app.media_combo.current(1)
        self.app._show_selected_item()
        self.assertEqual(self.app.output_name_var.get(), "Second video")
        self.app._clear_item_controls()
        self.assertEqual(self.app.output_name_var.get(), "")

    def test_edited_name_is_snapshotted_before_download(self) -> None:
        self.app.output_name_var.set("My video 100%")
        with patch("localwebvideodownloader.gui.threading.Thread") as thread:
            self.app._start_download()
        request = thread.call_args.kwargs["args"][0]
        self.assertEqual(request.output_name, "My video 100%")
        self.assertEqual(str(self.app.output_name_entry.cget("state")), "disabled")

    def test_invalid_name_remains_editable_and_does_not_download(self) -> None:
        self.app.output_name_var.set("../outside")
        with (
            patch("localwebvideodownloader.gui.threading.Thread") as thread,
            patch("localwebvideodownloader.gui.messagebox.showerror") as error,
        ):
            self.app._start_download()
        error.assert_called_once()
        thread.assert_not_called()
        self.assertIsNone(self.app.busy_mode)
        self.assertEqual(str(self.app.output_name_entry.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
