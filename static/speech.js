/* Listening review. Original ASR stays immutable; edits are appended server-side. */
"use strict";

let speechTick = null;
let speechActiveMs = 0;
let speechLastTick = 0;
let speechSaving = false;
let speechDispose = null;
let speechReviewContext = null;

window.stopSpeechTimer = () => {
  clearInterval(speechTick); speechTick = null;
  if (speechDispose) speechDispose();
  speechDispose = null;
};

function speechInterval(item, duration) {
  const start = Number(item?.start), end = Number(item?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  const range = {start: Math.max(0, start), end: Math.min(duration, end)};
  return range.end > range.start ? range : null;
}

function speechTimelineMarkup(speech) {
  const duration = Number(speech.duration_s);
  const waveform = speech.waveform;
  const analysis = speech.audio_analysis;
  const peaks = Array.isArray(waveform?.peaks) ? waveform.peaks : [];
  const hasWaveform = duration > 0 && peaks.length > 0;
  // Display scale only: preserve recorded peaks, audio gain, and QC metrics.
  const envelopePeak = peaks.reduce((maximum, peak) => {
    if (!Array.isArray(peak) || peak.length !== 2 || !peak.every(Number.isFinite)) return maximum;
    return Math.max(maximum, Math.abs(peak[0]), Math.abs(peak[1]));
  }, 0);
  const displayScale = envelopePeak > 0 ? envelopePeak : 1;
  const x = t => Math.max(0, Math.min(1000, Number(t) / duration * 1000));
  const intervalRect = (item, className, y, height) => {
    const range = speechInterval(item, duration);
    return range ? `<rect class="${className}" x="${x(range.start)}" y="${y}" width="${Math.max(1, x(range.end) - x(range.start))}" height="${height}"/>` : "";
  };
  const path = peaks.map((peak, index) => {
    if (!Array.isArray(peak) || peak.length !== 2 || !peak.every(Number.isFinite)) return "";
    const px = ((index + 0.5) / peaks.length * 1000).toFixed(2);
    const y = value => (60 - Math.max(-1, Math.min(1, value / displayScale)) * 43).toFixed(2);
    return `M${px},${y(peak[0])}V${y(peak[1])}`;
  }).join("");
  const issues = Array.isArray(analysis?.issues) ? analysis.issues : [];
  const vad = Array.isArray(analysis?.vad?.intervals) ? analysis.vad.intervals : [];
  return `<section class="speech-timeline" aria-label="Audio navigation">
    <div class="speech-timeline-heading"><b>Audio timeline</b><span id="speech-position">0.00 / ${duration.toFixed(2)} s</span></div>
    ${hasWaveform ? `<svg id="speech-waveform" viewBox="0 0 1000 120" preserveAspectRatio="none"
        role="slider" tabindex="0" aria-label="Audio position" aria-describedby="speech-timeline-help"
        aria-valuemin="0" aria-valuemax="${duration}" aria-valuenow="0">
      ${issues.map(item => intervalRect(item, "speech-wave-issue", 10, 104)).join("")}
      ${vad.map(item => intervalRect(item, "speech-wave-vad", 3, 5)).join("")}
      <line class="speech-wave-baseline" x1="0" y1="60" x2="1000" y2="60"/>
      <path class="speech-wave-peaks" d="${path}"/>
      <rect id="speech-wave-selection" class="speech-wave-selection" x="0" y="10" width="0" height="104"/>
      <line id="speech-wave-playhead" class="speech-wave-playhead" x1="0" y1="0" x2="0" y2="120"/>
    </svg><p class="speech-wave-scale">Amplitude scaled for navigation · audio unchanged</p>` : `<p class="speech-hint">Waveform analysis is not available for this clip. Use the player or time fields to review it.</p>`}
    <p id="speech-timeline-help" class="speech-hint">${hasWaveform ? "Click to seek; drag to select. Focus the waveform and use arrow keys to seek (Shift for 1 second). " : ""}Select a range, then play or mark it uncertain.${analysis && hasWaveform ? " Amber: review hints. Green: detected speech." : ""}</p>
    <div class="speech-selection-controls">
      <label>Start (s)<input id="speech-selection-start" type="number" min="0" max="${duration}" step="0.01" value="0.00"></label>
      <label>End (s)<input id="speech-selection-end" type="number" min="0" max="${duration}" step="0.01" value="${Math.min(duration, 3)}"></label>
      <button type="button" id="speech-play-selection">Play selection</button>
      <label class="speech-check"><input type="checkbox" id="speech-loop"> Loop selection</label>
      <button type="button" id="speech-play-full">Play full clip</button>
    </div>
    <div class="speech-mark-controls">
      <label for="speech-selection-reason">Uncertainty reason</label>
      <input id="speech-selection-reason" type="text" placeholder="e.g. unclear word" maxlength="500">
      <button type="button" id="speech-mark-uncertain">Mark selection uncertain</button>
    </div>
    <p id="speech-playback-status" class="speech-hint" aria-live="polite">Ready to listen.</p>
  </section>`;
}

function speechAnalysisMarkup(analysis) {
  if (!analysis) return "";
  const issues = Array.isArray(analysis.issues) ? analysis.issues : [];
  const warnings = Array.isArray(analysis.warnings) ? analysis.warnings : [];
  return `<section class="speech-analysis" aria-label="Audio analysis hints">
    <div class="speech-timeline-heading"><b>Review hints</b><span>${esc(analysis.status || "unknown")} · ${issues.length} flagged ranges</span></div>
    <p class="speech-hint">These are candidate problems, not confirmed errors. Quiet audio, VAD, and ASR timing can be wrong. No flags does not establish transcript accuracy.</p>
    <div class="speech-issue-list">${issues.map((issue, index) => `<div class="speech-issue">
      <button type="button" data-issue-index="${index}" class="speech-issue-play">
        <span>${Number(issue.start).toFixed(2)}–${Number(issue.end).toFixed(2)} s</span>
        ${esc(issue.message || issue.code)} <span aria-hidden="true">▶</span>
      </button>
      <details><summary>Evidence · ${esc(issue.code)} · ${esc(String(issue.priority ?? "review"))}</summary>
        <pre>${esc(JSON.stringify(issue.evidence || {}, null, 2))}</pre></details>
    </div>`).join("") || `<p class="speech-hint">No review ranges are available for this clip. Check the analysis status below.</p>`}</div>
    <details class="speech-analysis-details"><summary>Analysis status and limitations</summary>
      <p>VAD: ${esc(analysis.vad?.status || "unavailable")}${analysis.vad?.error ? ` · ${esc(String(analysis.vad.error))}` : ""}</p>
      ${warnings.length ? `<ul>${warnings.map(w => `<li>${esc(typeof w === "string" ? w : JSON.stringify(w))}</li>`).join("")}</ul>` : ""}
      <p>Issue playback records a successful playback start, not proof that a reviewer heard the entire range.</p>
      <p>Analyzer ${esc(analysis.analyzer_version || "unknown")} · ${esc(analysis.analysis_id || "unversioned")}</p>
    </details>
  </section>`;
}

function speechSetupPlayback(task) {
  const player = $("#speech-player");
  const waveform = $("#speech-waveform");
  const startInput = $("#speech-selection-start");
  const endInput = $("#speech-selection-end");
  const status = $("#speech-playback-status");
  const duration = Number(task.speech.duration_s);
  const analysis = task.speech.audio_analysis;
  const issues = Array.isArray(analysis?.issues) ? analysis.issues : [];
  const controller = new AbortController();
  const on = (target, event, callback) => target?.addEventListener(event, callback, {signal: controller.signal});
  let disposed = false, raf = null, bounds = null, pointer = null, playRequest = 0;
  const clamp = time => Math.max(0, Math.min(duration, time));
  const readSelection = () => {
    if (!startInput.value.trim() || !endInput.value.trim()) return null;
    const start = Number(startInput.value), end = Number(endInput.value);
    return Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end <= duration && end > start
      ? {start, end} : null;
  };
  const paintSelection = () => {
    const selection = readSelection();
    const rect = $("#speech-wave-selection");
    if (rect) {
      rect.setAttribute("x", selection ? selection.start / duration * 1000 : 0);
      rect.setAttribute("width", selection ? (selection.end - selection.start) / duration * 1000 : 0);
    }
    $("#speech-play-selection").disabled = !selection;
    $("#speech-mark-uncertain").disabled = !selection;
  };
  const setSelection = (start, end) => {
    startInput.value = clamp(start).toFixed(2);
    // Avoid rounding beyond an audio duration with sub-centisecond precision.
    endInput.value = String(Math.min(duration, Number(clamp(end).toFixed(2))));
    paintSelection();
  };
  const paintPosition = () => {
    if (disposed) return;
    const time = clamp(Number(player.currentTime) || 0);
    $("#speech-position").textContent = `${time.toFixed(2)} / ${duration.toFixed(2)} s`;
    if (waveform) {
      const x = time / duration * 1000;
      const playhead = $("#speech-wave-playhead");
      playhead.setAttribute("x1", x); playhead.setAttribute("x2", x);
      waveform.setAttribute("aria-valuenow", time.toFixed(2));
      waveform.setAttribute("aria-valuetext", `${time.toFixed(2)} seconds`);
    }
  };
  const enforceBounds = () => {
    if (disposed || !bounds || player.paused) return;
    if (player.currentTime >= bounds.end) {
      if ($("#speech-loop").checked) {
        player.currentTime = bounds.start;
      } else {
        player.pause(); player.currentTime = bounds.end;
        status.textContent = "Selection finished. Replay or choose another range.";
      }
    }
  };
  const animate = () => {
    raf = null;
    if (disposed) return;
    enforceBounds(); paintPosition();
    if (!player.paused) raf = requestAnimationFrame(animate);
  };
  const play = async (selection, issueId = null) => {
    const request = ++playRequest;
    bounds = selection;
    try {
      player.currentTime = selection?.start ?? 0;
      await player.play();
      if (disposed || request !== playRequest || player.paused) return;
      if (issueId && !speechReviewContext.played_issue_ids.includes(issueId)) {
        speechReviewContext.played_issue_ids.push(issueId);
      }
      status.textContent = selection
        ? `Playing ${selection.start.toFixed(2)}–${selection.end.toFixed(2)} s${$("#speech-loop").checked ? " · looping" : ""}.`
        : "Playing the full clip.";
    } catch (error) {
      if (!disposed && request === playRequest) {
        bounds = null;
        status.textContent = "Playback could not start. Try the audio player.";
        toast(error.message, true);
      }
    }
  };
  const seek = time => {
    ++playRequest; bounds = null;
    player.currentTime = clamp(time);
    status.textContent = `Position ${clamp(time).toFixed(2)} s. Full-clip playback is active.`;
    paintPosition();
  };
  const selectAndPlay = (item, context = 0, issueId = null) => {
    const range = speechInterval(item, duration);
    if (!range) return toast("This range has no valid audio timing.", true);
    setSelection(range.start - context, range.end + context);
    const selection = readSelection();
    if (selection) play(selection, issueId);
  };
  on(startInput, "input", () => { ++playRequest; bounds = null; paintSelection(); });
  on(endInput, "input", () => { ++playRequest; bounds = null; paintSelection(); });
  on($("#speech-play-selection"), "click", () => { const range = readSelection(); if (range) play(range); });
  on($("#speech-play-full"), "click", () => play(null));
  on($("#speech-mark-uncertain"), "click", () => {
    if (speechSaving) return;
    const range = readSelection(), reasonInput = $("#speech-selection-reason");
    const reason = reasonInput.value.trim().replace(/[\r\n]+/g, " ");
    if (!range || !reason) { reasonInput.focus(); return toast("Select a valid range and add an uncertainty reason.", true); }
    const field = $("#speech-uncertain");
    field.value = `${field.value.trim()}${field.value.trim() ? "\n" : ""}${range.start},${range.end},${reason}`;
    reasonInput.value = ""; speechSaveDraft();
    status.textContent = `Added uncertain span ${range.start.toFixed(2)}–${range.end.toFixed(2)} s. It will be saved with your review.`;
  });
  on($(".speech-segments"), "click", event => {
    const button = event.target.closest("button[data-start]");
    if (button) selectAndPlay(button.dataset);
  });
  on($(".speech-words"), "click", event => {
    const button = event.target.closest("button[data-start]");
    if (button) selectAndPlay(button.dataset, 0.08);
  });
  on($(".speech-issue-list"), "click", event => {
    const button = event.target.closest("button[data-issue-index]");
    if (!button) return;
    const issue = issues[Number(button.dataset.issueIndex)];
    if (issue) selectAndPlay(issue, 0.2, issue.id ? String(issue.id) : null);
  });
  const pointerTime = event => {
    const rect = waveform.getBoundingClientRect();
    return clamp((event.clientX - rect.left) / rect.width * duration);
  };
  on(waveform, "pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); waveform.focus();
    pointer = {id: event.pointerId, start: pointerTime(event), x: event.clientX, dragged: false};
    waveform.setPointerCapture(event.pointerId);
  });
  on(waveform, "pointermove", event => {
    if (!pointer || pointer.id !== event.pointerId) return;
    pointer.dragged ||= Math.abs(event.clientX - pointer.x) > 3;
    if (pointer.dragged) {
      ++playRequest; bounds = null;
      const end = pointerTime(event);
      setSelection(Math.min(pointer.start, end), Math.max(pointer.start, end));
    }
  });
  on(waveform, "pointerup", event => {
    if (!pointer || pointer.id !== event.pointerId) return;
    if (!pointer.dragged) seek(pointerTime(event));
    else status.textContent = "Range selected. Use Play selection to listen.";
    pointer = null;
    if (waveform.hasPointerCapture(event.pointerId)) waveform.releasePointerCapture(event.pointerId);
  });
  on(waveform, "pointercancel", () => { pointer = null; });
  on(waveform, "keydown", event => {
    let time = player.currentTime;
    if (event.key === "ArrowLeft") time -= event.shiftKey ? 1 : 0.1;
    else if (event.key === "ArrowRight") time += event.shiftKey ? 1 : 0.1;
    else if (event.key === "Home") time = 0;
    else if (event.key === "End") time = duration;
    else return;
    event.preventDefault(); seek(time);
  });
  on(player, "play", () => { if (raf === null) raf = requestAnimationFrame(animate); });
  on(player, "timeupdate", () => { enforceBounds(); paintPosition(); });
  on(player, "pause", () => { cancelAnimationFrame(raf); raf = null; paintPosition(); });
  on(document, "click", event => {
    const tab = event.target.closest?.(".tab");
    if (tab && tab.dataset.tab !== "annotate") { ++playRequest; player.pause(); }
  });
  on(player, "ended", () => {
    if (bounds && $("#speech-loop").checked) play({...bounds});
    else paintPosition();
  });
  on(player, "error", () => {
    status.textContent = "Audio could not load. Check the registered file and its hash.";
    toast(status.textContent, true);
  });
  on($("#speech-speed"), "change", event => { player.playbackRate = Number(event.target.value); });
  paintSelection();
  speechDispose = () => {
    disposed = true; ++playRequest;
    controller.abort(); cancelAnimationFrame(raf); player.pause();
    player.removeAttribute("src"); player.load();
  };
}

function speechDraftKey(task, annotator = state.annotator) {
  return `anota_speech_draft:${annotator}:${task.task_id}`;
}

function speechSaveDraft() {
  if (state.task?.task_type !== "speech") return;
  try {
    localStorage.setItem(speechDraftKey(state.task), JSON.stringify({
      text: $("#speech-correction").value, note: $("#speech-note").value,
      spans: $("#speech-uncertain").value
    }));
  } catch { /* Private browsing or full storage must not stop manual review. */ }
}

window.exportCurrentBatch = async () => {
  try {
    const batchId = state.batchId || state.task?.batch_id;
    const result = await api("/export", batchId ? {batch_id: batchId} : {});
    const link = document.createElement("a");
    link.href = result.download_url; link.download = `dataset_${result.version}.json`;
    document.body.appendChild(link); link.click(); link.remove();
    toast(`Export ${result.version} · SHA-256 ${result.sha256.slice(0, 12)}…`);
  } catch (e) { toast("export failed: " + e.message, true); }
};

window.renderSpeechTask = task => {
  window.stopSpeechTimer();
  $("#judge-badge").textContent = task.speech.audio_analysis ? "speech · review hints" : "speech · manual";
  $("#judge-badge").className = "badge on";
  const speech = task.speech;
  speechReviewContext = {
    analysis_id: speech.audio_analysis?.analysis_id || null,
    hints_exposed: Boolean(speech.audio_analysis), played_issue_ids: []
  };
  const latest = speech.latest_annotation;
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(speechDraftKey(task))); } catch {}
  $("#view-annotate").innerHTML = `
    <div class="card speech-card">
      <div class="progress-line">
        <span><b>${state.progress.done + 1}</b> / ${state.progress.total}</span>
        <span>Listening review · <b>${esc(speech.language)}</b></span>
        <span id="timer">0:00</span>
        <span>batch <b>${esc(task.batch.name)}</b></span>
      </div>
      <div class="speech-toolbar">
        <audio id="speech-player" controls preload="metadata" src="${esc(speech.audio_url)}"></audio>
        <label>Speed <select id="speech-speed" aria-label="Playback speed">
          <option value="0.75">0.75×</option><option value="1" selected>1×</option>
          <option value="1.25">1.25×</option></select></label>
      </div>
      <p class="speech-hint">Listen and correct the transcript. Select a segment to play its range. Alt + P plays or pauses.</p>
      ${speechTimelineMarkup(speech)}
      ${speechAnalysisMarkup(speech.audio_analysis)}
      <div class="textpanel"><div class="lbl">Original ASR · preserved in export</div>
        <div class="txt" id="speech-original">${esc(speech.asr_text)}</div></div>
      <div class="speech-segments">${(speech.segments || []).map(segment => `
        <button type="button" data-start="${Number(segment.start)}" data-end="${Number(segment.end)}" title="Play ${Number(segment.start).toFixed(1)} to ${Number(segment.end).toFixed(1)} seconds">
          <span>${Number(segment.start).toFixed(1)}–${Number(segment.end).toFixed(1)}s</span> ${esc(segment.text)}
        </button>`).join("")}</div>
      ${(speech.words || []).length ? `<details class="speech-word-details"><summary>Word timing · approximate</summary>
        <div class="speech-words">${speech.words.map(word => `<button type="button" data-start="${Number(word.start)}" data-end="${Number(word.end)}"
          title="Play ${Number(word.start).toFixed(2)}–${Number(word.end).toFixed(2)} s">${esc(word.w)}</button>`).join("")}</div></details>` : ""}
      <div class="field"><label for="speech-correction">Corrected transcript</label>
        <textarea id="speech-correction" rows="5" spellcheck="false"></textarea></div>
      <div id="speech-change" class="speech-hint" aria-live="polite"></div>
      <div class="field"><label for="speech-uncertain">Uncertain spans · optional, one per line: start,end,reason</label>
        <textarea id="speech-uncertain" rows="2" placeholder="1.2,2.5,unclear word"></textarea></div>
      <div class="field"><label for="speech-note">Review note · optional</label>
        <textarea id="speech-note" rows="2"></textarea></div>
      <div class="actions">
        <button class="primary" id="speech-submit">Save &amp; Next <kbd class="k">Ctrl + Enter</kbd></button>
        <button id="speech-skip">Skip</button><button id="speech-undo">Undo</button>
        <button id="speech-export">Export batch</button>
      </div>
      <details class="speech-provenance"><summary>Task identity</summary><code>${esc(task.task_id)}</code></details>
    </div>`;
  const editor = $("#speech-correction");
  editor.value = draft?.text ?? latest?.corrected_transcript ?? speech.asr_text;
  $("#speech-note").value = draft?.note ?? latest?.note ?? "";
  $("#speech-uncertain").value = draft?.spans ?? (latest?.uncertain_spans || []).map(s =>
    `${s.start},${s.end},${s.reason}`).join("\n");
  const describeChange = () => {
    $("#speech-change").textContent = editor.value === speech.asr_text
      ? "Transcript matches the ASR draft."
      : `Edited · ${[...speech.asr_text].length} → ${[...editor.value].length} characters. Original ASR is retained.`;
  };
  describeChange();
  [editor, $("#speech-note"), $("#speech-uncertain")].forEach(el =>
    el.addEventListener("input", () => { describeChange(); speechSaveDraft(); }));
  speechSetupPlayback(task);
  $("#speech-submit").onclick = window.submitSpeech;
  $("#speech-skip").onclick = skip;
  $("#speech-undo").onclick = undo;
  $("#speech-export").onclick = window.exportCurrentBatch;
  speechActiveMs = 0; speechLastTick = performance.now();
  speechTick = setInterval(() => {
    const now = performance.now();
    if (document.visibilityState === "visible" && document.hasFocus() &&
        $("#view-annotate").classList.contains("active")) {
      speechActiveMs += Math.min(now - speechLastTick, 1500);
    }
    speechLastTick = now;
    const seconds = Math.floor((Date.now() - state.startTs) / 1000);
    if ($("#timer")) $("#timer").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }, 500);
};

window.submitSpeech = async () => {
  const task = state.task;
  const annotator = state.annotator;
  if (speechSaving || task?.task_type !== "speech" ||
      !$("#view-annotate").classList.contains("active") || !$("#modal").classList.contains("hidden")) return;
  let spans;
  try {
    spans = $("#speech-uncertain").value.split("\n").filter(line => line.trim()).map(line => {
      const [start, end, ...reason] = line.split(",");
      const span = {start: Number(start), end: Number(end), reason: reason.join(",").trim()};
      if (!start?.trim() || !end?.trim() || !Number.isFinite(span.start) || !Number.isFinite(span.end) ||
          span.start < 0 || span.end <= span.start || span.end > task.speech.duration_s || !span.reason) {
        throw new Error("Use start,end,reason within the audio duration for each uncertain span.");
      }
      return span;
    });
  } catch (e) { return toast(e.message, true); }
  speechSaving = true; $("#speech-submit").disabled = true;
  const fields = [$("#speech-correction"), $("#speech-note"), $("#speech-uncertain")];
  fields.forEach(field => { field.disabled = true; });
  try {
    const elapsed = Date.now() - state.startTs;
    await api("/speech/submit", {
      annotator, assignment_id: task.assignment_id,
      corrected_transcript: $("#speech-correction").value, uncertain_spans: spans,
      note: $("#speech-note").value, elapsed_ms: elapsed,
      active_ms: Math.min(elapsed, Math.floor(speechActiveMs)),
      review_context: {...speechReviewContext, played_issue_ids: [...speechReviewContext.played_issue_ids]}
    });
    try { localStorage.removeItem(speechDraftKey(task, annotator)); } catch {}
    toast("Transcript saved ✓");
    if (state.task === task && state.annotator === annotator) {
      state.task = null; await claimNext();
    }
  } catch (e) { toast(e.message, true); }
  finally {
    speechSaving = false;
    if ($("#speech-submit")) $("#speech-submit").disabled = false;
    fields.forEach(field => { field.disabled = false; });
  }
};

document.addEventListener("keydown", e => {
  if (state.task?.task_type === "speech" && e.target.matches("textarea") &&
      $("#view-annotate").classList.contains("active") && $("#modal").classList.contains("hidden") &&
      e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault(); window.submitSpeech();
  }
});
