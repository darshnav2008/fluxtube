const dropZoneBar = document.getElementById("dropZoneBar");
const videoFileInput = document.getElementById("videoFileInput");
const pickFileBtn = document.getElementById("pickFileBtn");
const dropFileLabel = document.getElementById("dropFileLabel");
const errorBanner = document.getElementById("errorBanner");
const studioWorkspace = document.getElementById("studioWorkspace");

const videoPreview = document.getElementById("videoPreview");
const videoDuration = document.getElementById("videoDuration");
const videoResolutionBadge = document.getElementById("videoPlatform");
const videoTitle = document.getElementById("videoTitle");
const grabFrameBtn = document.getElementById("grabFrameBtn");
const frameToast = document.getElementById("frameToast");

// Direct Time Trimmer Elements
const clipDurationBadge = document.getElementById("clipDurationBadge");
const startSlider = document.getElementById("startSlider");
const endSlider = document.getElementById("endSlider");
const rangeTrackFill = document.getElementById("rangeTrackFill");
const startTimeInput = document.getElementById("startTimeInput");
const endTimeInput = document.getElementById("endTimeInput");
const resetTrimBtn = document.getElementById("resetTrimBtn");

const qualityGrid = document.getElementById("qualityGrid");
const selectedSummaryText = document.getElementById("selectedSummaryText");
const downloadBtn = document.getElementById("downloadBtn");
const downloadBtnLabel = document.getElementById("downloadBtnLabel");

const progressHud = document.getElementById("progressHud");
const progressStage = document.getElementById("progressStage");
const progressPct = document.getElementById("progressPct");
const progressFill = document.getElementById("progressFill");
const progressSpeed = document.getElementById("progressSpeed");
const progressEta = document.getElementById("progressEta");
const saveFileBtn = document.getElementById("saveFileBtn");

const historyToggle = document.getElementById("historyToggle");
const historyDrawer = document.getElementById("historyDrawer");
const drawerBackdrop = document.getElementById("drawerBackdrop");
const closeDrawerBtn = document.getElementById("closeDrawerBtn");
const clearHistoryBtn = document.getElementById("clearHistoryBtn");
const historyList = document.getElementById("historyList");
const historyCount = document.getElementById("historyCount");

const MAX_FILE_BYTES = 150 * 1024 * 1024;

const OUTPUT_MODES = [
  {
    id: "trim_mp4",
    type: "video",
    badge: "LOSSLESS",
    ext: "MP4",
    resolution: "Fast Clip",
    title: "Exact Cut • Original Quality",
    ratio: 1.0,
  },
  {
    id: "audio_mp3",
    type: "audio",
    badge: "320K AUDIO",
    ext: "MP3",
    resolution: "Audio MP3",
    title: "Studio High-Bitrate Sound",
    ratio: 0.14,
  },
  {
    id: "audio_m4a",
    type: "audio",
    badge: "AAC",
    ext: "M4A",
    resolution: "Audio M4A",
    title: "Compact Apple / Web Audio",
    ratio: 0.09,
  },
  {
    id: "compress_720",
    type: "video",
    badge: "SMALLER",
    ext: "MP4",
    resolution: "720p HD",
    title: "Web & WhatsApp Friendly",
    ratio: 0.45,
  },
  {
    id: "compress_480",
    type: "video",
    badge: "TINY",
    ext: "MP4",
    resolution: "480p Lite",
    title: "Ultra-Compact Sharing",
    ratio: 0.25,
  },
  {
    id: "mute_video",
    type: "video",
    badge: "SILENT",
    ext: "MP4",
    resolution: "Mute Video",
    title: "Remove Audio Track Only",
    ratio: 0.9,
  },
];

let currentFile = null;
let currentObjectUrl = null;
let currentDuration = 0;
let selectedMode = OUTPUT_MODES[0];
let activeFilter = "all";

// Mouse-tracking radial spotlight
document.addEventListener("mousemove", (e) => {
  document.querySelectorAll(".spotlight-card").forEach((card) => {
    const rect = card.getBoundingClientRect();
    card.style.setProperty("--mouse-x", `${e.clientX - rect.left}px`);
    card.style.setProperty("--mouse-y", `${e.clientY - rect.top}px`);
  });
});

function formatSeconds(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function parseTimeString(str) {
  if (!str) return null;
  const clean = String(str).trim();
  if (/^\d+(\.\d+)?$/.test(clean)) return Math.max(0, Math.floor(Number(clean)));
  const parts = clean.split(":").map((p) => Number(p));
  if (parts.some((n) => Number.isNaN(n) || n < 0)) return null;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return null;
}

function formatBytesJS(bytes) {
  if (!bytes || bytes <= 0) return "Auto";
  const units = ["B", "KB", "MB", "GB"];
  let val = bytes;
  for (const u of units) {
    if (val < 1024) {
      return u === "MB" || u === "GB" ? `${val.toFixed(1)} ${u}` : `${Math.round(val)} ${u}`;
    }
    val /= 1024;
  }
  return `${val.toFixed(1)} TB`;
}

function showError(msg) {
  errorBanner.textContent = msg;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

// File Picker & Drag-and-Drop
pickFileBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  videoFileInput.click();
});

dropZoneBar.addEventListener("click", () => {
  videoFileInput.click();
});

videoFileInput.addEventListener("change", () => {
  if (videoFileInput.files && videoFileInput.files[0]) {
    loadVideoFile(videoFileInput.files[0]);
  }
});

["dragenter", "dragover"].forEach((evt) => {
  dropZoneBar.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZoneBar.classList.add("drag-over");
  });
});

["dragleave", "drop"].forEach((evt) => {
  dropZoneBar.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZoneBar.classList.remove("drag-over");
  });
});

dropZoneBar.addEventListener("drop", (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) loadVideoFile(file);
});

function loadVideoFile(file) {
  clearError();
  if (!file.type.startsWith("video/") && !/\.(mp4|mov|webm|mkv|m4v|avi)$/i.test(file.name)) {
    showError("Please select a valid video file (MP4, MOV, WebM, MKV).");
    return;
  }
  if (file.size > MAX_FILE_BYTES) {
    showError("File is over 150 MB. Please choose a smaller video.");
    return;
  }

  currentFile = file;
  if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
  currentObjectUrl = URL.createObjectURL(file);

  dropFileLabel.textContent = `${file.name} (${formatBytesJS(file.size)})`;
  const pickLabel = pickFileBtn.querySelector(".btn-label");
  if (pickLabel) pickLabel.textContent = "Change Video";
  videoTitle.textContent = file.name;
  progressHud.hidden = true;
  saveFileBtn.hidden = true;
  if (frameToast) frameToast.hidden = true;

  videoPreview.src = currentObjectUrl;
  videoPreview.onloadedmetadata = () => {
    currentDuration = Math.max(1, Math.floor(videoPreview.duration || 1));
    videoDuration.textContent = formatSeconds(currentDuration);
    const w = videoPreview.videoWidth;
    const h = videoPreview.videoHeight;
    videoResolutionBadge.textContent =
      w && h ? `${w}×${h} • ${formatBytesJS(file.size)}` : formatBytesJS(file.size);

    initTrimmer(currentDuration);
    renderModes();
    updateSelectedSummary();

    const introBento = document.getElementById("introBento");
    const heroTop = document.getElementById("heroTop");
    const heroBottom = document.getElementById("heroBottom");
    const heroSection = document.getElementById("heroSection");
    if (introBento) introBento.hidden = true;
    if (heroTop) heroTop.hidden = true;
    if (heroBottom) heroBottom.hidden = true;
    if (heroSection) heroSection.classList.add("compact-mode");
    document.body.classList.add("workspace-open");
    studioWorkspace.hidden = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
}

// Instant Client-Side Full-Res Frame Grabber (Zero Server Load)
grabFrameBtn.addEventListener("click", () => {
  if (!currentFile || !videoPreview.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = videoPreview.videoWidth;
  canvas.height = videoPreview.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(videoPreview, 0, 0, canvas.width, canvas.height);

  const ts = formatSeconds(videoPreview.currentTime).replace(/:/g, "-");
  const baseName = currentFile.name.replace(/\.[^/.]+$/, "");
  const link = document.createElement("a");
  link.download = `${baseName}_frame_${ts}.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();

  if (frameToast) {
    frameToast.textContent = `Saved ${canvas.width}×${canvas.height} PNG at ${formatSeconds(videoPreview.currentTime)}`;
    frameToast.hidden = false;
  }
});

// Filter Tabs
document.querySelectorAll(".filter-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    activeFilter = btn.dataset.filter;
    document.querySelectorAll(".filter-tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.filter === activeFilter);
    });
    renderModes();
  });
});

// Direct Time Trimmer
function isTrimmed() {
  if (!currentDuration) return false;
  return Number(startSlider.value) > 0 || Number(endSlider.value) < currentDuration;
}

function initTrimmer(duration) {
  const maxDur = Math.max(1, Math.floor(duration || 60));
  startSlider.min = 0;
  startSlider.max = maxDur;
  startSlider.value = 0;

  endSlider.min = 0;
  endSlider.max = maxDur;
  endSlider.value = maxDur;

  startTimeInput.value = formatSeconds(0);
  endTimeInput.value = formatSeconds(maxDur);
  updateTrimmerUI(false);
}

function updateTrimmerUI(seekTarget) {
  if (!currentDuration) return;
  const maxDur = Math.max(1, Math.floor(currentDuration));
  let s = Number(startSlider.value);
  let e = Number(endSlider.value);

  if (s >= e) {
    s = Math.max(0, e - 1);
    startSlider.value = s;
  }

  const leftPct = (s / maxDur) * 100;
  const rightPct = (e / maxDur) * 100;
  rangeTrackFill.style.left = `${leftPct}%`;
  rangeTrackFill.style.width = `${Math.max(0, rightPct - leftPct)}%`;

  startTimeInput.value = formatSeconds(s);
  endTimeInput.value = formatSeconds(e);

  const clipLen = Math.max(1, e - s);
  clipDurationBadge.textContent = isTrimmed()
    ? `${formatSeconds(clipLen)} clip`
    : `Full (${formatSeconds(maxDur)})`;

  if (seekTarget === "start") videoPreview.currentTime = s;
  if (seekTarget === "end") videoPreview.currentTime = e;

  renderModes();
  updateSelectedSummary();
}

startSlider.addEventListener("input", () => {
  if (Number(startSlider.value) >= Number(endSlider.value)) {
    startSlider.value = Math.max(0, Number(endSlider.value) - 1);
  }
  updateTrimmerUI("start");
});

endSlider.addEventListener("input", () => {
  if (Number(endSlider.value) <= Number(startSlider.value)) {
    endSlider.value = Math.min(Number(endSlider.max), Number(startSlider.value) + 1);
  }
  updateTrimmerUI("end");
});

startTimeInput.addEventListener("change", () => {
  const parsed = parseTimeString(startTimeInput.value);
  if (parsed !== null) {
    startSlider.value = Math.min(Math.max(0, parsed), Number(endSlider.value) - 1);
  }
  updateTrimmerUI("start");
});

endTimeInput.addEventListener("change", () => {
  const parsed = parseTimeString(endTimeInput.value);
  if (parsed !== null) {
    endSlider.value = Math.max(Number(startSlider.value) + 1, Math.min(currentDuration, parsed));
  }
  updateTrimmerUI("end");
});

resetTrimBtn.addEventListener("click", () => {
  if (currentDuration) initTrimmer(currentDuration);
});

function estimateModeSize(mode) {
  if (!currentFile || !currentDuration) return "Auto";
  const clipLen = Math.max(1, Number(endSlider.value) - Number(startSlider.value));
  const timeRatio = Math.min(1, clipLen / currentDuration);
  return `~${formatBytesJS(currentFile.size * timeRatio * mode.ratio)}`;
}

function renderModes() {
  const list = OUTPUT_MODES.filter((m) =>
    activeFilter === "all" ? true : m.type === activeFilter
  );

  qualityGrid.innerHTML = "";
  list.forEach((m) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `quality-card${selectedMode && selectedMode.id === m.id ? " selected" : ""}`;
    card.innerHTML = `
      <div class="q-top">
        <span class="q-badge">${m.badge}</span>
        <span class="q-ext">${m.ext}</span>
      </div>
      <div class="q-res">${m.resolution}</div>
      <div class="q-bottom">
        <span>${m.title}</span>
        <span>${estimateModeSize(m)}</span>
      </div>
    `;
    card.addEventListener("click", () => {
      selectedMode = m;
      renderModes();
      updateSelectedSummary();
    });
    qualityGrid.appendChild(card);
  });
}

function updateSelectedSummary() {
  if (!selectedMode) return;
  selectedSummaryText.textContent = `${selectedMode.resolution} ${selectedMode.ext} (${estimateModeSize(selectedMode)})`;
  downloadBtnLabel.textContent = isTrimmed() ? "Export Clip" : "Export File";
}

// Process & Download via XHR (real upload progress)
downloadBtn.addEventListener("click", () => {
  if (!currentFile || !selectedMode) return;

  downloadBtn.disabled = true;
  progressHud.hidden = false;
  saveFileBtn.hidden = true;
  progressStage.textContent = "Uploading video...";
  progressPct.textContent = "0%";
  progressFill.style.width = "0%";
  progressSpeed.textContent = `Input: ${formatBytesJS(currentFile.size)}`;
  progressEta.textContent = selectedMode.ext;

  const formData = new FormData();
  formData.append("file", currentFile);
  formData.append("mode", selectedMode.id);
  formData.append("start_time", startTimeInput.value);
  formData.append("end_time", endTimeInput.value);
  formData.append("duration", String(currentDuration));

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/process", true);

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.min(70, Math.round((e.loaded / e.total) * 70));
      progressPct.textContent = `${pct}%`;
      progressFill.style.width = `${pct}%`;
      if (pct >= 70) {
        progressStage.textContent = isTrimmed()
          ? `Clipping ${startTimeInput.value} → ${endTimeInput.value}...`
          : `Processing ${selectedMode.resolution}...`;
      }
    }
  };

  xhr.onload = () => {
    downloadBtn.disabled = false;
    let data = {};
    try {
      data = JSON.parse(xhr.responseText || "{}");
    } catch {
      data = {};
    }

    if (xhr.status >= 200 && xhr.status < 300 && data.download_url) {
      progressPct.textContent = "100%";
      progressFill.style.width = "100%";
      progressStage.textContent = `Done (${data.actual_size || "Ready"})`;
      progressSpeed.textContent = `Output: ${data.actual_size || "—"}`;
      progressEta.textContent = "Saved";

      saveFileBtn.href = data.download_url;
      saveFileBtn.hidden = false;

      const a = document.createElement("a");
      a.href = data.download_url;
      a.download = data.filename || "fluxtube_output.mp4";
      document.body.appendChild(a);
      a.click();
      a.remove();

      saveToHistory({
        name: data.filename,
        mode: `${selectedMode.resolution} • ${data.actual_size || ""}`,
      });
    } else {
      progressStage.textContent = `Error: ${data.error || "Processing failed"}`;
    }
  };

  xhr.onerror = () => {
    downloadBtn.disabled = false;
    progressStage.textContent = "Network error while uploading. Please try again.";
  };

  xhr.send(formData);
});

// History Drawer
const HISTORY_KEY = "fluxtube_studio_history_v1";

function getHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveToHistory(entry) {
  const items = getHistory();
  items.unshift({
    name: entry.name,
    mode: entry.mode,
    time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 12)));
  renderHistory();
}

function renderHistory() {
  const items = getHistory();
  historyCount.textContent = items.length;
  if (!items.length) {
    historyList.innerHTML = `<p class="drawer-empty">No exports yet.</p>`;
    return;
  }

  historyList.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";
    row.innerHTML = `
      <div>
        <div class="history-item-title">${item.name}</div>
        <div class="history-item-sub">${item.mode} • ${item.time}</div>
      </div>
    `;
    historyList.appendChild(row);
  });
}

function toggleDrawer(open) {
  historyDrawer.classList.toggle("open", open);
  historyDrawer.setAttribute("aria-hidden", String(!open));
  drawerBackdrop.hidden = !open;
}

historyToggle.addEventListener("click", () => toggleDrawer(true));
closeDrawerBtn.addEventListener("click", () => toggleDrawer(false));
drawerBackdrop.addEventListener("click", () => toggleDrawer(false));
clearHistoryBtn.addEventListener("click", () => {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory();
});

renderHistory();
