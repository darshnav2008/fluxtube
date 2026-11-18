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
const aiStatusBadge = document.getElementById("aiStatusBadge");

const generateBtn = document.getElementById("generateBtn");
const generateBtnLabel = document.getElementById("generateBtnLabel");
const progressHud = document.getElementById("progressHud");
const progressStage = document.getElementById("progressStage");
const progressPct = document.getElementById("progressPct");
const progressFill = document.getElementById("progressFill");
const progressSpeed = document.getElementById("progressSpeed");
const progressEta = document.getElementById("progressEta");

const clipsGallery = document.getElementById("clipsGallery");
const clipsEmptyState = document.getElementById("clipsEmptyState");
const clipsCountBadge = document.getElementById("clipsCountBadge");

const historyToggle = document.getElementById("historyToggle");
const historyDrawer = document.getElementById("historyDrawer");
const drawerBackdrop = document.getElementById("drawerBackdrop");
const closeDrawerBtn = document.getElementById("closeDrawerBtn");
const clearHistoryBtn = document.getElementById("clearHistoryBtn");
const historyList = document.getElementById("historyList");
const historyCount = document.getElementById("historyCount");

const MAX_FILE_BYTES = 250 * 1024 * 1024;

let currentFile = null;
let currentObjectUrl = null;
let currentDuration = 60;
let pollTimer = null;

const settings = {
  clip_len: 30,
  num_clips: 3,
  framing: "blur_bg",
  caption_style: "viral_yellow",
  effect: "cinematic",
};

// Mouse-tracking radial spotlight
document.addEventListener("mousemove", (e) => {
  document.querySelectorAll(".spotlight-card").forEach((card) => {
    const rect = card.getBoundingClientRect();
    card.style.setProperty("--mouse-x", `${e.clientX - rect.left}px`);
    card.style.setProperty("--mouse-y", `${e.clientY - rect.top}px`);
  });
});

// Check whether GROQ_API_KEY is active on server
fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    if (!aiStatusBadge) return;
    if (h.ai_captions_enabled) {
      aiStatusBadge.textContent = "AI Whisper Captions: ACTIVE";
      aiStatusBadge.classList.add("ai-on");
    } else {
      aiStatusBadge.textContent = "Smart Auto-Clip Mode (Add GROQ_API_KEY for AI Captions)";
    }
  })
  .catch(() => {});

function formatSeconds(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytesJS(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
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

// Pill Selector Groups
document.querySelectorAll(".pill-group").forEach((group) => {
  const key = group.dataset.setting;
  group.querySelectorAll(".pill-opt").forEach((btn) => {
    btn.addEventListener("click", () => {
      group.querySelectorAll(".pill-opt").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const val = btn.dataset.val;
      settings[key] = key === "clip_len" || key === "num_clips" ? Number(val) : val;
      updateGenerateLabel();
    });
  });
});

function updateGenerateLabel() {
  generateBtnLabel.textContent = `Generate ${settings.num_clips} × ${settings.clip_len}s Clips →`;
}
updateGenerateLabel();

// Upload / Drag & Drop
pickFileBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  videoFileInput.click();
});

dropZoneBar.addEventListener("click", () => videoFileInput.click());

videoFileInput.addEventListener("change", () => {
  if (videoFileInput.files && videoFileInput.files[0]) {
    loadPodcastFile(videoFileInput.files[0]);
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
  if (file) loadPodcastFile(file);
});

function loadPodcastFile(file) {
  clearError();
  if (!file.type.startsWith("video/") && !/\.(mp4|mov|webm|mkv|m4v|avi)$/i.test(file.name)) {
    showError("Please upload a valid video file (MP4, MOV, WebM, MKV).");
    return;
  }
  if (file.size > MAX_FILE_BYTES) {
    showError("File is over 250 MB. Please upload a smaller podcast video.");
    return;
  }

  currentFile = file;
  if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
  currentObjectUrl = URL.createObjectURL(file);

  dropFileLabel.textContent = `${file.name} (${formatBytesJS(file.size)})`;
  const pickLabel = pickFileBtn.querySelector(".btn-label");
  if (pickLabel) pickLabel.textContent = "Change Podcast";

  videoTitle.textContent = file.name;
  progressHud.hidden = true;
  clipsGallery.innerHTML = "";
  clipsEmptyState.hidden = false;
  clipsCountBadge.textContent = "Ready";

  videoPreview.src = currentObjectUrl;
  videoPreview.onloadedmetadata = () => {
    currentDuration = Math.max(5, Math.floor(videoPreview.duration || 60));
    videoDuration.textContent = formatSeconds(currentDuration);
    const w = videoPreview.videoWidth;
    const h = videoPreview.videoHeight;
    videoResolutionBadge.textContent =
      w && h ? `${w}×${h} • ${formatBytesJS(file.size)}` : formatBytesJS(file.size);

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

// Generate AI Vertical Clips
generateBtn.addEventListener("click", () => {
  if (!currentFile) return;
  if (pollTimer) clearInterval(pollTimer);

  generateBtn.disabled = true;
  progressHud.hidden = false;
  progressStage.textContent = "Uploading podcast video...";
  progressPct.textContent = "0%";
  progressFill.style.width = "0%";
  progressSpeed.textContent = `Input: ${formatBytesJS(currentFile.size)}`;
  progressEta.textContent = `${settings.num_clips} × ${settings.clip_len}s (9:16)`;

  const formData = new FormData();
  formData.append("file", currentFile);
  formData.append("duration", String(currentDuration));
  formData.append("num_clips", String(settings.num_clips));
  formData.append("clip_len", String(settings.clip_len));
  formData.append("framing", settings.framing);
  formData.append("caption_style", settings.caption_style);
  formData.append("effect", settings.effect);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/generate-clips", true);

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.min(25, Math.round((e.loaded / e.total) * 25));
      progressPct.textContent = `${pct}%`;
      progressFill.style.width = `${pct}%`;
    }
  };

  xhr.onload = () => {
    let data = {};
    try {
      data = JSON.parse(xhr.responseText || "{}");
    } catch {
      data = {};
    }

    if (xhr.status < 200 || xhr.status >= 300 || !data.job_id) {
      progressStage.textContent = data.error || "Upload failed. Try again.";
      generateBtn.disabled = false;
      return;
    }

    const jobId = data.job_id;
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/job/${jobId}`);
        const job = await res.json();

        const mappedPct = Math.max(25, Math.round(25 + (job.progress || 0) * 0.75));
        progressPct.textContent = `${Math.min(100, mappedPct)}%`;
        progressFill.style.width = `${Math.min(100, mappedPct)}%`;
        progressStage.textContent = job.stage || "Processing...";

        if (job.status === "completed") {
          clearInterval(pollTimer);
          generateBtn.disabled = false;
          progressPct.textContent = "100%";
          progressFill.style.width = "100%";
          renderClipsGallery(job.clips || [], job.ai_mode);
          saveToHistory({
            name: currentFile.name,
            summary: `${(job.clips || []).length} × ${settings.clip_len}s Vertical Clips`,
          });
        } else if (job.status === "error") {
          clearInterval(pollTimer);
          generateBtn.disabled = false;
          progressStage.textContent = `Error: ${job.error || "Could not generate clips"}`;
        }
      } catch {
        clearInterval(pollTimer);
        generateBtn.disabled = false;
        progressStage.textContent = "Lost connection to server.";
      }
    }, 1200);
  };

  xhr.onerror = () => {
    generateBtn.disabled = false;
    progressStage.textContent = "Network error during upload.";
  };

  xhr.send(formData);
});

function renderClipsGallery(clips, aiMode) {
  clipsGallery.innerHTML = "";
  if (!clips.length) {
    clipsEmptyState.hidden = false;
    return;
  }
  clipsEmptyState.hidden = true;
  clipsCountBadge.textContent =
    aiMode === "groq_ai"
      ? `${clips.length} AI Viral Clips Ready`
      : `${clips.length} Vertical Clips Ready`;

  clips.forEach((clip) => {
    const card = document.createElement("article");
    card.className = "reel-card";
    card.innerHTML = `
      <div class="reel-video-wrap">
        <video class="reel-video" src="${clip.preview_url}" controls playsinline preload="metadata"></video>
        <span class="reel-score-pill">Viral ${clip.score}/100</span>
        <span class="reel-time-pill">${clip.start_str} → ${clip.end_str}</span>
      </div>
      <div class="reel-body">
        <h4 class="reel-title">${clip.title}</h4>
        <div class="reel-meta">
          <span>${clip.duration_sec}s • 9:16 HD</span>
          <span>${clip.has_captions ? "AI Captions ✓" : "FX Only"} • ${clip.size_label}</span>
        </div>
        <a class="btn-reel-dl" href="${clip.download_url}" download="${clip.filename}">
          Download Vertical MP4
        </a>
      </div>
    `;
    clipsGallery.appendChild(card);
  });
}

// History Drawer
const HISTORY_KEY = "fluxtube_ai_clips_history_v1";

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
    summary: entry.summary,
    time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 12)));
  renderHistory();
}

function renderHistory() {
  const items = getHistory();
  historyCount.textContent = items.length;
  if (!items.length) {
    historyList.innerHTML = `<p class="drawer-empty">No AI clips generated yet.</p>`;
    return;
  }

  historyList.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";
    row.innerHTML = `
      <div>
        <div class="history-item-title">${item.name}</div>
        <div class="history-item-sub">${item.summary} • ${item.time}</div>
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
