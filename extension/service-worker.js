"use strict";

const STATE_VERSION = 1;
const STATE_PREFIX = "local-web-video-downloader-tab-";
const MAX_CANDIDATES = 64;
const MAX_URL_LENGTH = 8192;
const MAX_TITLE_LENGTH = 256;
const MAX_CONTENT_TYPE_LENGTH = 128;
const NATIVE_HOST = "com.localwebvideodownloader.bridge";

const MESSAGE = Object.freeze({
  START: "IFD_START_DETECTION",
  STOP: "IFD_STOP_DETECTION",
  CLEAR: "IFD_CLEAR_CANDIDATES",
  STATUS: "IFD_GET_STATUS",
  OPEN: "IFD_OPEN_CANDIDATE",
  DISCOVERED: "IFD_CANDIDATES_DISCOVERED",
});

const KIND_PRIORITY = Object.freeze({
  hls: 0,
  dash: 1,
  direct: 2,
  media: 3,
  embed: 4,
});

const MEDIA_EXTENSIONS = new Set([
  ".mp4",
  ".webm",
  ".m4v",
  ".mov",
  ".mkv",
  ".avi",
  ".ogv",
]);

const SEGMENT_EXTENSIONS = new Set([
  ".ts",
  ".m4s",
  ".cmfv",
  ".cmfa",
  ".m4f",
  ".aac",
]);

const tabQueues = new Map();

function stateKey(tabId) {
  return `${STATE_PREFIX}${tabId}`;
}

function boundedString(value, maximum) {
  if (typeof value !== "string") {
    return "";
  }
  const normalized = value.replace(/[\u0000-\u001f\u007f]/g, " ").trim();
  return normalized.slice(0, maximum);
}

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
    const parsed = new URL(value.trim());
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

function normalizeContentType(value) {
  const mediaType = boundedString(value, MAX_CONTENT_TYPE_LENGTH)
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  return /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(mediaType)
    ? mediaType
    : "";
}

function sanitizedDisplayUrl(value) {
  const normalized = normalizeHttpUrl(value);
  if (!normalized) {
    return "Unavailable URL";
  }
  const parsed = new URL(normalized);
  let path = parsed.pathname || "/";
  if (path.length > 120) {
    path = `${path.slice(0, 72)}…${path.slice(-36)}`;
  }
  return `${parsed.origin}${path}`;
}

function sameOrigin(left, right) {
  try {
    return new URL(left).origin === new URL(right).origin;
  } catch {
    return false;
  }
}

function pathExtension(value) {
  try {
    const pathname = new URL(value).pathname.toLowerCase();
    const lastSlash = pathname.lastIndexOf("/");
    const lastDot = pathname.lastIndexOf(".");
    return lastDot > lastSlash ? pathname.slice(lastDot) : "";
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

function classifyUrl(value, requestType = "") {
  const normalized = normalizeHttpUrl(value);
  if (!normalized || looksLikeSegment(normalized)) {
    return null;
  }
  const parsed = new URL(normalized);
  const pathname = parsed.pathname.toLowerCase();
  const searchable = `${pathname}${parsed.search}`.toLowerCase();
  if (pathname.endsWith(".m3u8") || /(?:m3u8|mpegurl)/.test(searchable)) {
    return { kind: "hls", contentType: "application/vnd.apple.mpegurl" };
  }
  if (pathname.endsWith(".mpd") || /(?:[?&](?:format|type)=mpd\b)/.test(searchable)) {
    return { kind: "dash", contentType: "application/dash+xml" };
  }
  if (MEDIA_EXTENSIONS.has(pathExtension(normalized))) {
    return { kind: "direct", contentType: inferredMediaType(pathExtension(normalized)) };
  }
  if (requestType === "media") {
    return { kind: "media", contentType: "" };
  }
  return null;
}

function inferredMediaType(extension) {
  const mediaTypes = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".ogv": "video/ogg",
  };
  return mediaTypes[extension] || "";
}

function classifyContentType(value, url, requestType = "") {
  if (looksLikeSegment(url)) {
    return null;
  }
  const contentType = normalizeContentType(value);
  if (
    contentType === "application/vnd.apple.mpegurl" ||
    contentType === "application/x-mpegurl" ||
    contentType === "audio/mpegurl" ||
    contentType === "audio/x-mpegurl"
  ) {
    return { kind: "hls", contentType };
  }
  if (contentType === "application/dash+xml") {
    return { kind: "dash", contentType };
  }
  if (contentType.startsWith("video/")) {
    return { kind: requestType === "media" ? "media" : "direct", contentType };
  }
  return classifyUrl(url, requestType);
}

function contentTypeFromHeaders(headers) {
  if (!Array.isArray(headers)) {
    return "";
  }
  const header = headers.find(
    (candidate) =>
      candidate && typeof candidate.name === "string" && candidate.name.toLowerCase() === "content-type",
  );
  return normalizeContentType(header && typeof header.value === "string" ? header.value : "");
}

function makeCandidateId() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function enqueueTab(tabId, operation) {
  const previous = tabQueues.get(tabId) || Promise.resolve();
  const current = previous.catch(() => undefined).then(operation);
  const tracked = current.finally(() => {
    if (tabQueues.get(tabId) === tracked) {
      tabQueues.delete(tabId);
    }
  });
  tabQueues.set(tabId, tracked);
  return tracked;
}

async function readState(tabId) {
  const key = stateKey(tabId);
  const stored = await chrome.storage.session.get(key);
  const state = stored[key];
  return state && state.version === STATE_VERSION && state.enabled ? state : null;
}

async function writeState(tabId, state) {
  await chrome.storage.session.set({ [stateKey(tabId)]: state });
}

async function removeState(tabId) {
  await chrome.storage.session.remove(stateKey(tabId));
}

async function setDetectionBadge(tabId, enabled) {
  await chrome.action.setBadgeBackgroundColor({ tabId, color: enabled ? "#176b45" : "#697386" });
  await chrome.action.setBadgeText({ tabId, text: enabled ? "ON" : "" });
}

function normalizedCandidate(raw, provenance) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const url = normalizeHttpUrl(raw.url);
  const requestedKind = boundedString(raw.kind, 16).toLowerCase();
  const kind = Object.hasOwn(KIND_PRIORITY, requestedKind) ? requestedKind : "";
  if (!url || !kind || looksLikeSegment(url)) {
    return null;
  }
  return {
    url,
    kind,
    contentType: normalizeContentType(raw.contentType),
    source: provenance.source,
    frameUrl: normalizeHttpUrl(provenance.frameUrl),
    observedAt: Date.now(),
  };
}

function mergeCandidate(state, candidate) {
  const existing = state.candidates.find((item) => item.url === candidate.url);
  if (existing) {
    if (KIND_PRIORITY[candidate.kind] < KIND_PRIORITY[existing.kind]) {
      existing.kind = candidate.kind;
    }
    existing.contentType = candidate.contentType || existing.contentType;
    existing.frameUrl = candidate.frameUrl || existing.frameUrl;
    if (candidate.source === "network") {
      existing.source = "network";
    }
    existing.observedAt = candidate.observedAt;
    return;
  }

  const item = { id: makeCandidateId(), ...candidate };
  if (state.candidates.length < MAX_CANDIDATES) {
    state.candidates.push(item);
    return;
  }

  let replaceIndex = 0;
  for (let index = 1; index < state.candidates.length; index += 1) {
    const current = state.candidates[index];
    const replacement = state.candidates[replaceIndex];
    const currentPriority = KIND_PRIORITY[current.kind];
    const replacementPriority = KIND_PRIORITY[replacement.kind];
    if (
      currentPriority > replacementPriority ||
      (currentPriority === replacementPriority && current.observedAt < replacement.observedAt)
    ) {
      replaceIndex = index;
    }
  }
  const replacePriority = KIND_PRIORITY[state.candidates[replaceIndex].kind];
  if (KIND_PRIORITY[item.kind] <= replacePriority) {
    state.candidates[replaceIndex] = item;
  }
}

function addCandidates(tabId, rawCandidates, provenance) {
  return enqueueTab(tabId, async () => {
    const state = await readState(tabId);
    if (!state) {
      return false;
    }
    const frameId = Number.isInteger(provenance.frameId) ? provenance.frameId : -1;
    const reportedFrameUrl = normalizeHttpUrl(provenance.frameUrl);
    if (frameId >= 0 && reportedFrameUrl) {
      state.frames[String(frameId)] = reportedFrameUrl;
    }
    const effectiveProvenance = {
      ...provenance,
      frameId,
      frameUrl: reportedFrameUrl || state.frames[String(frameId)] || state.pageUrl,
    };
    for (const raw of rawCandidates.slice(0, MAX_CANDIDATES)) {
      const candidate = normalizedCandidate(raw, effectiveProvenance);
      if (candidate) {
        mergeCandidate(state, candidate);
      }
    }
    state.updatedAt = Date.now();
    await writeState(tabId, state);
    return true;
  });
}

function considerNetworkEvent(details, responseHeaders = null) {
  return enqueueTab(details.tabId, async () => {
    const state = await readState(details.tabId);
    if (!state) {
      return;
    }
    let stateChanged = false;
    const frameId = Number.isInteger(details.frameId) ? details.frameId : -1;
    if (details.type === "sub_frame" && frameId >= 0) {
      const frameUrl = normalizeHttpUrl(details.url);
      if (frameUrl) {
        state.frames[String(frameId)] = frameUrl;
        stateChanged = true;
      }
    }
    const classification = responseHeaders
      ? classifyContentType(
          contentTypeFromHeaders(responseHeaders),
          details.url,
          details.type,
        )
      : classifyUrl(details.url, details.type);
    if (!classification) {
      if (stateChanged) {
        state.updatedAt = Date.now();
        await writeState(details.tabId, state);
      }
      return;
    }
    const candidate = normalizedCandidate(
      { url: details.url, ...classification },
      {
        source: "network",
        frameId,
        frameUrl: state.frames[String(frameId)] || state.pageUrl,
      },
    );
    if (!candidate) {
      return;
    }
    mergeCandidate(state, candidate);
    state.updatedAt = Date.now();
    await writeState(details.tabId, state);
  });
}

function publicState(state) {
  if (!state) {
    return {
      enabled: false,
      pageTitle: "",
      pageDisplayUrl: "",
      candidates: [],
    };
  }
  const candidates = [...state.candidates]
    .sort(
      (left, right) =>
        KIND_PRIORITY[left.kind] - KIND_PRIORITY[right.kind] ||
        right.observedAt - left.observedAt,
    )
    .map((candidate) => ({
      id: candidate.id,
      kind: candidate.kind,
      source: candidate.source,
      contentType: candidate.contentType,
      displayUrl: sanitizedDisplayUrl(candidate.url),
      frameDisplayUrl: sanitizedDisplayUrl(candidate.frameUrl || state.pageUrl),
      observedAt: candidate.observedAt,
    }));
  return {
    enabled: true,
    pageTitle: boundedString(state.pageTitle, MAX_TITLE_LENGTH),
    pageDisplayUrl: sanitizedDisplayUrl(state.pageUrl),
    startedAt: state.startedAt,
    candidates,
  };
}

async function injectDetector(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["detector.js"],
    });
    return "";
  } catch {
    return "Rendered-page scanning was unavailable, but network detection is still active.";
  }
}

async function stopInjectedDetector(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => {
        if (globalThis.__localWebVideoDownloaderDetector) {
          globalThis.__localWebVideoDownloaderDetector.stop();
        }
      },
    });
  } catch {
    // The page may already have navigated or closed.
  }
}

async function startDetection(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const pageUrl = normalizeHttpUrl(tab.url || "");
  if (!tab.active || !pageUrl) {
    throw new Error("Open a normal HTTP or HTTPS page in the active tab first.");
  }
  const now = Date.now();
  return enqueueTab(tabId, async () => {
    await writeState(tabId, {
      version: STATE_VERSION,
      enabled: true,
      pageUrl,
      pageTitle: boundedString(tab.title || "", MAX_TITLE_LENGTH),
      startedAt: now,
      updatedAt: now,
      frames: { "0": pageUrl },
      candidates: [],
    });
    await setDetectionBadge(tabId, true);
    const warning = await injectDetector(tabId);
    return { ...publicState(await readState(tabId)), warning };
  });
}

async function stopDetection(tabId) {
  return enqueueTab(tabId, async () => {
    await removeState(tabId);
    await setDetectionBadge(tabId, false);
    await stopInjectedDetector(tabId);
    return publicState(null);
  });
}

async function clearCandidates(tabId) {
  return enqueueTab(tabId, async () => {
    const state = await readState(tabId);
    if (!state) {
      return publicState(null);
    }
    state.candidates = [];
    state.updatedAt = Date.now();
    await writeState(tabId, state);
    return publicState(state);
  });
}

function sendNativeMessage(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, (response) => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        reject(new Error("The desktop bridge is unavailable. Run the desktop setup again."));
        return;
      }
      if (!response || response.ok === false) {
        reject(new Error("The desktop application did not accept this media source."));
        return;
      }
      resolve(response);
    });
  });
}

async function openCandidate(tabId, candidateId) {
  const state = await readState(tabId);
  if (!state) {
    throw new Error("Detection is no longer active for this tab.");
  }
  const candidate = state.candidates.find((item) => item.id === candidateId);
  if (!candidate) {
    throw new Error("That media source is no longer available. Detect it again.");
  }
  const payload = {
    version: 1,
    command: "open_capture",
    capture: {
      url: candidate.url,
      kind: candidate.kind,
      page_url: state.pageUrl,
      frame_url: candidate.frameUrl || state.pageUrl,
      content_type: candidate.contentType || "",
    },
  };
  await sendNativeMessage(payload);
  await enqueueTab(tabId, async () => {
    const latest = await readState(tabId);
    if (!latest) {
      return;
    }
    latest.candidates = latest.candidates.filter((item) => item.id !== candidateId);
    latest.updatedAt = Date.now();
    await writeState(tabId, latest);
  });
  return { ok: true };
}

function isExtensionPageSender(sender) {
  return !sender.tab && typeof sender.url === "string" && sender.url.startsWith(chrome.runtime.getURL(""));
}

async function handleMessage(message, sender) {
  if (!message || typeof message.type !== "string") {
    throw new Error("Invalid extension message.");
  }

  if (message.type === MESSAGE.DISCOVERED) {
    const tabId = sender.tab && sender.tab.id;
    if (!Number.isInteger(tabId) || !Array.isArray(message.candidates)) {
      throw new Error("Invalid detector message.");
    }
    const frameUrl = normalizeHttpUrl(sender.url || message.frameUrl || "");
    const frameId = Number.isInteger(sender.frameId) ? sender.frameId : -1;
    if (frameId === 0) {
      await enqueueTab(tabId, async () => {
        const state = await readState(tabId);
        if (!state) {
          return;
        }
        state.pageUrl = frameUrl || state.pageUrl;
        state.pageTitle = boundedString(message.pageTitle || state.pageTitle, MAX_TITLE_LENGTH);
        state.frames["0"] = state.pageUrl;
        await writeState(tabId, state);
      });
    }
    await addCandidates(tabId, message.candidates, {
      source: message.source === "performance" ? "performance" : "dom",
      frameUrl,
      frameId,
    });
    return { ok: true };
  }

  if (!isExtensionPageSender(sender)) {
    throw new Error("Control messages are accepted only from the extension interface.");
  }
  const tabId = Number(message.tabId);
  if (!Number.isInteger(tabId) || tabId < 0) {
    throw new Error("The active tab could not be identified.");
  }

  switch (message.type) {
    case MESSAGE.START:
      return startDetection(tabId);
    case MESSAGE.STOP:
      return stopDetection(tabId);
    case MESSAGE.CLEAR:
      return clearCandidates(tabId);
    case MESSAGE.STATUS:
      return publicState(await readState(tabId));
    case MESSAGE.OPEN:
      return openCandidate(tabId, boundedString(message.candidateId, 128));
    default:
      throw new Error("Unknown extension message.");
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => {
      const safeMessage = boundedString(error && error.message, 240) || "The operation failed.";
      sendResponse({ ok: false, error: safeMessage.replace(/https?:\/\/\S+/gi, "[redacted URL]") });
    });
  return true;
});

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0 || (details.method && details.method !== "GET")) {
      return;
    }
    if (details.type === "main_frame") {
      const navigationUrl = normalizeHttpUrl(details.url);
      if (!navigationUrl) {
        return;
      }
      void enqueueTab(details.tabId, async () => {
        const state = await readState(details.tabId);
        if (!state) {
          return;
        }
        if (!sameOrigin(state.pageUrl, navigationUrl)) {
          await removeState(details.tabId);
          await setDetectionBadge(details.tabId, false);
          return;
        }
        state.pageUrl = navigationUrl;
        state.pageTitle = "";
        state.frames = { "0": navigationUrl };
        state.candidates = [];
        state.updatedAt = Date.now();
        await writeState(details.tabId, state);
      }).catch(() => undefined);
      return;
    }
    void considerNetworkEvent(details).catch(() => undefined);
  },
  {
    urls: ["http://*/*", "https://*/*"],
    types: ["main_frame", "sub_frame", "media", "xmlhttprequest", "other"],
  },
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0 || (details.method && details.method !== "GET")) {
      return;
    }
    void considerNetworkEvent(details, details.responseHeaders || []).catch(() => undefined);
  },
  {
    urls: ["http://*/*", "https://*/*"],
    types: ["sub_frame", "media", "xmlhttprequest", "other"],
  },
  ["responseHeaders"],
);

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete") {
    return;
  }
  void enqueueTab(tabId, async () => {
    const state = await readState(tabId);
    if (!state) {
      return;
    }
    await injectDetector(tabId);
  }).catch(() => undefined);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void enqueueTab(tabId, async () => removeState(tabId)).catch(() => undefined);
});
