/* Review tab: three-way comparison (human vs judge vs LF) + adjudication. */
"use strict";
(() => {
  const { api, esc, toast } = window.ANOTA;
  const SEVS = ["neutral", "minor", "major", "critical"];
  const ETYPES = ["no_error","mistranslation","omission","addition","terminology",
                  "number_unit","negation_polarity","grammar","punctuation"];
  let queue = [], selIdx = -1, reviewSerial = 0, speechSerial = 0;
  let speechBatch = window.ANOTA.state.batchId || "";

  async function loadSpeechQueue() {
    const serial = ++speechSerial;
    const holder = document.querySelector("#speech-rq-items");
    if (!holder) return;
    const { state } = window.ANOTA;
    const annotator = state.annotator, sort = state.speechSort;
    if (!annotator) {
      holder.innerHTML = '<div class="empty">Enter annotator id and press Start to review speech.</div>';
      return;
    }
    holder.innerHTML = '<div class="empty">Loading speech queue…</div>';
    const params = new URLSearchParams({ annotator, sort, limit: "200" });
    if (speechBatch) params.set("batch_id", speechBatch);
    try {
      const result = await api("/speech/review/queue?" + params);
      if (serial !== speechSerial || !holder.isConnected || annotator !== state.annotator) return;
      const items = result.items || [];
      holder.innerHTML = `<p class="speech-hint">${esc(result.total)} eligible tasks · showing ${items.length}. Opening an item starts its annotation lease.</p>
        ${items.length ? '<button class="primary" id="speech-rq-next">Review next</button>' : '<div class="empty">No eligible speech tasks in this selection.</div>'}
        <div class="speech-rq-list">${items.map((item, i) => {
          const r = item.ranking;
          const label = r ? (r.method === "ml" ? "ML" + (r.arm ? " · " + r.arm : "") : r.method === "qc" ? "QC" : "Original order") : "Original order";
          const fallback = r?.method === "qc" && r.reason ? "QC fallback: ML ranking unavailable for this queue." : "";
          const density = r?.method === "ml" && Number.isFinite(r.density) ? `${r.density.toFixed(3)} estimated edits/s` : "";
          const taskLabel = String(item.task_id).length > 18 ? String(item.task_id).slice(0, 18) + "…" : item.task_id;
          return `<article class="speech-rq-item"><div><b>${esc(item.source || "Speech task")}</b><span class="speech-rq-batch">${esc(item.batch_name)}</span>
            <small title="${esc(item.task_id)}">${esc(taskLabel)}</small><small>${esc(label)}${Number.isFinite(item.duration_s) ? " · " + esc(item.duration_s.toFixed(1)) + " s" : ""}</small>
            ${r && Number.isFinite(r.qc_score) ? `<small>QC score ${esc(r.qc_score.toFixed(2))}</small>` : ""}${density ? `<small>${esc(density)}</small>` : ""}${fallback ? `<small>${esc(fallback)}</small>` : ""}</div>
            <button data-open="${i}">Open</button></article>`;
        }).join("")}</div>`;
      const open = async index => {
        const item = items[index]; if (!item) return;
        holder.querySelectorAll("button").forEach(b => b.disabled = true);
        await window.ANOTA.reviewSpeechTask(item.task_id, item.batch_id, sort);
        if (holder.isConnected) holder.querySelectorAll("button").forEach(b => b.disabled = false);
      };
      holder.querySelector("#speech-rq-next")?.addEventListener("click", () => open(0));
      holder.querySelectorAll("[data-open]").forEach(b => b.onclick = () => open(Number(b.dataset.open)));
    } catch (e) {
      if (serial !== speechSerial || !holder.isConnected) return;
      holder.innerHTML = `<div class="empty">Speech queue unavailable: ${esc(e.message)} <button id="speech-rq-retry">Retry</button></div>`;
      holder.querySelector("#speech-rq-retry").onclick = loadSpeechQueue;
    }
  }

  window.renderReview = async function () {
    const serial = ++reviewSerial;
    const root = document.querySelector("#view-review");
    root.innerHTML = `<section class="card speech-rq"><h2>Speech review</h2>
      <div class="speech-rq-controls"><label>Order <select id="speech-rq-sort"><option value="ml">ML priority</option><option value="qc">QC priority</option><option value="fifo">Original order</option></select></label>
      <label>Batch <select id="speech-rq-batch"><option value="">All batches</option></select></label>
      <button id="speech-rq-refresh">Refresh</button>${window.ANOTA.state.task ? '<button class="primary" id="speech-rq-resume">Resume current task</button>' : ""}</div><div id="speech-rq-items"></div></section>
      <h2>Translation adjudication</h2><div id="translation-review"><div class="empty">Loading adjudication queue…</div></div>`;
    const sortSelect = root.querySelector("#speech-rq-sort");
    sortSelect.value = window.ANOTA.state.speechSort;
    sortSelect.onchange = () => { window.ANOTA.setSpeechSort(sortSelect.value); loadSpeechQueue(); };
    root.querySelector("#speech-rq-refresh").onclick = loadSpeechQueue;
    root.querySelector("#speech-rq-resume")?.addEventListener("click", () => {
      document.querySelector('.tab[data-tab="annotate"]').click();
    });
    const batchSelect = root.querySelector("#speech-rq-batch");
    batchSelect.innerHTML = document.querySelector("#batch-select").innerHTML;
    batchSelect.value = speechBatch;
    batchSelect.onchange = () => { speechBatch = batchSelect.value; loadSpeechQueue(); };
    loadSpeechQueue();
    const v = root.querySelector("#translation-review");
    try { queue = await api("/review/queue"); }
    catch (e) { if (serial === reviewSerial) v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (serial !== reviewSerial) return;
    if (!queue.length) { v.innerHTML = `<div class="empty">Review queue is empty ✓</div>`; return; }
    v.innerHTML = `<div class="review-grid">
      <div class="card" id="rq-list">
        ${queue.map((q, i) => `<div class="rq-item" data-i="${i}">
           ${q.disagreement >= 3 ? `<span class="dot high"></span>`
             : q.disagreement > 0 ? `<span class="dot mid"></span>` : ""}
           <b>${esc(q.task.id)}</b><span>${esc(q.annotation.annotator)}</span>
           <span class="dis">Δ ${esc(q.disagreement)}</span></div>`).join("")}
      </div>
      <div class="card" id="rq-detail"><div class="empty">Select an item.</div></div></div>`;
    v.querySelector("#rq-list").onclick = e => {
      const it = e.target.closest(".rq-item"); if (!it) return;
      selIdx = +it.dataset.i;
      v.querySelectorAll(".rq-item").forEach(x => x.classList.toggle("sel", x === it));
      renderDetail(queue[selIdx]);
    };
  };

  function col(title, body) { return `<div class="col"><h4>${title}</h4>${body}</div>`; }
  function labelBlock(x) {
    return `severity <b>${esc(x.worst_severity)}</b><br>
            types ${x.error_types.map(esc).join(", ") || "—"}<br>
            ${x.adequacy !== undefined ? `adequacy ${x.adequacy}` : ""}
            ${x.note ? `<br><i>${esc(x.note)}</i>` : ""}`;
  }

  function renderDetail(q) {
    const d = document.querySelector("#rq-detail");
    const judge = q.judge, disagree = judge && judge.worst_severity !== q.annotation.worst_severity;
    d.innerHTML = `
      <div class="textpanel"><div class="lbl">Source</div><div class="txt">${esc(q.task.source)}</div></div>
      <div class="textpanel"><div class="lbl">Hypothesis</div><div class="txt">${esc(q.task.hypothesis)}</div></div>
      ${q.task.reference ? `<details class="ref"><summary>reference</summary>
        <div class="textpanel"><div class="txt">${esc(q.task.reference)}</div></div></details>` : ""}
      <div class="threeway">
        ${col("Human · " + esc(q.annotation.annotator), labelBlock(q.annotation))}
        ${col("Judge" + (judge?.is_mock ? " · MOCK" : ""),
              judge ? `<span class="${disagree ? "disagree" : ""}">${labelBlock(judge)}</span>
                       <br>conf ${judge.confidence}` : "—")}
        ${col("LF lint", q.lf_errors.length
              ? q.lf_errors.map(f => `⚠ ${esc(f.lf)}<br><i>${esc(f.evidence)}</i>`).join("<br>") : "clean")}
      </div>
      <div class="actions">
        <button class="primary" id="rv-approve">Approve</button>
        <button id="rv-overturn-toggle">Overturn…</button>
      </div>
      <div id="rv-form" class="hidden" style="margin-top:12px">
        <div class="row">
          <div class="field"><label>severity</label>
            <select id="rv-sev">${SEVS.map(s=>`<option>${s}</option>`).join("")}</select></div>
          <div class="field"><label>adequacy</label>
            <select id="rv-adq">${[1,2,3,4,5].map(n=>`<option>${n}</option>`).join("")}</select></div>
          <div class="field"><label>fluency</label>
            <select id="rv-flu">${[1,2,3,4,5].map(n=>`<option>${n}</option>`).join("")}</select></div>
        </div>
        <div class="chips">${ETYPES.map(t=>`<button class="chip" data-v="${t}">${t}</button>`).join("")}</div>
        <div class="field"><label>case note → guideline appendix (required)</label>
          <textarea id="rv-case" rows="2"></textarea></div>
        <button class="primary" id="rv-overturn">Submit overturn</button>
      </div>`;
    const chosen = new Set();
    d.querySelector(".chips").onclick = e => {
      const c = e.target.closest(".chip"); if (!c) return;
      const t = c.dataset.v;
      if (t === "no_error") { chosen.clear(); chosen.add(t); }
      else { chosen.delete("no_error"); chosen.has(t) ? chosen.delete(t) : chosen.add(t); }
      d.querySelectorAll(".chip").forEach(x => x.classList.toggle("sel", chosen.has(x.dataset.v)));
      const sevSel = d.querySelector("#rv-sev");
      if (chosen.has("no_error")) sevSel.value = "neutral";
      else if (chosen.size && sevSel.value === "neutral") sevSel.value = "major";
    };
    d.querySelector("#rv-approve").onclick = () => verdict(q, { reviewer: "lead", verdict: "approved" });
    d.querySelector("#rv-overturn-toggle").onclick = () =>
      d.querySelector("#rv-form").classList.toggle("hidden");
    d.querySelector("#rv-overturn").onclick = () => {
      const caseNote = d.querySelector("#rv-case").value.trim();
      if (!chosen.size) return toast("pick replacement error types", true);
      if (!caseNote) return toast("case note required — it feeds the guideline", true);
      const sevSel = d.querySelector("#rv-sev");
      let sev = sevSel.value;
      const noErrorOnly = chosen.size === 1 && chosen.has("no_error");
      if (!noErrorOnly && sev === "neutral")
        return toast("a real error cannot be neutral — pick a severity", true);
      if (noErrorOnly && sev !== "neutral") { sev = "neutral"; sevSel.value = "neutral"; }
      verdict(q, { reviewer: "lead", verdict: "overturned", case_note: caseNote,
        replacement: { error_types: [...chosen], worst_severity: sev,
          adequacy: +d.querySelector("#rv-adq").value, fluency: +d.querySelector("#rv-flu").value,
          correction: "", note: sev === "critical" ? caseNote : "" } });
    };
  }

  async function verdict(q, body) {
    try { await api(`/review/${q.annotation.id}`, body); toast("verdict recorded ✓"); window.renderReview(); }
    catch (e) { toast(e.message, true); }
  }
})();
