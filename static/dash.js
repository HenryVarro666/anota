/* Ops dashboard: throughput, golden accuracy, agreement, error×arm matrix, routing builder. */
"use strict";
(() => {
  const { api, esc, toast } = window.PQA;
  let pollId = null;

  window.stopDashPoll = () => { clearInterval(pollId); pollId = null; };
  window.renderDashboard = async function (startPoll) {
    const v = document.querySelector("#view-dashboard");
    let o, m, a, g, b;
    try {
      [o, m, a, g, b] = await Promise.all([api("/stats/overview"), api("/stats/matrix"),
        api("/stats/annotators"), api("/stats/agreement"), api("/batches")]);
    } catch (e) { v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    v.innerHTML = `
      <div class="cards">
        ${stat(o.n_tasks, "tasks")} ${stat(o.n_annotations, "annotations")}
        ${stat(o.n_batches, "batches")}
        ${stat(o.judge.mode + (o.judge.reachable ? "" : " ⚠"), "judge")}
      </div>
      <div class="card"><h3>Throughput — last ${o.recent_elapsed_ms.length} annotations</h3>
        ${sparkline(o.recent_elapsed_ms)}</div>
      <div class="card"><h3>Error type × latency arm</h3>${matrix(m)}
        <div class="anchor-note">rate = share of tasks in arm carrying the error type
        (human label first, judge fallback; sources: human ${m.sources.human} / judge ${m.sources.judge})</div></div>
      <div class="card"><h3>Annotators</h3><table><tr><th>annotator</th><th>submitted</th>
        <th>avg time</th><th>golden</th></tr>
        ${a.map(s => `<tr><td>${esc(s.annotator)}</td><td>${s.n_submitted}</td>
          <td>${(s.avg_elapsed_ms/1000).toFixed(1)}s</td>
          <td>${s.golden_total ? s.golden_passed + "/" + s.golden_total : "—"}</td></tr>`).join("")}
      </table></div>
      <div class="card"><h3>Agreement</h3>
        ${g.pairwise.length ? g.pairwise.map(p => `<div>${esc(p.a)} × ${esc(p.b)} (n=${p.n}):
          κ<sub>sev</sub> <b>${p.kappa_severity}</b> · κ<sub>bin</sub> <b>${p.kappa_binary}</b></div>`).join("")
          : `<div class="anchor-note">pairwise κ needs ≥3 shared tasks between two annotators</div>`}
        ${g.judge_human ? `<div style="margin-top:6px">judge × human (n=${g.judge_human.n}):
          κ<sub>sev</sub> <b>${g.judge_human.kappa_severity}</b> ·
          κ<sub>bin</sub> <b>${g.judge_human.kappa_binary}</b></div>` : ""}</div>
      <div class="card"><h3>Batches & routing</h3>
        <table><tr><th>batch</th><th>tasks</th><th>overlap</th><th>suggestions</th></tr>
        ${b.map(x => `<tr><td>${esc(x.name)}</td><td>${x.n_tasks}</td><td>${x.overlap}</td>
          <td>${x.show_suggestions ? "ON" : "off"}</td></tr>`).join("")}</table>
        <div class="form-inline" style="margin-top:10px">
          <label>route top</label><input type="number" id="rt-n" value="10" min="1">
          <select id="rt-signal"><option value="judge_confidence">lowest judge confidence</option>
            <option value="lf_conflict">most LF errors</option></select>
          <button class="primary" id="rt-build">Build routing batch</button>
          <button id="ex-btn">Export snapshot</button>
        </div></div>`;
    v.querySelector("#rt-build").onclick = async () => {
      try {
        const r = await api("/routing/build",
          { top_n: +v.querySelector("#rt-n").value, signal: v.querySelector("#rt-signal").value });
        toast(`${r.name}: ${r.n} tasks — suggestions ON for this batch`);
        window.renderDashboard();
      } catch (e) { toast(e.message, true); }
    };
    v.querySelector("#ex-btn").onclick = async () => {
      try { const r = await api("/export", {}); toast(`${r.version} → sha ${r.sha256.slice(0, 12)}…`); }
      catch (e) { toast(e.message, true); }
    };
    if (startPoll && !pollId) pollId = setInterval(() => window.renderDashboard(), 5000);
  };

  const stat = (n, l) => `<div class="card stat"><div class="num">${esc(n)}</div><div class="lbl">${l}</div></div>`;

  function sparkline(xs) {
    if (!xs.length) return `<div class="anchor-note">no timed annotations yet</div>`;
    const max = Math.max(...xs), w = 600, h = 40;
    const pts = xs.map((x, i) => `${(i / Math.max(1, xs.length - 1)) * w},${h - (x / max) * (h - 4)}`);
    return `<svg class="sparkline" viewBox="0 0 ${w} ${h + 4}" preserveAspectRatio="none">
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts.join(" ")}"/></svg>`;
  }

  function matrix(m) {
    if (!m.arms.length) return `<div class="anchor-note">no labels yet</div>`;
    const cols = `140px repeat(${m.arms.length}, 1fr)`;
    let html = `<div class="matrix" style="grid-template-columns:${cols}">`;
    html += `<div class="hcell"></div>` + m.arms.map(a =>
      `<div class="hcell" style="text-align:center">${esc(a)} (n=${m.n[a]})</div>`).join("");
    for (const e of m.error_types) {
      html += `<div class="hcell">${esc(e)}</div>`;
      for (const a of m.arms) {
        const r = m.cells[a][e];
        const alpha = r > 0 ? 0.10 + 0.75 * r : 0.03;
        html += `<div class="cell" style="background:rgba(91,141,255,${alpha.toFixed(2)})">
                 ${(r * 100).toFixed(0)}%</div>`;
      }
    }
    return html + `</div>`;
  }
})();
