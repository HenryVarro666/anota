/* Anota Workbench — core + Annotate tab. Review/Dashboard live in review.js/dash.js. */
"use strict";

const state = { annotator: localStorage.getItem("anota_annotator") || "",
                speechSort: ["ml", "qc", "fifo"].includes(localStorage.getItem("anota_speech_sort")) ? localStorage.getItem("anota_speech_sort") : "ml",
                task: null, startTs: 0, sel: null, timerId: null,
                batchId: Number(new URLSearchParams(location.search).get("batch")) || null };

const $ = (s, el) => (el || document).querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, body) {
  const r = await fetch("/api" + path, body === undefined ? {} :
    { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.status;
    try { const j = await r.json(); msg = Array.isArray(j.detail) ? (j.detail[0]?.msg || msg) : (j.detail || msg); } catch {}
    const error = new Error(msg); error.status = r.status; throw error;
  }
  return r.json();
}

let toastId = null;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.className = isErr ? "err" : "";
  clearTimeout(toastId); toastId = setTimeout(() => t.classList.add("hidden"), 2600);
}

function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.id === "view-" + b.dataset.tab));
  if (b.dataset.tab === "review") window.renderReview?.();
  if (b.dataset.tab === "dashboard") window.renderDashboard?.(true); else window.stopDashPoll?.();
});

/* ---------- theme (auto -> light -> dark) + health ---------- */
const THEMES = ["auto", "light", "dark"];
const THEME_ICONS = { auto: "◐", light: "☀︎", dark: "☾" };
let themeMode = localStorage.getItem("anota_theme");
if (!THEMES.includes(themeMode)) themeMode = "auto";
function applyTheme(mode) {
  document.documentElement.dataset.theme = mode === "auto" ? "" : mode;
  const b = $("#theme-btn");
  b.textContent = THEME_ICONS[mode]; b.title = "appearance: " + mode;
}
applyTheme(themeMode);
$("#theme-btn").onclick = () => {
  themeMode = THEMES[(THEMES.indexOf(themeMode) + 1) % THEMES.length];
  localStorage.setItem("anota_theme", themeMode); applyTheme(themeMode);
};
async function health() {
  try {
    const h = await api("/health");
    const b = $("#judge-badge");
    if (state.task?.task_type === "speech") {
      b.textContent = state.task.speech.audio_analysis ? "speech · review hints" : "speech · manual";
      b.className = "badge on"; return;
    }
    b.textContent = "judge: " + h.judge.mode + (h.judge.reachable ? "" : " (offline)");
    b.className = "badge " + (h.judge.mode === "mock" ? "mock" : h.judge.reachable ? "on" : "off");
  } catch { $("#judge-badge").className = "badge off"; }
}
health();

async function loadBatches() {
  try {
    const batches = await api("/batches");
    $("#batch-select").innerHTML = '<option value="">All batches</option>' + batches.map(b =>
      `<option value="${b.id}">${esc(b.name)} (${b.n_tasks})</option>`).join("");
    $("#batch-select").value = state.batchId || "";
  } catch (e) { toast("batch list: " + e.message, true); }
}
$("#batch-select").onchange = e => {
  state.batchId = Number(e.target.value) || null;
  const url = new URL(location.href);
  if (state.batchId) url.searchParams.set("batch", state.batchId); else url.searchParams.delete("batch");
  history.replaceState(null, "", url);
  if (state.annotator) claimNext();
};
loadBatches();
$("#export-btn").onclick = window.exportCurrentBatch;

/* ---------- annotate ---------- */
const ERRORS = [["m","mistranslation"],["o","omission"],["a","addition"],["t","terminology"],
                ["n","number_unit"],["g","negation_polarity"],["r","grammar"],["p","punctuation"]];
const SEVERITIES = ["neutral","minor","major","critical"];

function freshSel() { return { errors: new Set(), severity: null, adequacy: null, fluency: null }; }

$("#annotator").value = state.annotator;
$("#start-btn").onclick = start;
function start() {
  const nextAnnotator = $("#annotator").value.trim();
  if (!nextAnnotator) return toast("annotator id required", true);
  if (state.annotator !== nextAnnotator) {
    window.speechSaveDraft?.(); state.task = null;
  }
  state.annotator = nextAnnotator;
  localStorage.setItem("anota_annotator", state.annotator);
  claimNext();
}

let claimSerial = 0;
async function claimNext(targetTaskId) {
  if (typeof targetTaskId !== "string") targetTaskId = null;
  const previousTask = state.task;
  const serial = ++claimSerial;
  const annotator = state.annotator, batchId = state.batchId;
  window.speechSaveDraft?.();
  state.task = null;
  window.stopSpeechTimer?.(); clearInterval(state.timerId);
  $("#view-annotate").innerHTML = '<div class="empty">Loading task…</div>';
  try {
    const body = { annotator, speech_sort: state.speechSort };
    if (targetTaskId) body.task_id = targetTaskId;
    if (batchId != null) body.batch_id = batchId;
    const r = await api("/claim", body);
    if (serial !== claimSerial || annotator !== state.annotator || batchId !== state.batchId) return;
    state.progress = r.progress;
    renderTask(r.task);
  } catch (e) {
    if (serial !== claimSerial) return;
    if (targetTaskId && previousTask) {
      renderTask(previousTask);
      toast(e.status === 409 ? "Finish or skip your current task first." : "claim failed: " + e.message, true);
      return;
    }
    toast("claim failed: " + e.message, true);
    $("#view-annotate").innerHTML = `<div class="empty">claim failed — <button id="btn-retry" class="primary">Retry</button></div>`;
    $("#btn-retry").onclick = claimNext;
  }
}

function renderTask(task) {
  window.stopSpeechTimer?.();
  clearInterval(state.timerId);
  state.task = task; state.sel = freshSel(); state.startTs = Date.now();
  const v = $("#view-annotate");
  if (!task) {
    v.innerHTML = `<div class="empty">🎉 Queue empty — nothing left to annotate.
      <div class="actions"><button id="empty-undo">Undo last submission</button>
      <button id="empty-export">Export batch</button></div></div>`;
    $("#empty-undo").onclick = undo;
    $("#empty-export").onclick = window.exportCurrentBatch;
    return;
  }
  if (task.task_type === "speech") return window.renderSpeechTask(task);
  const meta = task.metadata || {};
  const sugg = task.suggestions;
  v.innerHTML = `
  <div class="card">
    <div class="progress-line">
      <span>#<b>${state.progress.done + 1}</b>/${state.progress.total}</span>
      <span>task <b>${esc(task.task_id)}</b></span>
      ${meta.arm ? `<span>arm <b>${esc(meta.arm)}</b></span>` : ""}
      <span id="timer">0:00</span>
      <span>batch <b>${esc(task.batch.name)}</b></span>
      ${state.batchId != null ? `<span>filter: batch ${esc(state.batchId)} <button id="clear-batch" class="link-x" title="clear batch filter">×</button></span>` : ""}
    </div>
    <div class="textpanel"><div class="lbl">Source</div><div class="txt">${esc(task.source)}</div></div>
    <div class="textpanel"><div class="lbl">Hypothesis</div><div class="txt">${esc(task.hypothesis)}</div></div>
    ${task.reference ? `<details class="ref"><summary>reference</summary>
      <div class="textpanel"><div class="txt">${esc(task.reference)}</div></details>` : ""}
    ${sugg ? renderSuggestions(sugg) :
      `<div class="anchor-note">No machine signals: this batch collects golden labels —
       anchoring discipline is enforced server-side.</div>`}
    <div class="row">
      <div class="field"><label>adequacy <kbd class="k">1-5</kbd></label>
        <div class="seg" id="seg-adequacy">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join("")}</div></div>
      <div class="field"><label>fluency <kbd class="k">⇧1-5</kbd></label>
        <div class="seg" id="seg-fluency">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join("")}</div></div>
      <div class="field"><label>severity <kbd class="k">v</kbd></label>
        <div class="seg" id="seg-severity">${SEVERITIES.map(s=>`<button class="sev-${s}" data-v="${s}">${s}</button>`).join("")}</div></div>
    </div>
    <div class="field"><label>error types <kbd class="k">letter keys · 0 = no error</kbd></label>
      <div class="chips" id="chips-errors">
        <button class="chip" data-v="no_error"><kbd>0</kbd>no_error</button>
        ${ERRORS.map(([k,e])=>`<button class="chip" data-v="${e}"><kbd>${k}</kbd>${e}</button>`).join("")}
      </div></div>
    <div class="field"><label>correction (optional) <kbd class="k">c</kbd></label>
      <textarea id="correction" rows="2"></textarea></div>
    <div class="field"><label>note — required for critical <kbd class="k">x</kbd></label>
      <textarea id="note" rows="2"></textarea></div>
    <div class="actions">
      <button class="primary" id="btn-submit">Save &amp; Next <kbd class="k">␣</kbd></button>
      <button id="btn-skip">Skip <kbd class="k">s</kbd></button>
      <button id="btn-undo">Undo <kbd class="k">u</kbd></button>
      <span class="hint">? for guideline</span>
    </div>
  </div>`;
  state.timerId = setInterval(() => {
    const s = Math.floor((Date.now() - state.startTs) / 1000);
    const el = $("#timer"); if (el) el.textContent = `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;
  }, 1000);
  $("#seg-adequacy").onclick = e => e.target.dataset.v && setSeg("adequacy", +e.target.dataset.v);
  $("#seg-fluency").onclick = e => e.target.dataset.v && setSeg("fluency", +e.target.dataset.v);
  $("#seg-severity").onclick = e => e.target.dataset.v && setSeg("severity", e.target.dataset.v);
  $("#chips-errors").onclick = e => { const c = e.target.closest(".chip"); if (c) toggleError(c.dataset.v); };
  $("#btn-submit").onclick = submit; $("#btn-skip").onclick = skip; $("#btn-undo").onclick = undo;
  const clearBatchBtn = $("#clear-batch");
  if (clearBatchBtn) clearBatchBtn.onclick = () => { state.batchId = null; claimNext(); };
}

function renderSuggestions(sugg) {
  const j = sugg.judge;
  return `<div class="sugg"><div class="lbl">machine suggestions (routing batch)</div><div class="chips">
    ${j ? `<span class="chip ${j.is_mock ? "mockc" : "warnc"}"
        title="${esc(j.rationale)}">judge${j.is_mock ? " · MOCK" : ""}: ${esc(j.worst_severity)}
        · adequacy ${j.adequacy} · conf ${j.confidence}</span>` : ""}
    ${sugg.lf.map(f => `<span class="chip warnc" title="${esc(f.evidence)}">⚠ ${esc(f.lf)}</span>`).join("")}
  </div></div>`;
}

function setSeg(name, val) {
  state.sel[name] = val;
  document.querySelectorAll(`#seg-${name} button`).forEach(b =>
    b.classList.toggle("sel", String(b.dataset.v) === String(val)));
  if (name === "severity" && val !== "neutral" && state.sel.errors.size === 1 && state.sel.errors.has("no_error")) {
    state.sel.errors.delete("no_error");
    document.querySelectorAll("#chips-errors .chip").forEach(c =>
      c.classList.toggle("sel", state.sel.errors.has(c.dataset.v)));
  }
}
function toggleError(e) {
  const s = state.sel.errors;
  if (e === "no_error") {
    s.clear(); s.add("no_error"); setSeg("severity", "neutral");
    if (state.sel.adequacy == null) setSeg("adequacy", 5);
    if (state.sel.fluency == null) setSeg("fluency", 5);
  }
  else { s.delete("no_error"); s.has(e) ? s.delete(e) : s.add(e); }
  document.querySelectorAll("#chips-errors .chip").forEach(c =>
    c.classList.toggle("sel", s.has(c.dataset.v)));
}

let submitting = false;
async function submit() {
  const { sel, task } = state;
  if (task?.task_type === "speech") return window.submitSpeech();
  if (submitting || !task) return;
  if (!sel.errors.size) return toast("pick error types (0 = no error)", true);
  if (!sel.severity) return toast("pick severity (v)", true);
  if (!sel.adequacy || !sel.fluency) return toast("rate adequacy (1-5) and fluency (⇧1-5)", true);
  if (sel.errors.has("no_error") && sel.severity !== "neutral")
    return toast("no_error requires severity neutral (server rule)", true);
  if (!sel.errors.has("no_error") && sel.severity === "neutral")
    return toast("a real error cannot be neutral — pick severity (v)", true);
  if (sel.severity === "critical" && !$("#note").value.trim()) {
    $("#note").focus();
    return toast("critical requires a note (x)", true);
  }
  submitting = true;
  try {
    await api("/submit", { annotator: state.annotator, assignment_id: task.assignment_id,
      error_types: [...sel.errors], worst_severity: sel.severity,
      adequacy: sel.adequacy, fluency: sel.fluency,
      correction: $("#correction").value, note: $("#note").value,
      elapsed_ms: Date.now() - state.startTs });
    toast("saved ✓");
    state.task = null;
    claimNext();
  } catch (e) { toast(e.message, true); }
  finally { submitting = false; }
}
async function skip() {
  if (!state.task) return;
  const task = state.task, annotator = state.annotator;
  try { await api("/skip", { annotator, assignment_id: task.assignment_id });
        toast("skipped");
        if (state.task === task && state.annotator === annotator) claimNext();
  } catch (e) { toast(e.message, true); }
}
async function undo() {
  const annotator = state.annotator, serial = ++claimSerial;
  try {
    const r = await api("/undo", { annotator });
    if (annotator !== state.annotator || serial !== claimSerial) return;
    if (!r.task) return toast("nothing to undo", true);
    state.batchId = r.task.batch_id;
    $("#batch-select").value = state.batchId;
    state.progress.done = Math.max(0, state.progress.done - 1);
    renderTask(r.task); toast("reopened — previous row kept (append-only)");
  } catch (e) { toast(e.message, true); }
}
async function showGuideline() {
  const g = await api("/guideline");
  openModal(`<pre>${esc(g.text)}</pre>`);
}

/* ---------- keyboard ---------- */
document.addEventListener("keydown", e => {
  if (state.task?.task_type === "speech" && $("#view-annotate").classList.contains("active") &&
      $("#modal").classList.contains("hidden") && e.altKey && e.key.toLowerCase() === "p") {
    e.preventDefault(); const player = $("#speech-player");
    if (player.paused) player.play().catch(err => toast(err.message, true)); else player.pause();
    return;
  }
  if (e.key === "Escape") {
    if (e.target.matches("input, textarea")) return e.target.blur();
    return $("#modal").classList.add("hidden");
  }
  if (e.target.matches("input, textarea, select")) return;
  if (!$("#view-annotate").classList.contains("active") || !state.task) {
    if (e.key === "?") showGuideline();
    return;
  }
  if (state.task.task_type === "speech") {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); window.submitSpeech();
    } else if (e.key === "u") undo();
    else if (e.key === "c") $("#speech-correction").focus();
    return;
  }
  if (/^[1-5]$/.test(e.key) && !e.shiftKey) return setSeg("adequacy", +e.key);
  if (e.shiftKey && /^[!@#$%]$/.test(e.key))
    return setSeg("fluency", {"!":1,"@":2,"#":3,"$":4,"%":5}[e.key]);
  const err = ERRORS.find(([k]) => k === e.key.toLowerCase() && !e.metaKey && !e.ctrlKey);
  if (err && e.key !== "s" && e.key !== "u") return toggleError(err[1]);
  switch (e.key) {
    case "0": return toggleError("no_error");
    case "v": {
      const i = SEVERITIES.indexOf(state.sel.severity);
      return setSeg("severity", SEVERITIES[(i + 1) % SEVERITIES.length]);
    }
    case " ": e.preventDefault(); return submit();
    case "u": return undo();
    case "s": return skip();
    case "c": e.preventDefault(); return $("#correction")?.focus();
    case "x": e.preventDefault(); return $("#note")?.focus();
    case "e": return $("#chips-errors")?.scrollIntoView({behavior:"smooth", block:"center"});
    case "?": return showGuideline();
  }
});

window.ANOTA = { api, esc, toast, state };
window.ANOTA.setSpeechSort = (sort) => {
  state.speechSort = ["ml", "qc", "fifo"].includes(sort) ? sort : "ml";
  localStorage.setItem("anota_speech_sort", state.speechSort);
};
window.ANOTA.reviewSpeechTask = async (taskId, batchId, sort) => {
  if (!state.annotator) return toast("Enter annotator id and press Start.", true);
  window.ANOTA.setSpeechSort(sort);
  if (state.task) {
    document.querySelector('.tab[data-tab="annotate"]').click();
    if (state.task.task_id !== taskId) toast("Finish or skip your current task first.", true);
    return;
  }
  state.batchId = Number(batchId);
  $("#batch-select").value = state.batchId;
  const url = new URL(location.href);
  url.searchParams.set("batch", state.batchId);
  history.replaceState(null, "", url);
  document.querySelector('.tab[data-tab="annotate"]').click();
  await claimNext(taskId);
};
window.ANOTA.annotateBatch = (id) => {
  state.batchId = id;
  document.querySelector('.tab[data-tab="annotate"]').click();
  if (state.annotator) claimNext(); else toast("enter annotator id and press Start");
};
if (state.annotator) claimNext();
