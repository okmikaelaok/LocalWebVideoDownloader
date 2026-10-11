using System.Text.Json;

namespace LocalWebVideoDownloader;

/// <summary>The native downloader view; all extraction and filesystem work stays in Python.</summary>
internal sealed class DownloadPanel : UserControl
{
    private readonly TextBox url = new() { Dock = DockStyle.Fill, PlaceholderText = "Paste a page or media URL" };
    private readonly Button inspect = new() { Text = "Inspect", AutoSize = true };
    private readonly Button openBrowser = new() { Text = "Open in browser", AutoSize = true };
    private readonly ComboBox items = Selector();
    private readonly ListBox formats = FormatList();
    private readonly ListBox audio = FormatList();
    private readonly ComboBox captions = Selector();
    private readonly CheckBox embed = new() { Text = "Embed selected captions (requires FFmpeg)", AutoSize = true };
    private readonly TextBox output = new() { Dock = DockStyle.Fill };
    private readonly TextBox filename = new() { Dock = DockStyle.Fill, MaxLength = 180, PlaceholderText = "Choose a filename; the extension is added automatically" };
    private readonly Button browse = new() { Text = "Browse...", AutoSize = true };
    private readonly Button download = new() { Text = "Download", AutoSize = true, Enabled = false };
    private readonly Button cancel = new() { Text = "Cancel", AutoSize = true, Enabled = false };
    private readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Maximum = 1000 };
    private readonly Label summary = new() { AutoSize = true, Text = "No video inspected yet.", MaximumSize = new Size(1000, 0) };
    private readonly Label status = new() { AutoSize = true, Text = "Paste a URL to begin, or open the Browser tab to discover loaded videos.", MaximumSize = new Size(1000, 0) };
    private readonly Label capabilities = new() { AutoSize = true, ForeColor = SystemColors.GrayText, Text = "Connecting to the download engine..." };
    private readonly List<JsonElement> mediaItems = [];
    private string? operation;
    private bool configured;
    private bool engineAvailable;
    private bool awaitingRequest;
    private bool downloadCompleted;
    public bool DownloadInProgress => operation == "download";
    public bool Busy => operation is not null || awaitingRequest;
    public event Action<string>? BrowserRequested;

    private sealed record Option<T>(string Label, T Value)
    {
        public override string ToString() => Label;
    }

    public DownloadPanel()
    {
        Dock = DockStyle.Fill;
        AutoScroll = true;
        Padding = new Padding(18);
        var layout = new TableLayoutPanel { Dock = DockStyle.Top, ColumnCount = 2, RowCount = 11, AutoSize = true };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 130));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (var row = 0; row < 11; row++) layout.RowStyles.Add(new RowStyle(row == 4 ? SizeType.Absolute : SizeType.AutoSize, row == 4 ? 270 : 0));
        var introduction = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Margin = new Padding(0, 0, 0, 16) };
        introduction.Controls.Add(new Label { AutoSize = true, Text = "LocalWebVideoDownloader", Font = new Font("Segoe UI", 19, FontStyle.Bold) });
        introduction.Controls.Add(new Label { AutoSize = true, Text = "Inspect a link, choose your quality, and save it locally. Browser capture is available when you need it." });
        layout.Controls.Add(introduction, 0, 0); layout.SetColumnSpan(introduction, 2);

        var urlActions = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, ColumnCount = 3 };
        urlActions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        urlActions.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        urlActions.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        urlActions.Controls.Add(url, 0, 0); urlActions.Controls.Add(inspect, 1, 0); urlActions.Controls.Add(openBrowser, 2, 0);
        AddRow(layout, 1, "Page or media URL", urlActions);
        AddRow(layout, 2, "Detected video", items);
        layout.Controls.Add(summary, 1, 3);
        var quality = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2 };
        quality.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        quality.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        quality.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        quality.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        quality.Controls.Add(new Label { Text = "Video quality", AutoSize = true, Font = new Font("Segoe UI", 11, FontStyle.Bold), Margin = new Padding(0, 4, 0, 8) }, 0, 0);
        quality.Controls.Add(new Label { Text = "Audio", AutoSize = true, Font = new Font("Segoe UI", 11, FontStyle.Bold), Margin = new Padding(8, 4, 0, 8) }, 1, 0);
        formats.Margin = new Padding(0, 0, 8, 0);
        audio.Margin = new Padding(8, 0, 0, 0);
        quality.Controls.Add(formats, 0, 1);
        quality.Controls.Add(audio, 1, 1);
        AddRow(layout, 4, "Video and audio", quality);
        var captionArea = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, ColumnCount = 1, RowCount = 2 };
        captionArea.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        captionArea.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        captionArea.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        captionArea.Controls.Add(captions, 0, 0); captionArea.Controls.Add(embed, 0, 1);
        AddRow(layout, 5, "Captions", captionArea);
        var filenameArea = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, RowCount = 2, ColumnCount = 1 };
        filenameArea.Controls.Add(filename, 0, 0);
        filenameArea.Controls.Add(new Label { Text = "Base name only; the media extension is automatic. Leave blank to use the original title and ID.", AutoSize = true, ForeColor = SystemColors.GrayText }, 0, 1);
        AddRow(layout, 6, "Save as", filenameArea);
        var folder = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, ColumnCount = 2 };
        folder.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); folder.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        folder.Controls.Add(output, 0, 0); folder.Controls.Add(browse, 1, 0);
        AddRow(layout, 7, "Output folder", folder);
        var progressArea = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, RowCount = 2, ColumnCount = 1, Margin = new Padding(0, 12, 0, 8) };
        progressArea.RowStyles.Add(new RowStyle(SizeType.Absolute, 23)); progressArea.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        progressArea.Controls.Add(progress, 0, 0); progressArea.Controls.Add(status, 0, 1);
        layout.Controls.Add(progressArea, 0, 8); layout.SetColumnSpan(progressArea, 2);
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.RightToLeft };
        buttons.Controls.Add(download); buttons.Controls.Add(cancel);
        layout.Controls.Add(buttons, 0, 9); layout.SetColumnSpan(buttons, 2);
        layout.Controls.Add(capabilities, 0, 10); layout.SetColumnSpan(capabilities, 2);
        Controls.Add(layout);

        inspect.Click += (_, _) => RequestInspection();
        url.KeyDown += (_, e) => { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; RequestInspection(); } };
        openBrowser.Click += (_, _) => BrowserRequested?.Invoke(url.Text.Trim());
        items.SelectedIndexChanged += (_, _) => ShowSelectedItem();
        formats.SelectedIndexChanged += (_, _) => UpdateEnabled();
        captions.SelectedIndexChanged += (_, _) => { if (captions.SelectedIndex <= 0) embed.Checked = false; UpdateEnabled(); };
        browse.Click += (_, _) => ChooseFolder();
        download.Click += (_, _) => RequestDownload();
        cancel.Click += (_, _) => CancelDownload();
        Resize += (_, _) => { summary.MaximumSize = new Size(Math.Max(300, Width - 190), 0); status.MaximumSize = new Size(Math.Max(300, Width - 40), 0); };
        UpdateEnabled();
    }

    private static ListBox FormatList() => new() { Dock = DockStyle.Fill, IntegralHeight = false, HorizontalScrollbar = true, Font = new Font("Segoe UI", 11), SelectionMode = SelectionMode.One };
    private static ComboBox Selector() => new() { Dock = DockStyle.Fill, DropDownStyle = ComboBoxStyle.DropDownList, DropDownWidth = 750 };
    private static void AddRow(TableLayoutPanel layout, int row, string label, Control control)
    {
        layout.Controls.Add(new Label { Text = label, AutoSize = true, Margin = new Padding(0, 7, 10, 7) }, 0, row);
        control.Margin = new Padding(0, 3, 0, 8);
        layout.Controls.Add(control, 1, row);
    }
    private static string TextValue(JsonElement value, string property, string fallback = "") => value.TryGetProperty(property, out var found) && found.ValueKind == JsonValueKind.String ? found.GetString() ?? fallback : fallback;
    private static bool Flag(JsonElement value, string property) => value.TryGetProperty(property, out var found) && found.ValueKind == JsonValueKind.True;
    private static IEnumerable<JsonElement> Array(JsonElement value, string property) => value.TryGetProperty(property, out var found) && found.ValueKind == JsonValueKind.Array ? found.EnumerateArray() : [];

    public void Receive(JsonElement message)
    {
        switch (TextValue(message, "command"))
        {
            case "init":
                output.Text = TextValue(message, "default_output_directory");
                configured = true;
                if (message.TryGetProperty("capabilities", out var support))
                {
                    engineAvailable = Flag(support, "yt_dlp_available");
                    capabilities.Text = $"Download engine: {(engineAvailable ? "ready" : "unavailable")}  |  FFmpeg: {(Flag(support, "ffmpeg_available") ? "available" : "not found")}  |  ffprobe: {(Flag(support, "ffprobe_available") ? "available" : "not found")}";
                }
                break;
            case "busy":
                operation = TextValue(message, "operation") is { Length: > 0 } next ? next : null;
                awaitingRequest = false;
                if (operation == "inspection") ClearInspection();
                var busyMessage = TextValue(message, "message");
                if (busyMessage.Length > 0 && !downloadCompleted) status.Text = busyMessage;
                if (operation is null)
                {
                    // Reassigning Style recreates the native progress control and can
                    // clear its visible fill even when Value still reports 1000.
                    if (progress.Style != ProgressBarStyle.Continuous) progress.Style = ProgressBarStyle.Continuous;
                }
                else { downloadCompleted = false; progress.Value = 0; progress.Style = ProgressBarStyle.Marquee; progress.MarqueeAnimationSpeed = 25; }
                break;
            case "inspection_complete":
                ClearInspection();
                mediaItems.AddRange(Array(message, "items").Take(1000).Select(item => item.Clone()));
                foreach (var item in mediaItems) items.Items.Add(TextValue(item, "title", "Untitled video"));
                if (items.Items.Count > 0) items.SelectedIndex = 0;
                status.Text = mediaItems.Count > 0 ? "Choose quality, captions, and a filename, then click Download." : "No downloadable videos were found. Try playing the page in the Browser tab.";
                progress.Style = ProgressBarStyle.Continuous; progress.Value = 0;
                break;
            case "progress":
                if (downloadCompleted) break;
                if (message.TryGetProperty("fraction", out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var fraction) && double.IsFinite(fraction))
                { progress.Style = ProgressBarStyle.Continuous; progress.Value = (int)(Math.Clamp(fraction, 0, 1) * 1000); }
                else { progress.Style = ProgressBarStyle.Marquee; progress.MarqueeAnimationSpeed = 25; }
                status.Text = ProgressText(message);
                break;
            case "download_complete":
                downloadCompleted = true;
                progress.Style = ProgressBarStyle.Continuous; progress.Value = 1000;
                var files = Array(message, "files").Where(file => file.ValueKind == JsonValueKind.String).Select(file => file.GetString()).ToArray();
                var existing = Array(message, "existing_files").Count();
                status.Text = files.Length == 0 ? "Completed." : $"Completed — saved {files.Length} file(s): {string.Join("; ", files.Take(3))}";
                if (existing > 0) status.Text += $" ({existing} existing file(s) reused.)";
                if (message.TryGetProperty("validated", out var validated) && validated.ValueKind == JsonValueKind.Null) status.Text += " Output validation was unavailable.";
                break;
            case "error":
                downloadCompleted = false;
                status.Text = TextValue(message, "message", "The operation could not be completed.");
                progress.Style = ProgressBarStyle.Continuous; progress.Value = 0;
                if (TextValue(message, "operation") == "inspection") ClearInspection();
                break;
        }
        UpdateEnabled();
    }

    private static string ProgressText(JsonElement message)
    {
        var text = TextValue(message, "message");
        if (text.Length == 0) text = TextValue(message, "phase", "Downloading");
        if (message.TryGetProperty("fraction", out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var fraction) && double.IsFinite(fraction)) text += $" — {Math.Clamp(fraction, 0, 1):P0}";
        if (message.TryGetProperty("speed_bytes_per_second", out var speed) && speed.ValueKind == JsonValueKind.Number && speed.TryGetDouble(out var bytes) && bytes > 0) text += $" — {bytes / (1024 * 1024):0.0} MiB/s";
        if (message.TryGetProperty("eta_seconds", out var eta) && eta.ValueKind == JsonValueKind.Number && eta.TryGetDouble(out var seconds) && seconds >= 0) text += $" — {seconds:0}s remaining";
        return text;
    }

    private void ClearInspection()
    {
        downloadCompleted = false;
        mediaItems.Clear(); items.Items.Clear(); formats.Items.Clear(); audio.Items.Clear(); captions.Items.Clear();
        embed.Checked = false;
        filename.Clear(); summary.Text = "No video inspected yet.";
    }

    private void ShowSelectedItem()
    {
        formats.Items.Clear(); audio.Items.Clear(); captions.Items.Clear();
        if (items.SelectedIndex < 0 || items.SelectedIndex >= mediaItems.Count) { UpdateEnabled(); return; }
        var item = mediaItems[items.SelectedIndex];
        formats.Items.Add(new Option<string>("Best available (automatic)", "best"));
        audio.Items.Add(new Option<string?>("Automatic / included audio", null));
        foreach (var format in Array(item, "formats"))
        {
            if (Flag(format, "has_drm")) continue;
            var kind = TextValue(format, "kind");
            var id = TextValue(format, "format_id");
            var label = string.Join(" | ", new[] { TextValue(format, "resolution"), kind, TextValue(format, "extension"), TextValue(format, "language"), id }.Where(part => part.Length > 0));
            if (kind is "video only" or "video + audio") formats.Items.Add(new Option<string>(label, id));
            else if (kind == "audio only") audio.Items.Add(new Option<string?>(label, id));
        }
        embed.Checked = false;
        captions.Items.Add(new Option<int>("No captions", -1));
        var index = 0;
        foreach (var caption in Array(item, "captions"))
        {
            var label = string.Join(" | ", new[] { TextValue(caption, "language"), TextValue(caption, "name"), TextValue(caption, "extension"), Flag(caption, "automatic") ? "automatic" : "manual" }.Where(part => part.Length > 0));
            captions.Items.Add(new Option<int>(label, index++));
        }
        formats.SelectedIndex = 0; audio.SelectedIndex = 0; captions.SelectedIndex = 0;
        filename.Text = TextValue(item, "suggested_name");
        summary.Text = $"{TextValue(item, "extractor", "Media")}  •  {Math.Max(0, formats.Items.Count - 1)} video format(s)  •  {Math.Max(0, captions.Items.Count - 1)} caption track(s)";
        if (Flag(item, "has_drm")) summary.Text += "  •  DRM-protected media is not supported.";
        if (Flag(item, "is_live")) summary.Text += "  •  Live streams are not supported.";
        UpdateEnabled();
    }

    private void UpdateEnabled()
    {
        var idle = !Busy && configured && engineAvailable;
        inspect.Enabled = idle; url.Enabled = !Busy;
        items.Enabled = idle && mediaItems.Count > 0;
        formats.Enabled = idle && formats.Items.Count > 0; audio.Enabled = idle && audio.Items.Count > 0;
        captions.Enabled = idle && captions.Items.Count > 1; embed.Enabled = idle && captions.SelectedIndex > 0;
        filename.Enabled = idle; output.Enabled = idle; browse.Enabled = idle;
        var selected = items.SelectedIndex >= 0 && items.SelectedIndex < mediaItems.Count;
        download.Enabled = idle && selected && !Flag(mediaItems[items.SelectedIndex], "has_drm") && !Flag(mediaItems[items.SelectedIndex], "is_live");
        cancel.Enabled = operation == "download";
    }

    private void RequestInspection()
    {
        if (!inspect.Enabled) return;
        var target = MediaCatalog.NormalizeUrl(url.Text);
        if (target is null) { status.Text = "Enter a complete http:// or https:// page or media URL."; return; }
        BeginInspection(target);
        Program.Send(new { version = 1, @event = "inspect", url = target });
    }

    private void BeginInspection(string target)
    {
        url.Text = target; awaitingRequest = true; ClearInspection();
        status.Text = "Inspecting media..."; progress.Style = ProgressBarStyle.Marquee;
        UpdateEnabled();
    }

    public void InspectCapture(MediaCandidate source)
    {
        url.Text = source.Url;
        if (!Busy) BeginInspection(source.Url);
        else status.Text = "Captured source queued. It will be inspected after the current operation finishes.";
        Program.Send(new { version = 1, @event = "inspect", url = source.Url, capture = new { url = source.Url, kind = source.Kind, page_url = source.PageUrl, frame_url = source.FrameUrl, content_type = source.ContentType } });
    }

    private void RequestDownload()
    {
        if (!download.Enabled || formats.SelectedItem is not Option<string> format) return;
        if (string.IsNullOrWhiteSpace(output.Text)) { status.Text = "Choose an output folder first."; return; }
        downloadCompleted = false; progress.Value = 0;
        awaitingRequest = true; status.Text = "Preparing download..."; UpdateEnabled();
        Program.Send(new
        {
            version = 1, @event = "download", item_index = items.SelectedIndex, format_id = format.Value,
            audio_format_id = (audio.SelectedItem as Option<string?>)?.Value,
            caption_indices = captions.SelectedItem is Option<int> caption && caption.Value >= 0 ? new[] { caption.Value } : System.Array.Empty<int>(),
            embed_captions = embed.Checked, output_directory = output.Text.Trim(),
            output_name = string.IsNullOrEmpty(filename.Text) ? null : filename.Text
        });
    }

    public void CancelDownload()
    {
        Program.Send(new { version = 1, @event = "cancel" });
        cancel.Enabled = false; status.Text = "Cancelling download...";
    }

    private void ChooseFolder()
    {
        using var dialog = new FolderBrowserDialog { Description = "Choose where downloads should be saved", UseDescriptionForTitle = true };
        if (Directory.Exists(output.Text)) dialog.InitialDirectory = output.Text;
        if (dialog.ShowDialog(this) == DialogResult.OK) output.Text = dialog.SelectedPath;
    }

    internal void SetInitialUrl(string? initialUrl) { if (initialUrl is not null) url.Text = initialUrl; }

    internal void StartIntegrationInspection()
    {
        url.Text = "https://1.1.1.1/native-desktop-fixture";
        RequestInspection();
    }

    internal void StartIntegrationDownload()
    {
        // The parent sends busy:null after inspection_complete. Wait until that
        // queued command has enabled the controls before emulating the click.
        if (Busy)
        {
            var wait = new System.Windows.Forms.Timer { Interval = 25 };
            wait.Tick += (_, _) => { if (!Busy) { wait.Stop(); wait.Dispose(); StartIntegrationDownload(); } };
            wait.Start();
            return;
        }
        filename.Text = "Renamed 100% (native)";
        if (captions.Items.Count > 1) captions.SelectedIndex = 1;
        RequestDownload();
    }

    internal void VerifySmokeState()
    {
        Receive(JsonSerializer.SerializeToElement(new { command = "init", default_output_directory = "C:\\Downloads", capabilities = new { yt_dlp_available = true, ffmpeg_available = true, ffprobe_available = true } }));
        Receive(JsonSerializer.SerializeToElement(new
        {
            command = "inspection_complete",
            items = new[]
            {
                new
                {
                    title = "Fixture video", suggested_name = "Renamable fixture", extractor = "fixture", has_drm = false, is_live = false,
                    formats = new[] { new { format_id = "18", resolution = "720p", kind = "video + audio", extension = "mp4", has_drm = false } },
                    captions = new[] { new { language = "en", name = "English", extension = "vtt", automatic = false } }
                }
            }
        }));
        if (items.Items.Count != 1 || formats.Items.Count != 2 || captions.Items.Count != 2 || filename.Text != "Renamable fixture" || !download.Enabled) throw new InvalidOperationException("Download controls were not populated.");
        filename.Text = "My renamed video";
        captions.SelectedIndex = 1;
        RequestDownload();
        Receive(JsonSerializer.SerializeToElement(new { command = "busy", operation = "download", message = "Downloading fixture" }));
        if (!DownloadInProgress || download.Enabled || !cancel.Enabled) throw new InvalidOperationException("Busy state failed.");
        Receive(JsonSerializer.SerializeToElement(new { command = "progress", fraction = 0.5, speed_bytes_per_second = (double?)null, eta_seconds = (double?)null, phase = "downloading", message = (string?)null }));
        if (progress.Value != 500) throw new InvalidOperationException("Progress state failed.");
        Receive(JsonSerializer.SerializeToElement(new { command = "download_complete", files = new[] { "C:\\Downloads\\My renamed video.mp4" }, existing_files = System.Array.Empty<string>(), validated = true }));
        Receive(JsonSerializer.SerializeToElement(new { command = "busy", operation = (string?)null, message = "" }));
        Receive(JsonSerializer.SerializeToElement(new { command = "progress", fraction = (double?)null, phase = "finished" }));
        if (!download.Enabled || progress.Value != 1000 || progress.Style != ProgressBarStyle.Continuous || !status.Text.StartsWith("Completed")) throw new InvalidOperationException("Completion state failed.");
    }
}
