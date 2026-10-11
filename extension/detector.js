"use strict";

(() => {
  const CONTROLLER_KEY = "__localWebVideoDownloaderDetector";
  const MAX_URL_LENGTH = 8192;
  const MAX_LOCAL_SEEN = 256;
  const FLUSH_DELAY_MS = 120;

  if (globalThis[CONTROLLER_KEY] && globalThis[CONTROLLER_KEY].active) {
    globalThis[CONTROLLER_KEY].scan();
    return;
  }

  const MEDIA_EXTENSIONS = new Set([
    ".mp4",
    ".webm",
    ".m4v",
    ".mov",
    ".mkv",
    ".avi",
    ".ogv",
  ]);
  const SEGMENT_EXTENSIONS = new Set([".ts", ".m4s", ".cmfv", ".cmfa", ".m4f", ".aac"]);
  const seen = new Set();
  const pending = new Map();
  let active = true;
  let flushTimer = 0;
  let scanTimer = 0;

  function normalizeHttpUrl(value) {
    if (
      typeof value !== "string" ||
      !value.trim() ||
      value.length > MAX_URL_LENGTH ||
      /[\u0000-\u001f\u007f]/.test(value)
    ) {
      return "";
    }
    try {
      const parsed = new URL(value.trim(), document.baseURI);
      if (parsed.href.length > MAX_URL_LENGTH) {
        return "";
      }
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return "";
      }
      if (!parsed.hostname || parsed.username || parsed.password) {
        return "";
      }
      parsed.hash = "";
      return parsed.href.length <= MAX_URL_LENGTH ? parsed.href : "";
    } catch {
      return "";
    }
  }

  function pathExtension(value) {
    try {
      const pathname = new URL(value).pathname.toLowerCase();
      const slash = pathname.lastIndexOf("/");
      const dot = pathname.lastIndexOf(".");
      return dot > slash ? pathname.slice(dot) : "";
    } catch {
      return "";
    }
  }

  function looksLikeSegment(value) {
    const extension = pathExtension(value);
    if (SEGMENT_EXTENSIONS.has(extension)) {
      return true;
    }
    try {
      const pathname = new URL(value).pathname;
      return (
        /(?:^|[\/_\-.])(?:seg(?:ment)?|chunk|frag(?:ment)?|part)[_-]?\d/i.test(pathname) ||
        /(?:^|\/)(?:init|initialization)(?:[-_.]|$)/i.test(pathname)
      );
    } catch {
      return true;
    }
  }

  function mediaTypeForExtension(extension) {
    const values = {
      ".mp4": "video/mp4",
      ".m4v": "video/mp4",
      ".webm": "video/webm",
      ".mov": "video/quicktime",
      ".ogv": "video/ogg",
    };
    return values[extension] || "";
  }

  function classify(value, fallbackKind = "") {
    const url = normalizeHttpUrl(value);
    if (!url || looksLikeSegment(url)) {
      return null;
    }
    const parsed = new URL(url);
    const pathname = parsed.pathname.toLowerCase();
    const searchable = `${pathname}${parsed.search}`.toLowerCase();
    if (pathname.endsWith(".m3u8") || /(?:m3u8|mpegurl)/.test(searchable)) {
      return { url, kind: "hls", contentType: "application/vnd.apple.mpegurl" };
    }
    if (pathname.endsWith(".mpd") || /(?:[?&](?:format|type)=mpd\b)/.test(searchable)) {
      return { url, kind: "dash", contentType: "application/dash+xml" };
    }
    const extension = pathExtension(url);
    if (MEDIA_EXTENSIONS.has(extension)) {
      return { url, kind: "direct", contentType: mediaTypeForExtension(extension) };
    }
    if (fallbackKind === "embed" || fallbackKind === "media") {
      return {
        url,
        kind: fallbackKind,
        contentType: fallbackKind === "embed" ? "text/html" : "",
      };
    }
    return null;
  }

  function queueCandidate(value, fallbackKind, source) {
    if (!active || seen.size >= MAX_LOCAL_SEEN) {
      return;
    }
    const candidate = classify(value, fallbackKind);
    if (!candidate) {
      return;
    }
    const identity = `${candidate.kind}\u0000${candidate.url}`;
    if (seen.has(identity)) {
      return;
    }
    seen.add(identity);
    pending.set(identity, candidate);
    scheduleFlush(source);
  }

  function scheduleFlush(source) {
    if (flushTimer) {
      return;
    }
    flushTimer = globalThis.setTimeout(() => {
      flushTimer = 0;
      if (!active || pending.size === 0) {
        return;
      }
      const candidates = [...pending.values()].slice(0, 64);
      pending.clear();
      sendDiscovery(candidates, source);
    }, FLUSH_DELAY_MS);
  }

  function sendDiscovery(candidates, source) {
    const frameUrl = normalizeHttpUrl(location.href);
    const pageTitle = globalThis.top === globalThis ? document.title.slice(0, 256) : "";
    void chrome.runtime
      .sendMessage({
        type: "IFD_CANDIDATES_DISCOVERED",
        source,
        frameUrl,
        pageTitle,
        candidates,
      })
      .catch(() => undefined);
  }

  function scanMediaElement(element) {
    queueCandidate(element.currentSrc, "media", "dom");
    queueCandidate(element.src, "media", "dom");
    for (const source of element.querySelectorAll("source[src]")) {
      queueCandidate(source.src, "media", "dom");
    }
  }

  function scanDocument() {
    if (!active) {
      return;
    }
    for (const media of document.querySelectorAll("video")) {
      scanMediaElement(media);
    }
    for (const source of document.querySelectorAll("video source[src]")) {
      queueCandidate(source.src, "media", "dom");
    }
    for (const frame of document.querySelectorAll("iframe[src], embed[src]")) {
      queueCandidate(frame.src, "embed", "dom");
    }
  }

  function scheduleScan() {
    if (!active || scanTimer) {
      return;
    }
    scanTimer = globalThis.setTimeout(() => {
      scanTimer = 0;
      scanDocument();
    }, 180);
  }

  function inspectPerformanceEntry(entry) {
    if (!entry || typeof entry.name !== "string") {
      return;
    }
    const initiator = String(entry.initiatorType || "").toLowerCase();
    const fallback = initiator === "video" ? "media" : "";
    queueCandidate(entry.name, fallback, "performance");
  }

  for (const entry of performance.getEntriesByType("resource")) {
    inspectPerformanceEntry(entry);
  }

  let performanceObserver = null;
  if (typeof PerformanceObserver === "function") {
    performanceObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        inspectPerformanceEntry(entry);
      }
    });
    try {
      performanceObserver.observe({ type: "resource", buffered: true });
    } catch {
      try {
        performanceObserver.observe({ entryTypes: ["resource"] });
      } catch {
        performanceObserver = null;
      }
    }
  }

  const mutationObserver = new MutationObserver(scheduleScan);
  mutationObserver.observe(document.documentElement || document, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["src"],
  });

  function mediaEventHandler(event) {
    if (event.target instanceof HTMLVideoElement) {
      scanMediaElement(event.target);
    }
  }

  document.addEventListener("loadedmetadata", mediaEventHandler, true);
  document.addEventListener("canplay", mediaEventHandler, true);

  function stop() {
    if (!active) {
      return;
    }
    active = false;
    mutationObserver.disconnect();
    if (performanceObserver) {
      performanceObserver.disconnect();
    }
    document.removeEventListener("loadedmetadata", mediaEventHandler, true);
    document.removeEventListener("canplay", mediaEventHandler, true);
    if (flushTimer) {
      globalThis.clearTimeout(flushTimer);
    }
    if (scanTimer) {
      globalThis.clearTimeout(scanTimer);
    }
    pending.clear();
    globalThis[CONTROLLER_KEY] = null;
  }

  globalThis[CONTROLLER_KEY] = { active: true, scan: scanDocument, stop };
  sendDiscovery([], "dom");
  scanDocument();
})();
