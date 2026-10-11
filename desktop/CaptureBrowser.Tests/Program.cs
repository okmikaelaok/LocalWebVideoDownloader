using LocalWebVideoDownloader;

const string Page = "https://example.test/watch";
var count = 0;
void Check(bool condition, string name)
{
    if (!condition) throw new Exception("Failed: " + name);
    count++;
}

Check(MediaCatalog.Classify("https://cdn.test/source?token=secret", "application/vnd.apple.mpegurl; charset=utf-8", Page)?.Kind == "hls", "extensionless HLS MIME");
Check(MediaCatalog.Classify("https://cdn.test/source", "application/dash+xml", Page)?.Kind == "dash", "extensionless DASH MIME");
Check(MediaCatalog.Classify("https://cdn.test/source", "video/mp4", Page)?.Kind == "direct", "extensionless video MIME");
Check(MediaCatalog.Classify("https://cdn.test/master.m3u8", "", Page)?.Kind == "hls", "manifest extension");
Check(MediaCatalog.Classify("https://cdn.test/clip.mp4", "", Page)?.Kind == "direct", "direct file");
Check(MediaCatalog.Classify("https://cdn.test/courses/part-1/lecture.mp4", "video/mp4", Page)?.Kind == "direct", "directory names are not fragments");
Check(MediaCatalog.Classify("https://cdn.test/init/lecture.mp4", "video/mp4", Page)?.Kind == "direct", "init directory is not fragment");
foreach (var url in new[] { "blob:https://example.test/id", "file:///C:/secret", "https://u:p@cdn.test/clip.mp4", "javascript:alert(1)", "https://cdn.test/a\nb.mp4" })
    Check(MediaCatalog.NormalizeUrl(url) is null, "reject non-web/credentials/control");
Check(MediaCatalog.NormalizeUrl("https://cdn.test/v?sig=%2f%2F+abc#fragment") == "https://cdn.test/v?sig=%2f%2F+abc", "preserve signed query exactly");
Check(MediaCatalog.NormalizeUrl("https://cdn.test/" + new string('a', 8192)) is null, "reject overlong input");
Check(MediaCatalog.NormalizeUrl("https://cdn.test/" + new string('\u00e9', 4000)) is null, "reject expanded absolute URL");
foreach (var path in new[] { "segment-1.mp4", "chunk2.mp4", "init.mp4", "1.m4s", "part-3.ts" })
    Check(MediaCatalog.Classify("https://cdn.test/" + path, "video/mp4", Page) is null, "ignore fragments");
Check(!MediaCatalog.DisplayUrl("https://cdn.test/secret-path/clip.mp4?token=secret").Contains("secret"), "redact path and query");
Check(MediaCatalog.Classify("https://cdn.test/endpoint", "audio/mpeg", Page, hint: "media") is null, "ignore audio-only MIME");
var catalog = new MediaCatalog();
for (var i = 0; i < 100; i++) catalog.Add(MediaCatalog.Classify($"https://cdn.test/embed/{i}", "", Page, hint: "embed")!);
Check(catalog.Items.Count == 64, "bounded catalog");
var manifest = MediaCatalog.Classify("https://cdn.test/source", "application/dash+xml", Page)!;
catalog.Add(manifest);
Check(catalog.Items.Count == 64 && catalog.Items[0].Kind == "dash", "prioritize manifest at capacity");
catalog.Add(manifest with { FrameUrl = "https://player.test/embed" });
Check(catalog.Items.Count == 64 && catalog.Items[0].FrameUrl == "https://player.test/embed", "dedup preserves provenance");
catalog.Clear();
Check(catalog.Items.Count == 0, "clear session");
Console.WriteLine($"Passed {count} media discovery checks.");
