const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file");
const drop = document.getElementById("drop");
const filenameEl = document.getElementById("filename");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const statusSub = document.getElementById("status-sub");
const barFill = document.getElementById("bar-fill");
const resultsEl = document.getElementById("results");
const goBtn = document.getElementById("go");

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("drag");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    onFileChosen();
  }
});
fileInput.addEventListener("change", onFileChosen);

function onFileChosen() {
  const f = fileInput.files[0];
  if (!f) return;
  filenameEl.textContent = `${f.name} · ${(f.size / (1024 * 1024)).toFixed(1)} MB`;
}

function setStatus(text, sub = "", pct = null) {
  statusEl.classList.remove("hidden");
  statusText.textContent = text;
  statusSub.textContent = sub;
  if (pct !== null) barFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function showError(msg) {
  statusEl.classList.remove("hidden");
  statusEl.innerHTML = `<div class="error">${msg}</div>`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = fileInput.files[0];
  if (!f) {
    showError("Choose a video first.");
    return;
  }

  goBtn.disabled = true;
  resultsEl.classList.add("hidden");
  resultsEl.innerHTML = "";

  const fd = new FormData();
  fd.append("video", f);
  fd.append("n_clips", document.getElementById("n_clips").value);
  fd.append("clip_len", document.getElementById("clip_len").value);

  try {
    setStatus("Uploading…", `${(f.size / (1024 * 1024)).toFixed(1)} MB`, 5);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = (ev.loaded / ev.total) * 30; // upload = first 30%
        setStatus("Uploading…", `${Math.round(pct * (100/30))}% uploaded`, pct);
      }
    };
    const result = await new Promise((resolve, reject) => {
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { reject(new Error("invalid response")); }
        } else {
          reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
        }
      };
      xhr.onerror = () => reject(new Error("network error"));
      xhr.send(fd);
    });

    setStatus("Analyzing audio + motion…", `Job ${result.job_id}`, 35);
    await pollJob(result.job_id);
  } catch (err) {
    showError(`Failed: ${err.message}`);
  } finally {
    goBtn.disabled = false;
  }
});

// Use XHR instead of fetch() so it works behind basic-auth tunnel URLs
// (Chrome's fetch() refuses to resolve relative URLs against bases with creds).
function xhrGetJSON(path) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", path);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (e) { reject(new Error("invalid JSON")); }
      } else {
        reject(new Error(`HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.send();
  });
}

async function pollJob(jobId) {
  let lastStatus = "";
  while (true) {
    await new Promise((r) => setTimeout(r, 1500));
    const job = await xhrGetJSON(`/api/jobs/${jobId}`);

    if (job.status === "queued" && lastStatus !== "queued") {
      setStatus("Queued…", "", 35);
    } else if (job.status === "analyzing") {
      setStatus("Analyzing audio + motion…", `Source ${job.duration}s`, 50);
    } else if (job.status === "clipping") {
      const made = (job.clips || []).length;
      const total = (job.moments || []).length || job.n_clips || 4;
      const pct = 60 + (made / total) * 35;
      setStatus(`Generating clips… (${made}/${total})`, "Burning hook + CTA", pct);
      renderClips(job, /*partial=*/true);
    } else if (job.status === "done") {
      setStatus("Done!", `${(job.clips || []).length} clips ready`, 100);
      renderClips(job);
      return;
    } else if (job.status === "error") {
      throw new Error(job.error || "processing failed");
    }
    lastStatus = job.status;
  }
}

function renderClips(job, partial = false) {
  resultsEl.classList.remove("hidden");
  resultsEl.innerHTML = "";
  const clips = job.clips || [];
  for (const c of clips) {
    const node = document.createElement("div");
    node.className = "clip";
    node.innerHTML = `
      <video src="${c.url}" controls preload="metadata" playsinline></video>
      <div class="clip-meta">
        <div class="clip-title">
          <span>Clip ${c.index}</span>
          <span class="clip-time">${c.start}s → ${c.end}s · ${c.duration}s</span>
        </div>
        <div class="clip-hook">"${c.hook}"</div>
        <div class="score-pills">
          <span class="pill">score ${c.score}</span>
          <span class="pill audio">audio ${c.audio_score}</span>
          <span class="pill motion">motion ${c.motion_score}</span>
        </div>
        <div class="actions">
          <a class="primary" href="${c.url}" download="momentum_clip_${c.index}.mp4">Download</a>
          <a href="${c.url}" target="_blank" rel="noopener">Open</a>
        </div>
      </div>
    `;
    resultsEl.appendChild(node);
  }
  if (partial && clips.length === 0) {
    resultsEl.classList.add("hidden");
  }
}
