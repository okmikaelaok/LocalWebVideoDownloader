"use strict";

const MESSAGE = Object.freeze({
  START: "IFD_START_DETECTION",
  STOP: "IFD_STOP_DETECTION",
  CLEAR: "IFD_CLEAR_CANDIDATES",
  STATUS: "IFD_GET_STATUS",
  OPEN: "IFD_OPEN_CANDIDATE",
});

const OPTIONAL_ORIGINS = ["http://*/*", "https://*/*"];
const KIND_LABELS = Object.freeze({
  hls: "HLS manifest",
  dash: "DASH manifest",
  direct: "Direct video",
  media: "Browser media",
  embed: "Embedded player",
});

let activeTabId = null;
let selectedCandidateId = "";
let busy = false;
let pollTimer = 0;
let statusHoldUntil = 0;

const elements = {};

function bindElements() {
  elements.pageLabel = document.querySelector("#page-label");
  elements.stateBadge = document.querySelector("#state-badge");
  elements.status = document.querySelector("#status");
  elements.startButton = document.querySelector("#start-button");
  elements.stopButton = document.querySelector("#stop-button");
  elements.clearButton = document.querySelector("#clear-button");
  elements.candidateCount = document.querySelector("#candidate-count");
  elements.emptyMessage = document.querySelector("#empty-message");
  elements.candidateList = document.querySelector("#candidate-list");
  elements.openButton = document.querySelector("#open-button");
}

function safeMessage(value, fallback) {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  return value.replace(/https?:\/\/\S+/gi, "[redacted URL]").slice(0, 240);
}

function setStatus(message, isError = false, holdMilliseconds = isError ? 6000 : 0) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", isError);
  statusHoldUntil = holdMilliseconds > 0 ? Date.now() + holdMilliseconds : 0;
}

function setBusy(value) {
  busy = value;
  for (const button of [
    elements.startButton,
    elements.stopButton,
    elements.clearButton,
    elements.openButton,
  ]) {
    button.classList.toggle("working", value);
  }
}

function sendMessage(type, extras = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type, tabId: activeTabId, ...extras }, (response) => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        reject(new Error("The extension service is unavailable. Reopen this popup."));
        return;
      }
      if (!response || !response.ok) {
        reject(new Error(safeMessage(response && response.error, "The operation failed.")));
        return;
      }
      resolve(response.result);
    });
  });
}

function candidateRow(candidate) {
  const wrapper = document.createElement("label");
  wrapper.className = "candidate";

  const radio = document.createElement("input");
  radio.type = "radio";
  radio.name = "candidate";
  radio.value = candidate.id;
  radio.checked = candidate.id === selectedCandidateId;
  radio.addEventListener("change", () => {
    selectedCandidateId = candidate.id;
    elements.openButton.disabled = busy || !selectedCandidateId;
  });

  const content = document.createElement("span");
  content.className = "candidate-content";

  const heading = document.createElement("span");
  heading.className = "candidate-heading";
  heading.textContent = KIND_LABELS[candidate.kind] || "Media source";

  const address = document.createElement("span");
  address.className = "candidate-address";
  address.textContent = candidate.displayUrl;

  const detail = document.createElement("span");
  detail.className = "candidate-detail";
  const source = candidate.source === "network" ? "network" : "rendered page";
  detail.textContent = `${source}${candidate.contentType ? ` · ${candidate.contentType}` : ""}`;

  content.append(heading, address, detail);
  wrapper.append(radio, content);
  return wrapper;
}

function render(state) {
  const enabled = Boolean(state && state.enabled);
  const candidates = state && Array.isArray(state.candidates) ? state.candidates : [];
  if (!candidates.some((candidate) => candidate.id === selectedCandidateId)) {
    selectedCandidateId = "";
  }

  elements.pageLabel.textContent = state.pageTitle || state.pageDisplayUrl || "Active tab";
  elements.pageLabel.title = state.pageDisplayUrl || "";
  elements.stateBadge.textContent = enabled ? "On" : "Off";
  elements.stateBadge.classList.toggle("active", enabled);
  elements.candidateCount.textContent = String(candidates.length);
  elements.candidateList.replaceChildren(...candidates.map(candidateRow));
  elements.candidateList.hidden = candidates.length === 0;
  elements.emptyMessage.hidden = candidates.length > 0;

  elements.startButton.disabled = busy || enabled;
  elements.stopButton.disabled = busy || !enabled;
  elements.clearButton.disabled = busy || candidates.length === 0;
  elements.openButton.disabled = busy || !selectedCandidateId;

  if (!busy && Date.now() >= statusHoldUntil) {
    if (!enabled) {
      setStatus("Start detection, then play the video in this tab.");
    } else if (candidates.length === 0) {
      setStatus("Listening. Play or reload the video to reveal its media request.");
    } else {
      setStatus(
        `Found ${candidates.length} source${candidates.length === 1 ? "" : "s"}. Select one to inspect.`,
      );
    }
  }
}

async function refresh() {
  if (busy || activeTabId === null) {
    return;
  }
  try {
    render(await sendMessage(MESSAGE.STATUS));
  } catch (error) {
    setStatus(safeMessage(error.message, "Unable to read detection status."), true);
  }
}

async function startDetection() {
  if (busy) {
    return;
  }
  setBusy(true);
  setStatus("Requesting access needed to see third-party player and CDN requests…");
  let succeeded = false;
  let warning = "";
  try {
    const granted = await chrome.permissions.request({ origins: OPTIONAL_ORIGINS });
    if (!granted) {
      throw new Error("Media detection needs the requested website access.");
    }
    const state = await sendMessage(MESSAGE.START);
    render(state);
    warning = state.warning || "";
    succeeded = true;
  } catch (error) {
    setStatus(safeMessage(error.message, "Detection could not start."), true);
  } finally {
    setBusy(false);
    if (succeeded) {
      await refresh();
      if (warning) {
        setStatus(warning, true);
      }
    }
  }
}

async function stopDetection() {
  if (busy) {
    return;
  }
  setBusy(true);
  let succeeded = false;
  try {
    selectedCandidateId = "";
    render(await sendMessage(MESSAGE.STOP));
    succeeded = true;
  } catch (error) {
    setStatus(safeMessage(error.message, "Detection could not stop."), true);
  } finally {
    setBusy(false);
    if (succeeded) {
      await refresh();
    }
  }
}

async function clearCandidates() {
  if (busy) {
    return;
  }
  setBusy(true);
  let succeeded = false;
  try {
    selectedCandidateId = "";
    render(await sendMessage(MESSAGE.CLEAR));
    succeeded = true;
  } catch (error) {
    setStatus(safeMessage(error.message, "Detected sources could not be cleared."), true);
  } finally {
    setBusy(false);
    if (succeeded) {
      await refresh();
    }
  }
}

async function openCandidate() {
  if (busy || !selectedCandidateId) {
    return;
  }
  setBusy(true);
  setStatus("Connecting to the desktop application…");
  let succeeded = false;
  try {
    const granted = await chrome.permissions.request({ permissions: ["nativeMessaging"] });
    if (!granted) {
      throw new Error("Opening the desktop application needs native messaging permission.");
    }
    await sendMessage(MESSAGE.OPEN, { candidateId: selectedCandidateId });
    selectedCandidateId = "";
    succeeded = true;
  } catch (error) {
    setStatus(safeMessage(error.message, "The desktop application could not be opened."), true);
  } finally {
    setBusy(false);
    if (succeeded) {
      await refresh();
      setStatus("Sent to the desktop application.", false, 3000);
    }
  }
}

async function initialize() {
  bindElements();
  elements.startButton.addEventListener("click", startDetection);
  elements.stopButton.addEventListener("click", stopDetection);
  elements.clearButton.addEventListener("click", clearCandidates);
  elements.openButton.addEventListener("click", openCandidate);

  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || !Number.isInteger(tab.id)) {
    setStatus("No active browser tab is available.", true);
    elements.startButton.disabled = true;
    return;
  }
  activeTabId = tab.id;
  await refresh();
  pollTimer = globalThis.setInterval(refresh, 1200);
}

globalThis.addEventListener("unload", () => {
  if (pollTimer) {
    globalThis.clearInterval(pollTimer);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  void initialize().catch((error) => {
    setStatus(safeMessage(error.message, "The extension could not initialize."), true);
  });
});
