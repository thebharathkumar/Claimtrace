/* claimtrace viewer: renders the latest run and the drill-down trail. */

let RUN = null;

const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function refHtml(chunkId) {
  return `<span class="ref" data-chunk="${esc(chunkId)}">${esc(chunkId)}</span>`;
}

function renderBrief(brief) {
  const withRefs = esc(brief).replace(/\[ref:([^\]\s]+)\]/g, (_, id) => refHtml(id));
  const lines = withRefs.split("\n");
  if (lines.length && lines[0].startsWith("Not investment advice")) {
    lines[0] = `<span class="notice">${lines[0]}</span>`;
  }
  $("#brief").innerHTML = lines.join("\n");
}

function renderContradictions(checks) {
  const el = $("#contradictions");
  const contras = checks.filter((c) => c.status === "contradiction");
  if (!contras.length) {
    el.innerHTML = '<p class="section-note">No contradictions in this run.</p>';
    return;
  }
  el.innerHTML = contras.map((c) => `
    <div class="contra-card">
      <h3>${esc(label(c.claim_type))}</h3>
      <div class="contra-grid">
        ${c.detail.values.map((v) => `
          <div class="contra-value">
            <div class="val">${esc(v.value)}</div>
            <div class="src">
              ${v.sources.map(esc).join(", ")}<br/>
              ${v.claims.map((cl) => refHtml(cl.chunk_id)).join(" ")}
            </div>
          </div>`).join("")}
      </div>
    </div>`).join("");
}

const LABELS = {
  valuation: "Valuation", raise_size: "Raise size", shares_offered: "Shares offered",
  offer_price: "Offer price", listing_date: "Listing date", ticker: "Ticker",
  exchange: "Exchange", free_float: "Free float", retail_allocation: "Retail allocation",
  annual_revenue: "Annual revenue", net_loss: "Net loss",
};
function label(t) { return LABELS[t] || t; }

function renderClaims(checks) {
  const tbody = $("#claims-table tbody");
  tbody.innerHTML = checks.map((c) => {
    const value = c.status === "contradiction"
      ? c.detail.values.map((v) => esc(v.value)).join(" / ")
      : esc(c.agreed_value || "");
    return `<tr data-type="${esc(c.claim_type)}">
      <td>${esc(label(c.claim_type))}</td>
      <td>${value}</td>
      <td><span class="badge ${esc(c.status)}">${esc(c.status.replace("_", " "))}</span></td>
      <td>${c.detail.source_count}</td>
      <td><span class="conf-bar" style="width:${Math.round(c.confidence * 60)}px"></span>${c.confidence.toFixed(2)}</td>
    </tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => openClaimDrill(tr.dataset.type));
  });
}

function renderTimeline(spans) {
  const el = $("#timeline");
  if (!spans.length) { el.innerHTML = '<p class="section-note">No spans recorded.</p>'; return; }
  const t0 = Math.min(...spans.map((s) => s.start_ns));
  const t1 = Math.max(...spans.map((s) => s.end_ns));
  const total = Math.max(t1 - t0, 1);
  el.innerHTML = spans.map((s) => {
    const left = ((s.start_ns - t0) / total) * 100;
    const width = ((s.end_ns - s.start_ns) / total) * 100;
    const ms = Math.round((s.end_ns - s.start_ns) / 1e6);
    const tok = (s.attrs.input_tokens || 0) + (s.attrs.output_tokens || 0);
    const ok = s.attrs.succeeded !== false;
    return `<div class="span-row">
      <div class="span-name">${esc(s.name)}</div>
      <div class="span-track"><div class="span-bar" style="left:${left}%;width:${width}%"></div></div>
      <div class="span-stats">${ms} ms · ${tok} tokens · ${ok ? "ok" : "failed"}</div>
    </div>`;
  }).join("");
}

function renderAudit(entries) {
  $("#audit-log").innerHTML = entries.map((e) => `
    <div class="audit-entry">
      <span class="seq">#${e.seq}</span>
      <span class="type">${esc(e.entry_type)}</span>
      <span class="hash" title="${esc(e.entry_hash)}">${esc(e.entry_hash)}</span>
      <span class="seq">${esc(e.ts)}</span>
    </div>`).join("");
}

async function openChunkDrill(chunkId, claim) {
  const chunk = await fetch(`/api/runs/${RUN.run_id}/chunks/${encodeURIComponent(chunkId)}`).then((r) => r.json());
  const claims = RUN.claims.filter((c) => c.chunk_id === chunkId);
  const focus = claim || claims[0];
  let chunkHtml = esc(chunk.text);
  if (focus) {
    const local = [focus.span_start - chunk.span_start, focus.span_end - chunk.span_start];
    if (local[0] >= 0 && local[1] <= chunk.text.length) {
      chunkHtml = esc(chunk.text.slice(0, local[0]))
        + "<mark>" + esc(chunk.text.slice(local[0], local[1])) + "</mark>"
        + esc(chunk.text.slice(local[1]));
    }
  }
  $("#drill-body").innerHTML = `
    ${focus ? `
      <div class="drill-field"><div class="k">claim</div><div class="v">${esc(label(focus.claim_type))}: ${esc(focus.value_text)}</div></div>
      <div class="drill-field"><div class="k">char span in source</div><div class="v">${focus.span_start} to ${focus.span_end}</div></div>
      <div class="drill-field"><div class="k">retrieval score</div><div class="v">${focus.retrieval_score}</div></div>
      <div class="drill-field"><div class="k">audit entry hash</div><div class="v">${esc(focus.audit_hash || "")}</div></div>
    ` : ""}
    <div class="drill-field"><div class="k">source chunk ${esc(chunkId)} (${esc(chunk.source_id)})</div>
      <div class="drill-chunk">${chunkHtml}</div></div>
    ${claims.length > 1 ? `<div class="drill-field"><div class="k">all claims from this chunk</div><div class="v">${claims.map((c) => esc(c.claim_type + ": " + c.value_text)).join("<br/>")}</div></div>` : ""}
  `;
  $("#drilldown").classList.remove("hidden");
}

function openClaimDrill(claimType) {
  const check = RUN.checks.find((c) => c.claim_type === claimType);
  if (!check) return;
  const first = check.detail.values[0].claims[0];
  const claim = RUN.claims.find((c) => c.claim_id === first.claim_id);
  openChunkDrill(first.chunk_id, claim);
}

function wireRefs() {
  document.body.addEventListener("click", (ev) => {
    const ref = ev.target.closest(".ref");
    if (!ref) return;
    const chunkId = ref.dataset.chunk;
    const claim = RUN.claims.find((c) => c.chunk_id === chunkId);
    openChunkDrill(chunkId, claim);
  });
}

async function loadRun() {
  const resp = await fetch("/api/runs/latest");
  if (!resp.ok) {
    $("#brief").textContent = "No completed runs yet. Click run pipeline above, or use: claimtrace demo";
    return;
  }
  RUN = await resp.json();
  $("#run-meta").innerHTML = `
    run <span class="mode">${esc(RUN.run_id)}</span> · mode <span class="mode">${esc(RUN.mode)}</span> · ${esc(RUN.created_at)}<br/>
    <button id="btn-run" class="action">run pipeline</button>`;
  wireRunButton();
  $("#triage-link").href = RUN.triage_dashboard || "#";
  renderBrief(RUN.brief || "");
  renderContradictions(RUN.checks);
  renderClaims(RUN.checks);
  renderTimeline(RUN.spans);
  const audit = await fetch(`/api/runs/${RUN.run_id}/audit`).then((r) => r.json());
  renderAudit(audit);
}

function wireRunButton() {
  const btn = $("#btn-run");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.textContent = "running…";
    btn.disabled = true;
    await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_seed: true }),
    });
    await loadRun();
  });
}

function wireAuditButtons() {
  $("#btn-verify").addEventListener("click", async () => {
    const res = await fetch("/api/verify-chain", { method: "POST" }).then((r) => r.json());
    const el = $("#chain-status");
    if (res.ok) {
      el.className = "ok";
      el.textContent = `chain intact · ${res.entries} entries verified`;
    } else {
      el.className = "bad";
      el.textContent = `TAMPERED · first broken entry: #${res.first_invalid_seq}`;
    }
  });
  $("#btn-tamper").addEventListener("click", async () => {
    const res = await fetch("/api/demo/tamper", { method: "POST" }).then((r) => r.json());
    const el = $("#chain-status");
    el.className = "bad";
    el.textContent = res.tampered ? `flipped one byte in entry #${res.seq}. now verify.` : "nothing to tamper with";
  });
  $("#btn-restore").addEventListener("click", async () => {
    await fetch("/api/demo/restore", { method: "POST" });
    const el = $("#chain-status");
    el.className = "";
    el.textContent = "restored original payloads";
  });
}

$("#drill-close").addEventListener("click", () => $("#drilldown").classList.add("hidden"));
wireRefs();
wireAuditButtons();
wireRunButton();
loadRun();
