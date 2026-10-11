using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LocalWebVideoDownloader;

internal sealed class BrowserForm : Form
{
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill };
    private readonly TextBox address = new() { Dock = DockStyle.Fill, PlaceholderText = "Paste a page URL, then press Open and play the video" };
    private readonly ListView sources = new() { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false };
    private readonly Label status = new() { Dock = DockStyle.Fill, AutoEllipsis = true, TextAlign = ContentAlignment.MiddleLeft };
    private readonly Button detection = new() { Text = "Start detection", AutoSize = true, Enabled = false };
    private readonly Button inspect = new() { Text = "Inspect selected", AutoSize = true, Enabled = false };
    private readonly MediaCatalog catalog = new();
    private sealed record RequestObservation(int Generation, string PageUrl, string? DocumentUrl, bool Observed = false);
    private readonly Dictionary<string, RequestObservation> documents = [];
    private readonly System.Windows.Forms.Timer scanTimer = new() { Interval = 2000 };
    private readonly System.Windows.Forms.Timer smokeTimeout = new() { Interval = 20000 };
    private readonly string? initialUrl;
    private readonly bool smoke;
    private readonly bool desktop;
    private readonly bool integrationSmoke;
    private readonly TabControl? tabs;
    private readonly TabPage? browserTab;
    private readonly TabPage? downloadTab;
    private readonly DownloadPanel? downloader;
    private Task? initialization;
    private bool parentClosing;
    private bool enabled;
    private bool scanning;
    private bool ready;
    private bool integrationDownloadComplete;
    private int generation;
    private string pageUrl = "";
    public int ExitCode { get; private set; }

    public BrowserForm(string? initialUrl, bool smoke, bool desktop = false, bool desktopSmoke = false, bool integrationSmoke = false)
    {
        this.initialUrl = initialUrl;
        this.smoke = smoke;
        this.desktop = desktop;
        this.integrationSmoke = integrationSmoke;
        Text = desktop ? "LocalWebVideoDownloader" : "LocalWebVideoDownloader - Capture Browser";
        Font = new Font("Segoe UI", 10);
        Width = 1280; Height = 840; MinimumSize = desktop ? new Size(1020, 760) : new Size(940, 650);
        StartPosition = FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 4, ColumnCount = 1, Padding = new Padding(10) };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
        var navigation = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4 };
        navigation.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 65));
        navigation.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 70));
        navigation.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        navigation.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 80));
        navigation.Controls.Add(MakeButton("Back", () => { if (ready && browser.CanGoBack) browser.GoBack(); }), 0, 0);
        navigation.Controls.Add(MakeButton("Reload", () => { if (ready) browser.Reload(); }), 1, 0);
        navigation.Controls.Add(address, 2, 0);
        navigation.Controls.Add(MakeButton("Open", Navigate), 3, 0);
        address.KeyDown += (_, e) => { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; Navigate(); } };
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill };
        detection.Click += (_, _) => SetDetection(!enabled);
        inspect.Click += (_, _) => InspectSelected();
        actions.Controls.Add(detection);
        actions.Controls.Add(MakeButton("Clear sources", ClearSources));
        actions.Controls.Add(MakeButton("Scan page", () => { if (enabled) _ = ScanPage(); }));
        actions.Controls.Add(inspect);
        actions.Controls.Add(MakeButton("Inspect page URL", InspectPage));
        var panes = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal };
        panes.Panel1.Controls.Add(browser);
        sources.Columns.Add("Type", 120);
        sources.Columns.Add("Source (signed paths and queries hidden)", 530);
        sources.Columns.Add("Found via", 90);
        sources.Columns.Add("Content type", 230);
        sources.SelectedIndexChanged += (_, _) => inspect.Enabled = sources.SelectedItems.Count == 1;
        sources.DoubleClick += (_, _) => InspectSelected();
        panes.Panel2.Controls.Add(sources);
        layout.Controls.Add(navigation, 0, 0);
        layout.Controls.Add(actions, 0, 1);
        layout.Controls.Add(panes, 0, 2);
        layout.Controls.Add(status, 0, 3);
        if (desktop)
        {
            tabs = new TabControl { Dock = DockStyle.Fill, Padding = new Point(16, 7) };
            downloadTab = new TabPage("Download");
            browserTab = new TabPage("Browser");
            downloader = new DownloadPanel();
            downloader.SetInitialUrl(initialUrl);
            downloader.BrowserRequested += async target =>
            {
                if (!string.IsNullOrWhiteSpace(target)) address.Text = target;
                tabs.SelectedTab = browserTab;
                await EnsureBrowser();
                if (!string.IsNullOrWhiteSpace(target)) Navigate();
            };
            downloadTab.Controls.Add(downloader);
            browserTab.Controls.Add(layout);
            tabs.TabPages.Add(downloadTab); tabs.TabPages.Add(browserTab);
            tabs.SelectedIndexChanged += async (_, _) =>
            {
                if (tabs.SelectedTab == browserTab)
                {
                    if (panes.Height > 350) panes.SplitterDistance = panes.Height - 200;
                    await EnsureBrowser();
                }
            };
            Controls.Add(tabs);
            status.Text = "Browser capture starts only when you open this tab.";
        }
        else
        {
            Controls.Add(layout);
            status.Text = "Starting the private capture browser...";
        }
        Shown += async (_, _) =>
        {
            if (desktop)
            {
                Program.Send(new { version = 1, @event = "ready" });
                if (desktopSmoke) RunDesktopSmoke();
            }
            else
            {
                panes.SplitterDistance = Math.Max(150, panes.Height - 200);
                await EnsureBrowser();
            }
        };
        scanTimer.Tick += async (_, _) => await ScanPage();
        smokeTimeout.Tick += (_, _) => FinishSmoke(false);
        FormClosed += (_, _) => { scanTimer.Dispose(); smokeTimeout.Dispose(); catalog.Clear(); documents.Clear(); browser.Dispose(); };
        FormClosing += (_, e) =>
        {
            if (parentClosing || downloader?.DownloadInProgress != true) return;
            if (MessageBox.Show(this, "Cancel the active download and close LocalWebVideoDownloader?", Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
            { e.Cancel = true; return; }
            downloader.CancelDownload();
        };
        if (smoke) { ShowInTaskbar = false; Opacity = 0; smokeTimeout.Start(); }
        if (desktopSmoke || integrationSmoke) { ShowInTaskbar = false; Opacity = 0; }
    }

    public void CloseFromParent() { parentClosing = true; Close(); }

    public void ReceiveDesktopCommand(JsonElement message)
    {
        if (IsDisposed || downloader is null) return;
        try
        {
            downloader.Receive(message);
            if (integrationSmoke)
            {
                switch (message.GetProperty("command").GetString())
                {
                    case "init": downloader.StartIntegrationInspection(); break;
                    case "inspection_complete": BeginInvoke(downloader.StartIntegrationDownload); break;
                    case "download_complete": integrationDownloadComplete = true; break;
                    case "busy" when integrationDownloadComplete &&
                        (!message.TryGetProperty("operation", out var currentOperation) ||
                         currentOperation.ValueKind == JsonValueKind.Null):
                        FinishIntegrationSmoke(); break;
                    case "error":
                        Program.Send(new { version = 1, @event = "desktop_smoke_complete", ok = false });
                        ExitCode = 5; BeginInvoke(CloseFromParent);
                        break;
                }
            }
        }
        catch (Exception e) when (e is InvalidOperationException or JsonException or ArgumentException)
        {
            Program.Send(new { version = 1, @event = "error", code = "protocol_error" });
            ExitCode = 2; CloseFromParent();
        }
    }

    private void FinishIntegrationSmoke()
    {
        var ok = Application.OpenForms.Count == 1 && initialization is null && browser.CoreWebView2 is null;
        Program.Send(new
        {
            version = 1,
            @event = "desktop_smoke_complete",
            ok,
            windows = Application.OpenForms.Count,
            browser_initialized = browser.CoreWebView2 is not null
        });
        ExitCode = ok ? 0 : 5;
        BeginInvoke(CloseFromParent);
    }

    private Task EnsureBrowser() => initialization ??= InitializeBrowser();

    private static Button MakeButton(string text, Action action)
    {
        var button = new Button { Text = text, AutoSize = true };
        button.Click += (_, _) => action();
        return button;
    }

    private async Task InitializeBrowser()
    {
        status.Text = "Starting the private capture browser...";
        try
        {
            var profile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalWebVideoDownloader", "WebView2");
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: profile);
            if (IsDisposed) return;
            var options = environment.CreateCoreWebView2ControllerOptions();
            options.IsInPrivateModeEnabled = true;
            await browser.EnsureCoreWebView2Async(environment, options);
            if (IsDisposed) return;
            var core = browser.CoreWebView2;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
            core.DownloadStarting += (_, e) => { e.Cancel = true; status.Text = "Select a detected source below to download it in the app."; };
            core.NewWindowRequested += (_, e) => { e.Handled = true; status.Text = "A new window was blocked. Paste the intended page URL in the address bar."; };
            core.LaunchingExternalUriScheme += (_, e) => e.Cancel = true;
            core.NavigationStarting += (_, e) =>
            {
                var target = MediaCatalog.NormalizeUrl(e.Uri);
                if (target is null) { e.Cancel = true; return; }
                pageUrl = target;
                ClearSources();
            };
            core.FrameNavigationStarting += (_, e) =>
            {
                if (e.Uri is not "about:blank" and not "about:srcdoc" && MediaCatalog.NormalizeUrl(e.Uri) is null) e.Cancel = true;
            };
            core.SourceChanged += (_, _) => { address.Text = core.Source; pageUrl = MediaCatalog.NormalizeUrl(core.Source) ?? pageUrl; };
            core.NavigationCompleted += async (_, e) =>
            {
                if (!e.IsSuccess) status.Text = "The page could not finish loading. Check the address or try Reload.";
                if (enabled) await ScanPage();
            };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All,
                CoreWebView2WebResourceRequestSourceKinds.All);
            core.WebResourceRequested += (_, e) =>
            {
                if (!enabled || e.Request.Method != "GET") return;
                var url = MediaCatalog.NormalizeUrl(e.Request.Uri);
                if (url is null) return;
                if (documents.Count >= 128 && !documents.ContainsKey(url)) documents.Remove(documents.Keys.First());
                documents[url] = documents.TryGetValue(url, out var previous) && previous.Generation == generation
                    ? previous with { Observed = true }
                    : new(generation, pageUrl, null, true);
            };
            core.WebResourceResponseReceived += (_, e) =>
            {
                if (!enabled || e.Request.Method != "GET") return;
                var url = e.Request.Uri;
                if (!documents.Remove(url, out var observed) || !observed.Observed || observed.Generation != generation) return;
                if (e.Response.StatusCode is < 200 or >= 300) return;
                var mime = e.Response.Headers.Contains("Content-Type") ? e.Response.Headers.GetHeader("Content-Type") : "";
                var document = observed.DocumentUrl;
                // Cross-process frames may not appear in this DevTools session. The browser's
                // actual Referer origin supplies bounded provenance without exporting credentials.
                if (document is null && e.Request.Headers.Contains("Referer"))
                {
                    var referer = MediaCatalog.NormalizeUrl(e.Request.Headers.GetHeader("Referer"));
                    if (referer is not null) document = new Uri(referer).GetLeftPart(UriPartial.Authority) + "/";
                }
                AddSource(MediaCatalog.Classify(url, mime, observed.PageUrl, document));
            };
            // DevTools supplies only document provenance here; no headers or response bodies are retained.
            core.GetDevToolsProtocolEventReceiver("Network.requestWillBeSent").DevToolsProtocolEventReceived += (_, e) =>
            {
                if (!enabled || e.ParameterObjectAsJson.Length > 256 * 1024) return;
                try
                {
                    using var json = JsonDocument.Parse(e.ParameterObjectAsJson);
                    var root = json.RootElement;
                    var url = MediaCatalog.NormalizeUrl(root.GetProperty("request").GetProperty("url").GetString());
                    var document = MediaCatalog.NormalizeUrl(root.GetProperty("documentURL").GetString());
                    if (url is not null && document is not null)
                    {
                        if (documents.Count >= 128 && !documents.ContainsKey(url)) documents.Remove(documents.Keys.First());
                        documents[url] = documents.TryGetValue(url, out var observed) && observed.Generation == generation
                            ? observed with { DocumentUrl = document }
                            : new(generation, pageUrl, document);
                    }
                }
                catch (JsonException) { }
                catch (InvalidOperationException) { }
                catch (KeyNotFoundException) { }
            };
            await core.CallDevToolsProtocolMethodAsync("Network.enable", "{}");
            if (smoke) InstallSmokeFixture(environment);
            ready = true;
            detection.Enabled = true;
            if (!desktop) Program.Send(new { version = 1, @event = "ready" });
            status.Text = "Open a page and play its video. Use Start detection to find loaded media.";
            if (smoke)
            {
                SetDetection(true);
                core.Navigate("https://capture-fixture.invalid/page");
            }
            else if (!desktop && initialUrl is not null) { address.Text = initialUrl; Navigate(); }
        }
        catch (WebView2RuntimeNotFoundException)
        {
            if (desktop) status.Text = "WebView2 Runtime was not found. Install Microsoft's Evergreen WebView2 Runtime to use browser capture. The Download tab is still available.";
            else { Program.Send(new { version = 1, @event = "error", code = "runtime_missing" }); ExitCode = 3; Close(); }
        }
        catch
        {
            if (desktop) status.Text = "The capture browser could not start. Close and reopen the app to retry. The Download tab is still available.";
            else { Program.Send(new { version = 1, @event = "error", code = "initialization_failed" }); ExitCode = 4; Close(); }
        }
    }

    private void Navigate()
    {
        if (!ready) return;
        var url = MediaCatalog.NormalizeUrl(address.Text);
        if (url is null) { status.Text = "Enter a complete http:// or https:// page URL."; return; }
        SetDetection(true);
        browser.CoreWebView2.Navigate(url);
    }

    private void SetDetection(bool value)
    {
        enabled = value;
        ClearSources();
        detection.Text = enabled ? "Stop detection" : "Start detection";
        scanTimer.Enabled = enabled;
        status.Text = enabled ? "Detection on. Play or reload the video to reveal its media sources." : "Detection stopped. Captured sources cleared.";
        if (enabled && ready) _ = ScanPage();
    }

    private void ClearSources()
    {
        generation++;
        catalog.Clear(); documents.Clear(); sources.Items.Clear(); inspect.Enabled = false;
    }

    private void AddSource(MediaCandidate? candidate)
    {
        if (!enabled || candidate is null || !catalog.Add(candidate)) return;
        var selectedUrl = sources.SelectedItems.Count > 0 ? ((MediaCandidate)sources.SelectedItems[0].Tag!).Url : null;
        sources.BeginUpdate(); sources.Items.Clear();
        foreach (var item in catalog.Items)
        {
            var row = new ListViewItem([item.Kind.ToUpperInvariant(), item.DisplayUrl, item.Evidence, item.ContentType]) { Tag = item };
            sources.Items.Add(row);
            row.Selected = item.Url == selectedUrl;
        }
        sources.EndUpdate();
        status.Text = $"Detection on. {catalog.Items.Count} source(s) found. Select a manifest or video and click Inspect selected.";
        if (smoke && catalog.Items.Any(c => c.Kind == "hls") && catalog.Items.Any(c => c.Kind == "direct")) FinishSmoke(true);
    }

    private void InspectSelected()
    {
        if (sources.SelectedItems.Count == 1) SendCapture((MediaCandidate)sources.SelectedItems[0].Tag!);
    }

    private void InspectPage()
    {
        var source = MediaCatalog.Classify(pageUrl, "", pageUrl, pageUrl, "embed", "dom");
        if (source is not null) SendCapture(source);
    }

    private void SendCapture(MediaCandidate source)
    {
        if (desktop && downloader is not null && tabs is not null)
        {
            downloader.InspectCapture(source);
            tabs.SelectedTab = downloadTab;
            status.Text = "Source sent to the Download tab for quality and caption inspection.";
            return;
        }
        Program.Send(new
        {
            version = 1, @event = "capture",
            capture = new { url = source.Url, kind = source.Kind, page_url = source.PageUrl, frame_url = source.FrameUrl, content_type = source.ContentType }
        });
        status.Text = "Sent to the download window for quality and caption inspection.";
    }

    private async Task ScanPage()
    {
        if (!ready || !enabled || scanning || IsDisposed) return;
        scanning = true;
        var epoch = generation;
        try
        {
            var json = await browser.CoreWebView2.ExecuteScriptAsync(DomScanScript);
            if (!enabled || epoch != generation || IsDisposed || json.Length > 1024 * 1024) return;
            using var document = JsonDocument.Parse(json);
            if (document.RootElement.ValueKind != JsonValueKind.Array) return;
            foreach (var item in document.RootElement.EnumerateArray().Take(64))
            {
                AddSource(MediaCatalog.Classify(item.GetProperty("url").GetString(), "", pageUrl,
                    item.GetProperty("frame").GetString(), item.GetProperty("hint").GetString() ?? "", "dom"));
            }
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or System.Runtime.InteropServices.COMException or KeyNotFoundException)
        { /* Network observation continues if the current document cannot be scanned. */ }
        finally { scanning = false; }
    }

    private const string DomScanScript = """
        (() => {
          const result = [], visited = new Set(), seen = new Set();
          function scan(win, depth) {
            if (depth > 3 || visited.size >= 32 || visited.has(win)) return;
            visited.add(win);
            try {
              const doc = win.document;
              function add(value, hint) {
                if (typeof value !== 'string' || !value || value.length > 8192) return;
                try {
                  const u = new URL(value, doc.baseURI);
                  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.href.length > 8192) return;
                  u.hash = '';
                  if (seen.has(u.href)) return;
                  const path = u.pathname.toLowerCase();
                  const priority = /\.(m3u8|mpd)$/.test(path) ? 0 : /\.(mp4|webm|m4v|mov|mkv|ogv)$/.test(path) ? 1 : hint === 'media' ? 2 : hint === 'embed' ? 3 : 9;
                  if (priority === 9 || /\.(ts|m4s|cmfv|cmfa|m4f|aac)$/.test(path)) return;
                  const candidate = {url:u.href, hint, frame:win.location.href, priority};
                  if (result.length >= 64) {
                    const worst = result.reduce((a, c, i) => c.priority > result[a].priority ? i : a, 0);
                    if (priority >= result[worst].priority) return;
                    seen.delete(result[worst].url);
                    result.splice(worst, 1);
                  }
                  result.push(candidate); seen.add(u.href);
                } catch {}
              }
              for (const v of doc.querySelectorAll('video')) { add(v.currentSrc, 'media'); add(v.src, 'media'); }
              for (const s of doc.querySelectorAll('video source[src]')) add(s.src, 'media');
              for (let i = 0; i < win.frames.length && i < 32; i++) scan(win.frames[i], depth + 1);
              for (const f of doc.querySelectorAll('iframe[src],embed[src]')) add(f.src, 'embed');
              for (const e of win.performance.getEntriesByType('resource').slice(-256)) add(e.name, '');
            } catch {}
          }
          scan(window, 0);
          return result;
        })()
        """;

    private void InstallSmokeFixture(CoreWebView2Environment environment)
    {
        browser.CoreWebView2.WebResourceRequested += (_, e) =>
        {
            var path = new Uri(e.Request.Uri).AbsolutePath;
            var (mime, body) = path switch
            {
                "/page" => ("text/html", "<title>Local capture fixture</title><iframe src='https://player-fixture.invalid/frame'></iframe>"),
                "/frame" => ("text/html", "<video src='https://cdn-fixture.invalid/clip.mp4'></video><script>fetch('https://cdn-fixture.invalid/manifest').catch(()=>{});</script>"),
                "/manifest" => ("application/vnd.apple.mpegurl", "#EXTM3U\n#EXT-X-ENDLIST\n"),
                "/clip.mp4" => ("video/mp4", "fixture"),
                _ => ("text/plain", "")
            };
            e.Response = environment.CreateWebResourceResponse(new MemoryStream(Encoding.UTF8.GetBytes(body)), 200, "OK", $"Content-Type: {mime}\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-store");
        };
    }

    private void FinishSmoke(bool success)
    {
        if (!smoke || IsDisposed || !smokeTimeout.Enabled) return;
        smokeTimeout.Stop();
        if (success) SendCapture(catalog.Items.First(c => c.Kind == "hls"));
        Program.Send(new { version = 1, @event = "smoke_complete", ok = success, kinds = catalog.Items.Select(c => c.Kind).Distinct().ToArray() });
        ExitCode = success ? 0 : 5;
        BeginInvoke(() => Close());
    }

    private void RunDesktopSmoke()
    {
        try
        {
            if (tabs?.SelectedTab != downloadTab || initialization is not null || browser.CoreWebView2 is not null)
                throw new InvalidOperationException("Browser was initialized at startup.");
            downloader!.VerifySmokeState();
            if (Application.OpenForms.Count != 1) throw new InvalidOperationException("More than one top-level window opened.");
            using var screenshot = new Bitmap(Width, Height);
            DrawToBitmap(screenshot, new Rectangle(Point.Empty, Size));
            screenshot.Save(Path.Combine(AppContext.BaseDirectory, "desktop-ui-smoke.png"), System.Drawing.Imaging.ImageFormat.Png);
            Program.Send(new { version = 1, @event = "desktop_smoke_complete", ok = true, browser_initialized = false, windows = Application.OpenForms.Count });
        }
        catch
        {
            ExitCode = 5;
            Program.Send(new { version = 1, @event = "desktop_smoke_complete", ok = false });
        }
        BeginInvoke(CloseFromParent);
    }
}
