/* PropioQA Workbench — core + Annotate tab. Review/Dashboard live in review.js/dash.js. */
"use strict";

const state = { annotator: localStorage.getItem("pqa_annotator") || "",
                task: null, startTs: 0, sel: null, timerId: null };

const $ = (s, el) => (el || document).querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, body) {
  const r = await fetch("/api" + path, body === undefined ? {} :
    { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.status;
    try { const j = await r.json(); msg = Array.isArray(j.detail) ? (j.detail[0]?.msg || msg) : (j.detail || msg); } catch {}
    throw new Error(msg);
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

/* ---------- theme + health ---------- */
$("#theme-btn").onclick = () => {
  const cur = document.documentElement.dataset.theme === "light" ? "" : "light";
  document.documentElement.dataset.theme = cur; localStorage.setItem("pqa_theme", cur);
};
document.documentElement.dataset.theme = localStorage.getItem("pqa_theme") || "";
async function health() {
  try {
    const h = await api("/health");
    const b = $("#judge-badge");
    b.textContent = "judge: " + h.judge.mode + (h.judge.reachable ? "" : " (offline)");
    b.className = "badge " + (h.judge.mode === "mock" ? "mock" : h.judge.reachable ? "on" : "off");
  } catch { $("#judge-badge").className = "badge off"; }
}
health();

/* ---------- annotate ---------- */
const ERRORS = [["m","mistranslation"],["o","omission"],["a","addition"],["t","terminology"],
                ["n","number_unit"],["g","negation_polarity"],["r","grammar"],["p","punctuation"]];
const SEVERITIES = ["neutral","minor","major","critical"];

function freshSel() { return { errors: new Set(), severity: null, adequacy: null, fluency: null }; }

$("#annotator").value = state.annotator;
$("#start-btn").onclick = start;
function start() {
  state.annotator = $("#annotator").value.trim();
  if (!state.annotator) return toast("annotator id required", true);
  localStorage.setItem("pqa_annotator", state.annotator);
  claimNext();
}

async function claimNext() {
  try {
    const r = await api("/claim", { annotator: state.annotator });
    state.progress = r.progress;
    renderTask(r.task);
  } catch (e) {
    toast("claim failed: " + e.message, true);
    $("#view-annotate").innerHTML = `<div class="empty">claim failed — <button id="btn-retry" class="primary">Retry</button></div>`;
    $("#btn-retry").onclick = claimNext;
  }
}

function renderTask(task) {
  clearInterval(state.timerId);
  state.task = task; state.sel = freshSel(); state.startTs = Date.now();
  const v = $("#view-annotate");
  if (!task) { v.innerHTML = `<div class="empty">🎉 Queue empty — nothing left to annotate.</div>`; return; }
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
  if (e === "no_error") { s.clear(); s.add("no_error"); setSeg("severity", "neutral"); }
  else { s.delete("no_error"); s.has(e) ? s.delete(e) : s.add(e); }
  document.querySelectorAll("#chips-errors .chip").forEach(c =>
    c.classList.toggle("sel", s.has(c.dataset.v)));
}

async function submit() {
  const { sel, task } = state;
  if (!task) return;
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
}
async function skip() {
  if (!state.task) return;
  try { await api("/skip", { annotator: state.annotator, assignment_id: state.task.assignment_id });
        toast("skipped"); claimNext(); } catch (e) { toast(e.message, true); }
}
async function undo() {
  try {
    const r = await api("/undo", { annotator: state.annotator });
    if (!r.task) return toast("nothing to undo", true);
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
  if (e.key === "Escape") return $("#modal").classList.add("hidden");
  if (e.target.matches("input, textarea, select")) return;
  if (!$("#view-annotate").classList.contains("active") || !state.task) {
    if (e.key === "?") showGuideline();
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

window.PQA = { api, esc, toast, state };
if (state.annotator) claimNext();
