/* Review tab: three-way comparison (human vs judge vs LF) + adjudication. */
"use strict";
(() => {
  const { api, esc, toast } = window.PQA;
  const SEVS = ["neutral", "minor", "major", "critical"];
  const ETYPES = ["no_error","mistranslation","omission","addition","terminology",
                  "number_unit","negation_polarity","grammar","punctuation"];
  let queue = [], selIdx = -1;

  window.renderReview = async function () {
    const v = document.querySelector("#view-review");
    try { queue = await api("/review/queue"); }
    catch (e) { v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (!queue.length) { v.innerHTML = `<div class="empty">Review queue is empty ✓</div>`; return; }
    v.innerHTML = `<div class="review-grid">
      <div class="card" id="rq-list" style="padding:6px">
        ${queue.map((q, i) => `<div class="rq-item" data-i="${i}">
           <b>${esc(q.task.id)}</b> · ${esc(q.annotation.annotator)}
           <span class="dis">Δ ${q.disagreement}</span></div>`).join("")}
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
    };
    d.querySelector("#rv-approve").onclick = () => verdict(q, { reviewer: "lead", verdict: "approved" });
    d.querySelector("#rv-overturn-toggle").onclick = () =>
      d.querySelector("#rv-form").classList.toggle("hidden");
    d.querySelector("#rv-overturn").onclick = () => {
      const caseNote = d.querySelector("#rv-case").value.trim();
      if (!chosen.size) return toast("pick replacement error types", true);
      if (!caseNote) return toast("case note required — it feeds the guideline", true);
      const sev = d.querySelector("#rv-sev").value;
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
