"""Legacy Tkinter desktop interface for LocalWebVideoDownloader."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from localwebvideodownloader.browser_capture import CapturedSource
from localwebvideodownloader.errors import AppError, InspectionError
from localwebvideodownloader.filenames import suggest_output_name, validate_output_name
from localwebvideodownloader.models import (
    CaptionTrack,
    DownloadRequest,
    DownloadResult,
    InspectionResult,
    MediaItem,
    ProgressEvent,
)
from localwebvideodownloader.security import redact_sensitive_text
from localwebvideodownloader.webview_browser import BrowserSession
from localwebvideodownloader.ytdlp_backend import YtDlpBackend, get_capabilities


def _human_bytes(value: float | int | None) -> str:
    if value is None or value < 0:
        return "-"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "-"


def _duration(value: float | None) -> str:
    if value is None:
        return "unknown"
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class DownloaderWindow:
    POLL_INTERVAL_MS = 100

    def __init__(
        self,
        root: tk.Tk,
        *,
        backend: YtDlpBackend | None = None,
        initial_url: str | None = None,
        auto_inspect: bool = False,
        open_browser: bool = False,
    ) -> None:
        self.root = root
        self.root.title("LocalWebVideoDownloader")
        self.root.geometry("1080x760")
        self.root.minsize(860, 620)

        self.backend = backend or YtDlpBackend()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.inspection: InspectionResult | None = None
        self.cancel_event: threading.Event | None = None
        self.busy_mode: str | None = None
        self.progress_indeterminate = False
        self.format_rows: dict[str, str] = {}
        self.audio_ids: list[str | None] = []
        self.caption_rows: list[CaptionTrack] = []
        self.browser_session: BrowserSession | None = None
        self.capture_context: CapturedSource | None = None
        self.pending_capture: CapturedSource | None = None

        default_output = Path(__file__).resolve().parents[2] / "VideosDownloaded"

        self.url_var = tk.StringVar()
        self.media_var = tk.StringVar()
        self.audio_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(default_output))
        self.output_name_var = tk.StringVar()
        self.embed_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Paste a page or media URL to begin.")
        self.summary_var = tk.StringVar(value="No video inspected yet.")
        self.browser_status_var = tk.StringVar(value="Capture browser: closed")

        self._build_ui()
        self._show_capabilities()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)
        if initial_url:
            self.url_var.set(initial_url)
            self.status_var.set("Browser capture received. Inspecting the selected media source...")
            if auto_inspect:
                self.root.after(150, self._start_inspection)
        if open_browser:
            self.root.after(200, self._open_capture_browser)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        heading = ttk.Label(outer, text="LocalWebVideoDownloader", font=("Segoe UI", 17, "bold"))
        heading.grid(row=0, column=0, sticky="w", pady=(0, 10))

        url_frame = ttk.Frame(outer)
        url_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        url_frame.columnconfigure(1, weight=1)
        ttk.Label(url_frame, text="Page or media URL:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _event: self._start_inspection())
        self.inspect_button = ttk.Button(url_frame, text="Inspect", command=self._start_inspection)
        self.inspect_button.grid(row=0, column=2)
        self.capture_button = ttk.Button(
            url_frame, text="Open capture browser", command=self._open_capture_browser
        )
        self.capture_button.grid(row=0, column=3, padx=(8, 0))

        media_frame = ttk.Frame(outer)
        media_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        media_frame.columnconfigure(1, weight=1)
        ttk.Label(media_frame, text="Detected video:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.media_combo = ttk.Combobox(
            media_frame, textvariable=self.media_var, state="readonly", values=()
        )
        self.media_combo.grid(row=0, column=1, sticky="ew")
        self.media_combo.bind("<<ComboboxSelected>>", lambda _event: self._show_selected_item())

        content = ttk.Panedwindow(outer, orient=tk.VERTICAL)
        content.grid(row=3, column=0, sticky="nsew")

        format_frame = ttk.LabelFrame(content, text="Video quality")
        format_frame.columnconfigure(0, weight=1)
        format_frame.rowconfigure(0, weight=1)
        content.add(format_frame, weight=3)

        columns = ("resolution", "kind", "container", "fps", "bitrate", "codecs", "size")
        self.format_tree = ttk.Treeview(
            format_frame, columns=columns, show="tree headings", selectmode="browse", height=11
        )
        self.format_tree.heading("#0", text="Format ID")
        self.format_tree.column("#0", width=150, minwidth=110, stretch=False)
        headings = {
            "resolution": ("Resolution", 105),
            "kind": ("Streams", 120),
            "container": ("Type", 60),
            "fps": ("FPS", 55),
            "bitrate": ("Bitrate", 75),
            "codecs": ("Codecs", 210),
            "size": ("Size", 90),
        }
        for name, (label, width) in headings.items():
            self.format_tree.heading(name, text=label)
            self.format_tree.column(name, width=width, minwidth=50)
        self.format_tree.tag_configure("protected", foreground="#777777")
        format_scroll = ttk.Scrollbar(
            format_frame, orient=tk.VERTICAL, command=self.format_tree.yview
        )
        self.format_tree.configure(yscrollcommand=format_scroll.set)
        self.format_tree.grid(row=0, column=0, sticky="nsew")
        format_scroll.grid(row=0, column=1, sticky="ns")

        options_frame = ttk.LabelFrame(content, text="Audio and captions", padding=9)
        options_frame.columnconfigure(1, weight=1)
        options_frame.rowconfigure(1, weight=1)
        content.add(options_frame, weight=2)

        ttk.Label(options_frame, text="Audio track:").grid(
            row=0, column=0, sticky="nw", padx=(0, 8), pady=(0, 8)
        )
        self.audio_combo = ttk.Combobox(
            options_frame, textvariable=self.audio_var, state="readonly", values=()
        )
        self.audio_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(options_frame, text="Captions:").grid(row=1, column=0, sticky="nw", padx=(0, 8))
        caption_container = ttk.Frame(options_frame)
        caption_container.grid(row=1, column=1, sticky="nsew")
        caption_container.columnconfigure(0, weight=1)
        caption_container.rowconfigure(0, weight=1)
        self.caption_list = tk.Listbox(
            caption_container,
            selectmode=tk.EXTENDED,
            exportselection=False,
            height=5,
            activestyle="dotbox",
        )
        caption_scroll = ttk.Scrollbar(
            caption_container, orient=tk.VERTICAL, command=self.caption_list.yview
        )
        self.caption_list.configure(yscrollcommand=caption_scroll.set)
        self.caption_list.grid(row=0, column=0, sticky="nsew")
        caption_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Checkbutton(
            options_frame,
            text="Embed selected captions into the video (requires FFmpeg)",
            variable=self.embed_var,
        ).grid(row=2, column=1, sticky="w", pady=(7, 0))

        output_frame = ttk.Frame(outer)
        output_frame.grid(row=4, column=0, sticky="ew", pady=(10, 8))
        output_frame.columnconfigure(1, weight=1)
        ttk.Label(output_frame, text="Save to:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.output_entry = ttk.Entry(output_frame, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.browse_button = ttk.Button(output_frame, text="Browse...", command=self._browse_output)
        self.browse_button.grid(row=0, column=2)
        ttk.Label(output_frame, text="File name:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(7, 0)
        )
        self.output_name_entry = ttk.Entry(output_frame, textvariable=self.output_name_var)
        self.output_name_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(
            output_frame,
            text="Extension added automatically. Leave blank to use the original title and ID.",
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(3, 0))

        action_frame = ttk.Frame(outer)
        action_frame.grid(row=5, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(action_frame, mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.download_button = ttk.Button(
            action_frame, text="Download", command=self._start_download, state=tk.DISABLED
        )
        self.download_button.grid(row=0, column=1, padx=(0, 6))
        self.cancel_button = ttk.Button(
            action_frame, text="Cancel", command=self._cancel_download, state=tk.DISABLED
        )
        self.cancel_button.grid(row=0, column=2)

        ttk.Label(outer, textvariable=self.status_var, wraplength=1000).grid(
            row=6, column=0, sticky="w", pady=(8, 1)
        )
        ttk.Label(outer, textvariable=self.summary_var, foreground="#555555", wraplength=1000).grid(
            row=7, column=0, sticky="w"
        )
        ttk.Label(outer, textvariable=self.browser_status_var, wraplength=1000).grid(
            row=8, column=0, sticky="w", pady=(4, 0)
        )

    def _open_capture_browser(self) -> None:
        if self.browser_session is not None:
            return
        self.browser_session = BrowserSession(
            lambda kind, value: self.events.put((f"browser_{kind}", value))
        )
        self.capture_button.configure(state=tk.DISABLED)
        self.browser_status_var.set("Capture browser: starting...")
        threading.Thread(
            target=self.browser_session.run,
            args=(self.url_var.get().strip() or None,),
            daemon=True,
        ).start()

    def _accept_capture(self, capture: CapturedSource) -> None:
        self.capture_context = capture
        self.url_var.set(capture.url)
        self.browser_status_var.set(
            "Capture browser: source selected; inspecting its qualities and captions."
        )
        self.root.deiconify()
        self.root.lift()
        self._start_inspection()

    def _show_capabilities(self) -> None:
        capabilities = get_capabilities()
        if not capabilities.yt_dlp_available:
            self.status_var.set(
                "yt-dlp is not installed yet. Follow the setup commands in README.md "
                "before inspecting."
            )
        elif not capabilities.ffmpeg_path:
            self.status_var.set(
                "Ready. FFmpeg is not on PATH; pre-muxed downloads may work, but merging will not."
            )
        else:
            self.status_var.set("Ready. Paste a public page or media URL to begin.")

    def _set_busy(self, mode: str | None) -> None:
        self.busy_mode = mode
        busy = mode is not None
        self.inspect_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.url_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.browse_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.output_name_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        can_download = not busy and self.inspection is not None
        self.download_button.configure(state=tk.NORMAL if can_download else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if mode == "download" else tk.DISABLED)

    def _start_inspection(self) -> None:
        if self.busy_mode:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo(
                "URL required", "Enter a page or media URL first.", parent=self.root
            )
            return
        self.inspection = None
        self._clear_item_controls()
        self._set_busy("inspection")
        self.status_var.set("Inspecting the page and available media metadata...")
        self.summary_var.set("This can take a few seconds depending on the player.")
        capture = (
            self.capture_context
            if self.capture_context and self.capture_context.url == url
            else None
        )
        threading.Thread(target=self._inspection_worker, args=(url, capture), daemon=True).start()

    def _inspection_worker(self, url: str, capture: CapturedSource | None = None) -> None:
        try:
            backend = self.backend
            if capture is not None:
                backend = YtDlpBackend(referer_url=capture.referer_url)
            elif self.capture_context is not None:
                backend = YtDlpBackend()
            result = backend.inspect(url)
        except AppError as exc:
            self.events.put(("inspection_error", exc))
        except Exception as exc:  # defensive boundary around a third-party extractor
            self.events.put(
                (
                    "inspection_error",
                    InspectionError(
                        "An unexpected error occurred while inspecting the page.",
                        diagnostic=redact_sensitive_text(exc),
                    ),
                )
            )
        else:
            self.events.put(("inspection_complete", (result, backend)))

    def _handle_inspection_complete(self, result: InspectionResult) -> None:
        self.inspection = result
        values = [f"{index}. {item.title}" for index, item in enumerate(result.items, start=1)]
        self.media_combo.configure(values=values)
        self.media_combo.current(0)
        self._show_selected_item()
        self._set_busy(None)
        self.status_var.set(f"Found {len(result.items)} video item(s). Choose a quality below.")

    def _selected_item(self) -> MediaItem | None:
        if self.inspection is None:
            return None
        index = self.media_combo.current()
        if index < 0 or index >= len(self.inspection.items):
            return None
        return self.inspection.items[index]

    def _show_selected_item(self) -> None:
        item = self._selected_item()
        self._clear_format_options()
        if item is None:
            return

        self.output_name_var.set(suggest_output_name(item.title))

        self.format_rows["best"] = "best"
        self.format_tree.insert(
            "",
            tk.END,
            iid="best",
            text="best",
            values=("Best available", "automatic", "-", "-", "-", "-", "-"),
        )
        for row_number, media_format in enumerate(item.video_formats, start=1):
            row_id = f"format-{row_number}"
            self.format_rows[row_id] = media_format.format_id
            bitrate = (
                f"{media_format.bitrate_kbps:.0f} kbps"
                if media_format.bitrate_kbps is not None
                else "-"
            )
            fps = f"{media_format.fps:g}" if media_format.fps is not None else "-"
            tags = ("protected",) if media_format.has_drm else ()
            self.format_tree.insert(
                "",
                tk.END,
                iid=row_id,
                text=media_format.format_id,
                values=(
                    media_format.resolution,
                    media_format.kind,
                    media_format.extension or "-",
                    fps,
                    bitrate,
                    media_format.codec_summary,
                    _human_bytes(media_format.filesize),
                ),
                tags=tags,
            )
        self.format_tree.selection_set("best")
        self.format_tree.focus("best")

        audio_values = ["Automatic (best matching audio)"]
        self.audio_ids = [None]
        for media_format in item.audio_formats:
            suffix = " [DRM]" if media_format.has_drm else ""
            audio_values.append(f"{media_format.label}{suffix}")
            self.audio_ids.append(media_format.format_id)
        self.audio_combo.configure(values=audio_values)
        self.audio_combo.current(0)

        self.caption_rows = list(item.captions)
        if self.caption_rows:
            for track in self.caption_rows:
                self.caption_list.insert(tk.END, track.label)
        else:
            self.caption_list.insert(tk.END, "No captions advertised")

        flags = []
        if item.is_live:
            flags.append("live streams are not supported")
        if item.has_drm:
            flags.append("DRM-protected media is not supported")
        warning = f" Warning: {'; '.join(flags)}." if flags else ""
        self.summary_var.set(
            f"{item.title} | duration {_duration(item.duration_seconds)} | "
            f"{len(item.formats)} formats | {len(item.captions)} caption tracks.{warning}"
        )

    def _clear_item_controls(self) -> None:
        self.media_combo.configure(values=())
        self.media_var.set("")
        self.output_name_var.set("")
        self._clear_format_options()
        self.download_button.configure(state=tk.DISABLED)

    def _clear_format_options(self) -> None:
        for row in self.format_tree.get_children():
            self.format_tree.delete(row)
        self.format_rows.clear()
        self.audio_combo.configure(values=())
        self.audio_var.set("")
        self.audio_ids.clear()
        self.caption_list.delete(0, tk.END)
        self.caption_rows.clear()

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose download directory",
            initialdir=self.output_var.get() or str(Path.home()),
            mustexist=False,
            parent=self.root,
        )
        if selected:
            self.output_var.set(selected)

    def _start_download(self) -> None:
        if self.busy_mode:
            return
        item = self._selected_item()
        if item is None:
            messagebox.showinfo(
                "Inspect first", "Inspect a page and select a video first.", parent=self.root
            )
            return
        selected_rows = self.format_tree.selection()
        if not selected_rows:
            messagebox.showinfo("Quality required", "Select a video quality.", parent=self.root)
            return
        format_id = self.format_rows.get(selected_rows[0])
        if not format_id:
            messagebox.showerror(
                "Invalid selection", "Select a listed video quality.", parent=self.root
            )
            return

        audio_index = self.audio_combo.current()
        audio_format_id = (
            self.audio_ids[audio_index] if 0 <= audio_index < len(self.audio_ids) else None
        )
        captions = tuple(
            self.caption_rows[index]
            for index in self.caption_list.curselection()
            if index < len(self.caption_rows)
        )
        caption_languages = [track.language.casefold() for track in captions]
        if len(caption_languages) != len(set(caption_languages)):
            messagebox.showinfo(
                "Choose one caption variant",
                "Choose only one caption format or type for each language.",
                parent=self.root,
            )
            return
        if self.embed_var.get() and not captions:
            messagebox.showinfo(
                "Captions required",
                "Select at least one caption track before enabling caption embedding.",
                parent=self.root,
            )
            return
        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showinfo("Folder required", "Choose an output folder.", parent=self.root)
            return
        try:
            output_name = validate_output_name(self.output_name_var.get())
        except AppError as exc:
            messagebox.showerror("Invalid file name", exc.message, parent=self.root)
            self.output_name_entry.focus_set()
            return

        request = DownloadRequest(
            item=item,
            output_directory=Path(output_text),
            format_id=format_id,
            audio_format_id=audio_format_id,
            captions=captions,
            embed_captions=self.embed_var.get(),
            output_name=output_name,
        )
        self.cancel_event = threading.Event()
        self.progress_bar.configure(mode="indeterminate", value=0)
        self.progress_bar.start(12)
        self.progress_indeterminate = True
        self._set_busy("download")
        self.status_var.set(f"Starting download: {item.title}")
        threading.Thread(target=self._download_worker, args=(request,), daemon=True).start()

    def _download_worker(self, request: DownloadRequest) -> None:
        try:
            result = self.backend.download(
                request,
                progress=lambda event: self.events.put(("progress", event)),
                cancel_event=self.cancel_event,
            )
        except AppError as exc:
            self.events.put(("download_error", exc))
        except Exception as exc:  # defensive boundary around downloader/postprocessors
            self.events.put(
                (
                    "download_error",
                    InspectionError(
                        "An unexpected error occurred while downloading the video.",
                        diagnostic=redact_sensitive_text(exc),
                    ),
                )
            )
        else:
            self.events.put(("download_complete", result))

    def _cancel_download(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.cancel_button.configure(state=tk.DISABLED)
            self.status_var.set("Cancelling at the next safe download checkpoint...")

    def _handle_progress(self, event: ProgressEvent) -> None:
        if event.phase == "downloading":
            if event.fraction is not None:
                if self.progress_indeterminate:
                    self.progress_bar.stop()
                    self.progress_bar.configure(mode="determinate")
                    self.progress_indeterminate = False
                self.progress_bar["value"] = event.fraction * 100
                status = f"Downloading: {event.fraction * 100:.1f}%"
            else:
                status = f"Downloading: {_human_bytes(event.downloaded_bytes)}"
            if event.speed_bytes_per_second:
                status += f" at {_human_bytes(event.speed_bytes_per_second)}/s"
            if event.eta_seconds is not None:
                status += f" - about {int(event.eta_seconds)} seconds remaining"
            self.status_var.set(status)
        elif event.phase == "processing":
            self.status_var.set(event.message or "Processing downloaded media...")
        elif event.message:
            self.status_var.set(event.message)

    def _finish_progress(self) -> None:
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0)
        self.progress_indeterminate = False
        self.cancel_event = None
        self._set_busy(None)

    def _handle_download_complete(self, result: DownloadResult) -> None:
        self._finish_progress()
        if result.files:
            names = "\n".join(
                f"{path}"
                f"{' (already existed; not overwritten)' if path in result.existing_files else ''}"
                for path in result.files
            )
            self.status_var.set(f"Download complete: {result.files[0].name}")
            validation = (
                "\n\nffprobe found the expected media stream."
                if result.validated is True
                else "\n\nMedia validation was skipped."
            )
            messagebox.showinfo(
                "Download complete", f"Saved:\n{names}{validation}", parent=self.root
            )
        else:
            self.status_var.set("Download complete.")
            messagebox.showinfo(
                "Download complete",
                "yt-dlp completed without reporting a newly created filename.",
                parent=self.root,
            )

    def _handle_error(self, error: AppError) -> None:
        self._finish_progress()
        self.status_var.set(error.message)
        messagebox.showerror("Operation failed", error.message, parent=self.root)

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.events.get_nowait()
                if event_type == "inspection_complete":
                    result, self.backend = payload
                    self._handle_inspection_complete(result)
                elif event_type == "inspection_error":
                    self.inspection = None
                    self._set_busy(None)
                    self.status_var.set(payload.message)
                    messagebox.showerror("Inspection failed", payload.message, parent=self.root)
                elif event_type == "progress":
                    self._handle_progress(payload)
                elif event_type == "download_complete":
                    self._handle_download_complete(payload)
                elif event_type == "download_error":
                    self._handle_error(payload)
                elif event_type == "browser_ready":
                    self.browser_status_var.set(
                        "Capture browser: ready. Open a page, play its video, "
                        "and inspect a detected source."
                    )
                elif event_type == "browser_capture":
                    self.pending_capture = payload
                    if self.busy_mode:
                        self.browser_status_var.set(
                            "Capture browser: selection waiting for the current "
                            "operation to finish."
                        )
                elif event_type == "browser_error":
                    self.browser_status_var.set(payload.message)
                    messagebox.showerror("Capture browser", payload.message, parent=self.root)
                elif event_type == "browser_closed":
                    self.browser_session = None
                    self.capture_button.configure(state=tk.NORMAL)
                    self.browser_status_var.set(
                        "Capture browser: closed. You can reopen it at any time."
                    )
        except queue.Empty:
            pass
        if self.pending_capture is not None and not self.busy_mode:
            capture, self.pending_capture = self.pending_capture, None
            self._accept_capture(capture)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)

    def _on_close(self) -> None:
        if self.busy_mode == "download":
            close = messagebox.askyesno(
                "Download in progress",
                "Cancel the download and close? A resumable .part file may remain.",
                parent=self.root,
            )
            if not close:
                return
            if self.cancel_event is not None:
                self.cancel_event.set()
        if self.browser_session is not None:
            self.browser_session.close()
        self.root.destroy()


def main(
    *,
    initial_url: str | None = None,
    referer_url: str | None = None,
    auto_inspect: bool = False,
    open_browser: bool = False,
) -> int:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    backend = YtDlpBackend(referer_url=referer_url)
    DownloaderWindow(
        root,
        backend=backend,
        initial_url=initial_url,
        auto_inspect=auto_inspect,
        open_browser=open_browser,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
