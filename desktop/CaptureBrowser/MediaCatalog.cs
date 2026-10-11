using System.Text.RegularExpressions;

namespace LocalWebVideoDownloader;

internal sealed record MediaCandidate(string Url, string Kind, string ContentType, string PageUrl,
    string? FrameUrl, string Evidence)
{
    public string DisplayUrl => MediaCatalog.DisplayUrl(Url);
    public int Priority => Kind switch { "hls" => 0, "dash" => 1, "direct" => 2, "media" => 3, _ => 4 };
}

internal sealed class MediaCatalog
{
    public const int MaxUrlLength = 8192;
    public const int MaxCandidates = 64;
    private readonly List<MediaCandidate> items = [];
    public IReadOnlyList<MediaCandidate> Items => items.OrderBy(c => c.Priority).ToArray();

    public static string? NormalizeUrl(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw) || raw.Length > MaxUrlLength || raw.Any(char.IsControl))
            return null;
        raw = raw.Trim();
        if (!Uri.TryCreate(raw, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "https" && uri.Scheme != "http") ||
            uri.Host.Length == 0 || uri.UserInfo.Length > 0 || uri.AbsoluteUri.Length > MaxUrlLength)
            return null;
        // Preserve the supplied path/query bytes: signed URLs must never be shortened or rebuilt.
        var fragment = raw.IndexOf('#');
        return fragment < 0 ? raw : raw[..fragment];
    }

    public static string DisplayUrl(string raw)
    {
        var url = NormalizeUrl(raw);
        if (url is null) return "Unavailable source";
        var uri = new Uri(url);
        var extension = Path.GetExtension(uri.AbsolutePath);
        if (extension.Length > 8 || !Regex.IsMatch(extension, @"^\.[a-zA-Z0-9]+$")) extension = "";
        return uri.GetLeftPart(UriPartial.Authority) + "/<media>" + extension;
    }

    public static MediaCandidate? Classify(string? raw, string? mime, string pageUrl,
        string? frameUrl = null, string hint = "", string evidence = "network")
    {
        var url = NormalizeUrl(raw);
        var page = NormalizeUrl(pageUrl);
        if (url is null || page is null) return null;
        var uri = new Uri(url);
        var path = uri.AbsolutePath.ToLowerInvariant();
        var filename = Path.GetFileName(path);
        var ext = Path.GetExtension(path);
        var contentType = (mime ?? "").Split(';')[0].Trim().ToLowerInvariant();
        if (contentType.Length > 128 || !Regex.IsMatch(contentType, @"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$"))
            contentType = "";
        string? kind = null;
        if (contentType is "application/vnd.apple.mpegurl" or "application/x-mpegurl" or
            "audio/mpegurl" or "audio/x-mpegurl" || path.EndsWith(".m3u8")) kind = "hls";
        else if (contentType == "application/dash+xml" || path.EndsWith(".mpd")) kind = "dash";
        else
        {
            if (ext is ".ts" or ".m4s" or ".cmfv" or ".cmfa" or ".m4f" or ".aac" ||
                Regex.IsMatch(filename, @"(?:^|[_.-])(?:seg(?:ment)?|chunk|frag(?:ment)?|part)[_-]?\d") ||
                Regex.IsMatch(filename, @"^(?:init|initialization)(?:[-_.]|$)")) return null;
            if (ext is ".mp4" or ".webm" or ".m4v" or ".mov" or ".mkv" or ".ogv" ||
                contentType.StartsWith("video/")) kind = "direct";
            else if (hint == "media" && !contentType.StartsWith("audio/")) kind = "media";
            else if (hint == "embed") kind = "embed";
        }
        return kind is null ? null : new(url, kind, contentType, page,
            NormalizeUrl(frameUrl), evidence is "dom" ? "dom" : "network");
    }

    public bool Add(MediaCandidate candidate)
    {
        var index = items.FindIndex(c => c.Url == candidate.Url);
        if (index >= 0)
        {
            var old = items[index];
            var merged = candidate with
            {
                Kind = old.Priority < candidate.Priority ? old.Kind : candidate.Kind,
                ContentType = candidate.ContentType.Length > 0 ? candidate.ContentType : old.ContentType,
                FrameUrl = candidate.FrameUrl ?? old.FrameUrl,
            };
            if (old == merged) return false;
            items[index] = merged;
            return true;
        }
        if (items.Count == MaxCandidates)
        {
            var worst = items.MaxBy(c => c.Priority)!;
            if (candidate.Priority >= worst.Priority) return false;
            items.Remove(worst);
        }
        items.Add(candidate);
        return true;
    }

    public void Clear() => items.Clear();
}
