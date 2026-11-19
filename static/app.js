const analyzeForm = document.getElementById("analyzeForm");
const urlInput = document.getElementById("urlInput");
const pasteBtn = document.getElementById("pasteBtn");
const analyzeBtn = document.getElementById("analyzeBtn");
const errorBanner = document.getElementById("errorBanner");
const studioWorkspace = document.getElementById("studioWorkspace");

const videoThumb = document.getElementById("videoThumb");
const videoDuration = document.getElementById("videoDuration");
const videoTitle = document.getElementById("videoTitle");
const engineBadge = document.getElementById("engineBadge");

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

// Alternate Fast Download Links
const altCobaltBtn = document.getElementById("altCobaltBtn");
const altPipedBtn = document.getElementById("altPipedBtn");
const altTenDownBtn = document.getElementById("altTenDownBtn");

// History Drawer
const historyToggle = document.getElementById("historyToggle");
const historyDrawer = document.getElementById("historyDrawer");
const drawerBackdrop = document.getElementById("drawerBackdrop");
const closeDrawerBtn = document.getElementById("closeDrawerBtn");
const clearHistoryBtn = document.getElementById("clearHistoryBtn");
const historyList = document.getElementById("historyList");
const historyCount = document.getElementById("historyCount");

// Settings Modal & Cloud Fix
const settingsToggle = document.getElementById("settingsToggle");
const settingsModal = document.getElementById("settingsModal");
const settingsBackdrop = document.getElementById("settingsBackdrop");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const navStatusDot = document.getElementById("navStatusDot");
const modalCookieStatus = document.getElementById("modalCookieStatus");
const modalProxyStatus = document.getElementById("modalProxyStatus");
const modalCookiesInput = document.getElementById("modalCookiesInput");
const modalProxyInput = document.getElementById("modalProxyInput");
const saveCookiesBtn = document.getElementById("saveCookiesBtn");
const saveProxyBtn = document.getElementById("saveProxyBtn");
const cookieSaveMsg = document.getElementById("cookieSaveMsg");
const proxySaveMsg = document.getElementById("proxySaveMsg");

let currentVideo = null;
let selectedQuality = null;
let activeFilter = "all";
let pollTimer = null;

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

pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      urlInput.value = text.trim();
      urlInput.focus();
    }
  } catch {
    urlInput.focus();
  }
});

document.querySelectorAll(".filter-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    activeFilter = btn.dataset.filter;
    document.querySelectorAll(".filter-tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.filter === activeFilter);
    });
    renderQualities();
  });
});

analyzeForm.addEventListener("submit", (e) => {
  e.preventDefault();
  inspectUrl(urlInput.value.trim());
});

function showError(msg) {
  errorBanner.innerHTML = "";
  const span = document.createElement("span");
  span.textContent = msg;
  errorBanner.appendChild(span);

  if (msg.includes("restricted") || msg.includes("Cookies") || msg.includes("bot")) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-open-fix";
    btn.textContent = "Open Cloud Setup ⚙️";
    btn.style.marginLeft = "12px";
    btn.style.padding = "4px 10px";
    btn.style.borderRadius = "6px";
    btn.style.background = "var(--accent-grad)";
    btn.style.color = "#000";
    btn.style.border = "none";
    btn.style.fontWeight = "700";
    btn.style.cursor = "pointer";
    btn.addEventListener("click", () => toggleSettingsModal(true));
    errorBanner.appendChild(btn);
  }

  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.innerHTML = "";
}

function setInspectLoading(loading) {
  analyzeBtn.disabled = loading;
  analyzeBtn.querySelector(".btn-label").textContent = loading ? "Loading..." : "Fetch →";
  analyzeBtn.querySelector(".btn-spinner").hidden = !loading;
}

function isTrimmed() {
  if (!currentVideo) return false;
  const maxDur = Math.max(1, Math.floor(currentVideo.duration || 60));
  return Number(startSlider.value) > 0 || Number(endSlider.value) < maxDur;
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

  updateTrimmerUI();
}

function updateTrimmerUI() {
  if (!currentVideo) return;
  const maxDur = Math.max(1, Math.floor(currentVideo.duration || 60));
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

  renderQualities();
  updateSelectedSummary();
}

startSlider.addEventListener("input", () => {
  if (Number(startSlider.value) >= Number(endSlider.value)) {
    startSlider.value = Math.max(0, Number(endSlider.value) - 1);
  }
  updateTrimmerUI();
});

endSlider.addEventListener("input", () => {
  if (Number(endSlider.value) <= Number(startSlider.value)) {
    endSlider.value = Math.min(Number(endSlider.max), Number(startSlider.value) + 1);
  }
  updateTrimmerUI();
});

startTimeInput.addEventListener("change", () => {
  if (!currentVideo) return;
  const parsed = parseTimeString(startTimeInput.value);
  if (parsed !== null) {
    startSlider.value = Math.min(Math.max(0, parsed), Number(endSlider.value) - 1);
  }
  updateTrimmerUI();
});

endTimeInput.addEventListener("change", () => {
  if (!currentVideo) return;
  const maxDur = Math.max(1, Math.floor(currentVideo.duration || 60));
  const parsed = parseTimeString(endTimeInput.value);
  if (parsed !== null) {
    endSlider.value = Math.max(Number(startSlider.value) + 1, Math.min(maxDur, parsed));
  }
  updateTrimmerUI();
});

resetTrimBtn.addEventListener("click", () => {
  if (!currentVideo) return;
  initTrimmer(currentVideo.duration);
});

function getEffectiveSizeLabel(q) {
  if (!currentVideo || !q.size_bytes || !currentVideo.duration) return q.size;
  if (!isTrimmed()) return q.size;
  const clipLen = Math.max(1, Number(endSlider.value) - Number(startSlider.value));
  const ratio = Math.min(1, clipLen / currentVideo.duration);
  return formatBytesJS(q.size_bytes * ratio);
}

function updateAlternateLinks(video) {
  const encUrl = encodeURIComponent(video.webpage_url || "");
  const vidId = video.id || "";

  if (altCobaltBtn) {
    altCobaltBtn.href = `https://cobalt.tools/`;
    altCobaltBtn.title = "Open Cobalt fast downloader";
  }
  if (altPipedBtn) {
    altPipedBtn.href = vidId ? `https://piped.video/watch?v=${vidId}` : `https://piped.video/`;
    altPipedBtn.title = "Watch or stream via Piped without ads";
  }
  if (altTenDownBtn) {
    altTenDownBtn.href = `https://10downloader.com/download?v=${encUrl}`;
    altTenDownBtn.title = "Instant browser download via 10Downloader";
  }
}

async function inspectUrl(url) {
  if (!url) return;
  clearError();
  setInspectLoading(true);
  progressHud.hidden = true;
  saveFileBtn.hidden = true;

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) {
      showError(data.error || "Failed to fetch this video link.");
      return;
    }

    currentVideo = data;
    videoThumb.src = data.thumbnail || "";
    videoDuration.textContent = data.duration_string || "0:00";
    videoTitle.textContent = data.title;
    const platEl = document.getElementById("videoPlatform");
    if (platEl) platEl.textContent = data.platform || "Video";

    if (engineBadge) {
      if (data.engine === "fallback") {
        engineBadge.textContent = "⚡ Fallback Engine";
        engineBadge.classList.add("fallback");
        engineBadge.title = "Fetched via cloud failover pipeline (Invidious / Piped)";
      } else {
        engineBadge.textContent = "Native Engine";
        engineBadge.classList.remove("fallback");
        engineBadge.title = "Fetched via high-speed native engine";
      }
    }

    updateAlternateLinks(data);

    selectedQuality =
      data.qualities.find((q) => q.id === "video_1080") || data.qualities[0] || null;

    initTrimmer(data.duration);
    renderQualities();
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

    saveToHistory(data);
  } catch {
    showError("Network error while contacting download server.");
  } finally {
    setInspectLoading(false);
  }
}

function renderQualities() {
  if (!currentVideo) return;
  const list = currentVideo.qualities.filter((q) =>
    activeFilter === "all" ? true : q.type === activeFilter
  );

  qualityGrid.innerHTML = "";
  list.forEach((q) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `quality-card${selectedQuality && selectedQuality.id === q.id ? " selected" : ""}`;
    const sizeLabel = getEffectiveSizeLabel(q);
    card.innerHTML = `
      <div class="q-top">
        <span class="q-badge">${q.badge}</span>
        <span class="q-ext">${q.ext}</span>
      </div>
      <div class="q-res">${q.resolution}</div>
      <div class="q-bottom">
        <span>${q.title}</span>
        <span>${sizeLabel}</span>
      </div>
    `;
    card.addEventListener("click", () => {
      selectedQuality = q;
      renderQualities();
      updateSelectedSummary();
    });
    qualityGrid.appendChild(card);
  });
}

function updateSelectedSummary() {
  if (!selectedQuality) return;
  const sizeLabel = getEffectiveSizeLabel(selectedQuality);
  selectedSummaryText.textContent = `${selectedQuality.resolution} ${selectedQuality.ext} (${sizeLabel})`;
  downloadBtnLabel.textContent = isTrimmed() ? "Download Clip" : "Download";
}

downloadBtn.addEventListener("click", async () => {
  if (!currentVideo || !selectedQuality) return;
  if (pollTimer) clearInterval(pollTimer);

  downloadBtn.disabled = true;
  progressHud.hidden = false;
  saveFileBtn.hidden = true;
  progressStage.textContent = isTrimmed()
    ? `Clipping ${startTimeInput.value} → ${endTimeInput.value}...`
    : "Starting download...";
  progressPct.textContent = "0%";
  progressFill.style.width = "0%";
  progressSpeed.textContent = "Speed: —";
  progressEta.textContent = "ETA: —";

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentVideo.webpage_url,
        quality_id: selectedQuality.id,
        format_id: selectedQuality.format_id || null,
        audio_id: selectedQuality.audio_id || null,
        title: currentVideo.title,
        duration: currentVideo.duration,
        start_time: startTimeInput.value,
        end_time: endTimeInput.value,
        engine: currentVideo.engine || "yt-dlp",
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.job_id) {
      progressStage.textContent = data.error || "Could not start download.";
      downloadBtn.disabled = false;
      return;
    }

    const jobId = data.job_id;
    pollTimer = setInterval(async () => {
      try {
        const pRes = await fetch(`/api/progress/${jobId}`);
        const p = await pRes.json();

        progressPct.textContent = `${Math.round(p.progress || 0)}%`;
        progressFill.style.width = `${p.progress || 0}%`;
        progressSpeed.textContent = `Speed: ${p.speed || "—"}`;
        progressEta.textContent = `ETA: ${p.eta || "—"}`;

        if (p.status === "downloading") {
          progressStage.textContent = isTrimmed()
            ? `Trimming ${startTimeInput.value} → ${endTimeInput.value}...`
            : "Downloading...";
        } else if (p.status === "processing") {
          progressStage.textContent = "Finalizing file...";
        } else if (p.status === "completed") {
          clearInterval(pollTimer);
          progressStage.textContent = p.actual_size
            ? `Done (${p.actual_size})`
            : "Done — saving file!";
          downloadBtn.disabled = false;
          const fileUrl = `/api/file/${jobId}`;
          saveFileBtn.href = fileUrl;
          saveFileBtn.hidden = false;

          const a = document.createElement("a");
          a.href = fileUrl;
          a.download = p.filename || "video.mp4";
          document.body.appendChild(a);
          a.click();
          a.remove();
        } else if (p.status === "error") {
          clearInterval(pollTimer);
          progressStage.textContent = `Error: ${p.error || "Download failed"}`;
          downloadBtn.disabled = false;
        }
      } catch {
        clearInterval(pollTimer);
        downloadBtn.disabled = false;
      }
    }, 700);
  } catch {
    progressStage.textContent = "Failed to connect to download server.";
    downloadBtn.disabled = false;
  }
});

// History Drawer Logic
const HISTORY_KEY = "fluxtube_history_v1";

function getHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveToHistory(video) {
  const items = getHistory().filter((item) => item.id !== video.id);
  items.unshift({
    id: video.id,
    title: video.title,
    thumbnail: video.thumbnail,
    uploader: video.uploader,
    duration_string: video.duration_string,
    webpage_url: video.webpage_url,
  });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 12)));
  renderHistory();
}

function renderHistory() {
  const items = getHistory();
  historyCount.textContent = items.length;
  if (!items.length) {
    historyList.innerHTML = `<p class="drawer-empty">No videos yet.</p>`;
    return;
  }

  historyList.innerHTML = "";
  items.forEach((item) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "history-item";
    btn.innerHTML = `
      <img src="${item.thumbnail || ""}" alt="" />
      <div>
        <div class="history-item-title">${item.title}</div>
        <div class="history-item-sub">${item.duration_string}</div>
      </div>
    `;
    btn.addEventListener("click", () => {
      toggleDrawer(false);
      urlInput.value = item.webpage_url;
      inspectUrl(item.webpage_url);
    });
    historyList.appendChild(btn);
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

// Settings & Cloud Fix Modal Logic
function toggleSettingsModal(open) {
  settingsModal.hidden = !open;
  settingsBackdrop.hidden = !open;
  if (open) {
    loadSettings();
  }
}

settingsToggle.addEventListener("click", () => toggleSettingsModal(true));
closeSettingsBtn.addEventListener("click", () => toggleSettingsModal(false));
settingsBackdrop.addEventListener("click", () => toggleSettingsModal(false));

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();

    if (modalCookieStatus) {
      if (data.has_cookies) {
        modalCookieStatus.textContent = "Loaded & Active ✓";
        modalCookieStatus.className = "status-col-val text-success";
      } else {
        modalCookieStatus.textContent = "Not Set (Fallback Active)";
        modalCookieStatus.className = "status-col-val";
      }
    }

    if (modalProxyStatus) {
      modalProxyStatus.textContent = data.active_proxy || "None";
    }

    if (navStatusDot) {
      if (data.has_cookies || data.has_proxy) {
        navStatusDot.className = "status-indicator-dot";
      } else {
        navStatusDot.className = "status-indicator-dot warning";
      }
    }
  } catch {
    // ignore
  }
}

saveCookiesBtn.addEventListener("click", async () => {
  const cookies = modalCookiesInput.value.trim();
  if (!cookies) return;

  saveCookiesBtn.disabled = true;
  saveCookiesBtn.textContent = "Saving...";

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies }),
    });
    const data = await res.json();
    cookieSaveMsg.textContent = "Cookies saved & activated! ✓";
    modalCookiesInput.value = "";
    loadSettings();
  } catch {
    cookieSaveMsg.textContent = "Failed to save cookies.";
  } finally {
    saveCookiesBtn.disabled = false;
    saveCookiesBtn.textContent = "Save & Activate Cookies";
  }
});

saveProxyBtn.addEventListener("click", async () => {
  const proxy = modalProxyInput.value.trim();

  saveProxyBtn.disabled = true;
  saveProxyBtn.textContent = "Saving...";

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy }),
    });
    const data = await res.json();
    proxySaveMsg.textContent = "Proxy updated! ✓";
    loadSettings();
  } catch {
    proxySaveMsg.textContent = "Failed to save proxy.";
  } finally {
    saveProxyBtn.disabled = false;
    saveProxyBtn.textContent = "Save Proxy";
  }
});

// Initialize on page load
renderHistory();
loadSettings();
