/* Glass Box — Agent Audit Console (vanilla JS, no build step).
 *
 * SECURITY UI RULE (PRD §9): external/untrusted strings — market data,
 * model-generated text, notes, flags — are rendered with textContent ONLY.
 * Nothing untrusted is ever injected with innerHTML.
 */
"use strict";

// ------------------------------------------------------------ DOM helpers
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function frag(hash) {
  if (!hash || hash.length < 10) return hash || "—";
  return hash.slice(0, 4) + "..." + hash.slice(-3);
}
function money(n) { return "$" + Number(n).toFixed(2); }
function fmtTime(iso) { return (iso || "").slice(11, 19); }

const STEP_DEFS = [
  ["CYCLE_STARTED", "CYCLE STARTED"],
  ["DATA_SNAPSHOT", "DATA SNAPSHOT"],
  ["ANALYSIS_AND_POLICY", "ANALYSIS + RISK REVIEW"],
  ["DECISION", "DECISION"],
  ["EXECUTION_RESULT", "EXECUTION"],
];

const state = {
  decisions: new Map(),   // decision_id -> summary
  order: [],              // most-recent first
  selected: null,
  follow: true,
  eventCount: 0,
  verification: null,     // last /api/chain/verify result
  verifying: false,
};

// ------------------------------------------------------------ header state
function renderAgentState(s) {
  const pill = document.getElementById("agent-state");
  pill.textContent = s;
  pill.className = "pill";
  if (s === "RUNNING") pill.classList.add("pill-running");
  else if (s === "HALTED") pill.classList.add("pill-halted");
  else pill.classList.add("pill-paused");
  document.getElementById("btn-resume").hidden = s !== "PAUSED";
  document.getElementById("btn-clear-halt").hidden = s !== "HALTED";
  document.getElementById("btn-tick").disabled = s !== "RUNNING";
}

function renderCounters(summary) {
  document.getElementById("cnt-cycles").textContent = summary.cycles;
  document.getElementById("cnt-execute").textContent = summary.execute;
  document.getElementById("cnt-block").textContent = summary.block;
  document.getElementById("cnt-escalate").textContent =
    summary.escalate + summary.halt;
}

// ---------------------------------------------------------- audit banner
function renderAuditBanner() {
  const banner = document.getElementById("audit-banner");
  const status = document.getElementById("audit-status");
  const detail = document.getElementById("audit-detail");
  detail.textContent = "";

  const v = state.verification;
  if (!v) {
    banner.className = "audit audit-neutral";
    status.textContent = "— NOT VERIFIED YET —";
    detail.textContent = "press VERIFY CHAIN to recompute every hash "
      + "and check sequence + previous-hash continuity";
    return;
  }
  if (v.valid) {
    banner.className = "audit audit-ok";
    status.textContent = "✓ VERIFIED";
    detail.textContent = v.checked + " / " + state.eventCount
      + " EVENTS · every hash recomputed from genesis · "
      + "sequence + previous-hash continuity intact";
  } else {
    banner.className = "audit audit-broken";
    status.textContent = "✕ CHAIN BROKEN";
    const kind = { sequence: "SEQUENCE BREAK", prev_hash: "CHAIN LINK BREAK",
                   record_hash: "CONTENT TAMPER" }[v.first_break?.kind] || "BREAK";
    status.textContent += "  ·  FIRST BREAK: SEQ " + v.first_break_seq;
    if (v.first_break && v.first_break.kind !== "sequence") {
      detail.appendChild(el("div", null, kind + " AT SEQ " + v.first_break.seq));
      detail.appendChild(el("div", null,
        "Expected: " + frag(v.first_break.expected)));
      detail.appendChild(el("div", null,
        "Actual:   " + frag(v.first_break.actual)));
    } else {
      detail.appendChild(el("div", null, kind));
      detail.appendChild(el("div", null,
        "Expected seq: " + v.first_break.expected
        + " · Actual seq: " + v.first_break.actual));
    }
  }
}

async function runVerification() {
  if (state.verifying) return;
  state.verifying = true;
  try {
    const res = await fetch("/api/chain/verify");
    state.verification = await res.json();
    renderAuditBanner();
  } finally {
    state.verifying = false;
  }
}

// ------------------------------------------------------------ trace pane
function outcomeClass(o) {
  return { EXECUTE: "oc-execute", BLOCK: "oc-block", ESCALATE: "oc-escalate",
           HALT: "oc-halt", HOLD: "oc-hold" }[o || ""] || "oc-pending";
}

function stepClassFor(eventType, outcome) {
  if (eventType === "DECISION") {
    if (outcome === "BLOCK") return "done block-step";
    if (outcome === "ESCALATE" || outcome === "HALT") return "done warn-step";
  }
  return "done";
}

function buildTraceCard(dec) {
  const card = el("div", "trace-card");
  card.dataset.decisionId = dec.decision_id;

  const head = el("div", "trace-head");
  head.appendChild(el("span", "trace-id", dec.decision_id));
  head.appendChild(el("span", "trace-sym", dec.symbol || "—"));
  head.appendChild(el("span", "outcome-chip " + outcomeClass(dec.outcome),
                      dec.outcome || "PENDING"));
  head.appendChild(el("span", "trace-time", fmtTime(dec.ts)));
  card.appendChild(head);

  const steps = el("div", "trace-steps");
  const seen = new Set(dec.event_types || []);
  for (const [type, label] of STEP_DEFS) {
    const s = el("div", "step");
    if (seen.has(type)) {
      s.className = "step " + stepClassFor(type, dec.outcome);
    } else if (type === "EXECUTION_RESULT" && dec.outcome
               && dec.outcome !== "EXECUTE" && dec.outcome !== "PENDING") {
      s.className = "step skipped";
      s.appendChild(el("div", "step-dot"));
      s.appendChild(el("span", null, "EXECUTION · n/a"));
      steps.appendChild(s);
      continue;
    }
    s.appendChild(el("div", "step-dot"));
    s.appendChild(el("span", null, label));
    steps.appendChild(s);
  }
  card.appendChild(steps);
  card.addEventListener("click", () => {
    state.follow = false;
    document.getElementById("follow-chip").hidden = false;
    selectDecision(dec.decision_id);
  });
  return card;
}

function rebuildTrace() {
  const list = document.getElementById("trace-list");
  list.textContent = "";
  for (const id of state.order) {
    const dec = state.decisions.get(id);
    if (!dec) continue;
    const card = buildTraceCard(dec);
    if (id === state.selected) card.classList.add("selected");
    list.appendChild(card);
  }
}

function markSelectedCard() {
  document.querySelectorAll(".trace-card").forEach((c) => {
    c.classList.toggle("selected",
      c.dataset.decisionId === state.selected);
  });
}

// ---------------------------------------------------------- detail pane
function sectionBox(label, extra) {
  const wrap = el("div", "section");
  const lab = el("div", "section-label", label);
  if (extra) lab.appendChild(extra);
  wrap.appendChild(lab);
  const box = el("div", "section-box");
  wrap.appendChild(box);
  return [wrap, box];
}

function kvTable(rows) {
  const t = el("table", "data");
  const tbody = el("tbody");
  for (const [k, v, mono] of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", "muted", k));
    const td = el("td", mono ? "mono" : null, v);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  t.appendChild(tbody);
  return t;
}

function renderDetail(detail) {
  const root = document.getElementById("detail");
  root.textContent = "";
  document.getElementById("detail-empty").hidden = true;
  root.hidden = false;

  const events = detail.events;
  const byType = {};
  for (const e of events) byType[e.event_type] = e;

  const analysis = byType.ANALYSIS_AND_POLICY?.payload || {};
  const snap = byType.DATA_SNAPSHOT?.payload || {};
  const decision = byType.DECISION?.payload || {};
  const execResult = byType.EXECUTION_RESULT?.payload || null;
  const proposal = analysis.proposal || {};
  const outcome = decision.action || "PENDING";

  // -- Decision header: action, symbol, notional, confidence -------------
  const header = el("div", "detail-header");
  header.appendChild(el("span", "detail-id", detail.decision_id));
  header.appendChild(el("span",
    "outcome-chip " + outcomeClass(outcome), outcome));
  header.appendChild(el("span", "detail-meta",
    (decision.proposal_action || proposal.action || "—") + " "
    + (decision.symbol || proposal.symbol || "—")));
  if (proposal.quote_notional_usdt !== undefined) {
    header.appendChild(el("span", "detail-meta",
      money(proposal.quote_notional_usdt) + " notional"));
  }
  if (analysis.confidence !== undefined) {
    header.appendChild(el("span", "detail-meta",
      "confidence " + Number(analysis.confidence).toFixed(2)));
  }
  root.appendChild(header);

  // -- What the agent saw -------------------------------------------------
  const [sawWrap, sawBox] = sectionBox("WHAT THE AGENT SAW");
  const srcChip = el("span", "src-chip", (snap.source || "simulator").toUpperCase());
  sawWrap.querySelector(".section-label").appendChild(srcChip);
  const books = snap.symbols || {};
  const t = el("table", "data");
  const thead = el("thead");
  const htr = el("tr");
  for (const h of ["SYMBOL", "BID", "ASK", "MID", "SPREAD (BPS)", "TS"]) {
    htr.appendChild(el("th", h === "SYMBOL" ? null : "num", h));
  }
  thead.appendChild(htr);
  t.appendChild(thead);
  const tbody = el("tbody");
  for (const sym of Object.keys(books)) {
    const b = books[sym];
    const tr = el("tr");
    tr.appendChild(el("td", "mono", sym));
    tr.appendChild(el("td", "num", Number(b.bid).toFixed(2)));
    tr.appendChild(el("td", "num", Number(b.ask).toFixed(2)));
    tr.appendChild(el("td", "num", Number(b.mid).toFixed(2)));
    tr.appendChild(el("td", "num", Number(b.spread_bps).toFixed(2)));
    tr.appendChild(el("td", "num", fmtTime(snap.ts)));
    tbody.appendChild(tr);
  }
  t.appendChild(tbody);
  sawBox.appendChild(t);
  if (snap.note) {
    const flags = proposal.flags || [];
    const note = el("div",
      "snapshot-note" + (flags.length ? " flagged-note" : ""));
    note.appendChild(el("span", "note-tag",
      flags.length ? "UNTRUSTED INPUT · FLAGGED" : "UNTRUSTED INPUT"));
    note.appendChild(el("span", null, snap.note));
    sawBox.appendChild(note);
  }
  root.appendChild(sawWrap);

  // -- Model proposal: thesis + factors + confidence ----------------------
  const [propWrap, propBox] = sectionBox("MODEL PROPOSAL — ADVISORY, NOT SOVEREIGN");
  propBox.appendChild(el("div", "thesis",
    "“" + (proposal.thesis_plain_english || "—") + "”"));
  const factors = proposal.factors || [];
  if (factors.length) {
    const ft = el("table", "data");
    const fthead = el("thead");
    const ftr = el("tr");
    ftr.appendChild(el("th", null, "FACTOR"));
    ftr.appendChild(el("th", null, "OBSERVED VALUE"));
    fthead.appendChild(ftr);
    ft.appendChild(fthead);
    const fbody = el("tbody");
    for (const f of factors) {
      const tr = el("tr");
      tr.appendChild(el("td", null, f.name));
      tr.appendChild(el("td", "mono", f.value));
      fbody.appendChild(tr);
    }
    ft.appendChild(fbody);
    propBox.appendChild(ft);
  }
  if ((proposal.flags || []).length) {
    const flagsRow = el("div", "flags");
    for (const f of proposal.flags) {
      flagsRow.appendChild(el("span", "flag-chip", "⚑ " + f));
    }
    propBox.appendChild(flagsRow);
  }
  if (analysis.confidence !== undefined) {
    const conf = Number(analysis.confidence);
    const block = el("div", "confidence-block");
    block.appendChild(el("span", "conf-value", conf.toFixed(2)));
    const bar = el("div", "conf-bar");
    const fill = el("div", "conf-fill");
    fill.style.width = Math.round(conf * 100) + "%";
    bar.appendChild(fill);
    block.appendChild(bar);
    propBox.appendChild(block);
    propBox.appendChild(el("div", "conf-copy",
      "MODEL CONFIDENCE is the model's own self-reported estimate. "
      + "Confidence is not permission to trade — the deterministic risk "
      + "governor below decides."));
  }
  root.appendChild(propWrap);

  // -- Separator: confidence ≠ approval -----------------------------------
  const sep = el("div", "approval-separator");
  sep.appendChild(el("span", null, "RISK GOVERNOR APPROVAL"));
  sep.appendChild(el("span", "arrow",
    "— deterministic, independent of the model, evaluated in fixed order "
    + "P-05 → P-01 → P-02 → P-03 → P-04"));
  root.appendChild(sep);

  // -- Risk governor table: actual value AND threshold ---------------------
  const [govWrap, govBox] = sectionBox("RISK GOVERNOR — FIVE FIXED RULES");
  const gt = el("table", "data");
  const gthead = el("thead");
  const gtr = el("tr");
  for (const h of ["ID", "RULE", "ACTUAL", "THRESHOLD", "VERDICT"]) {
    gtr.appendChild(el("th", null, h));
  }
  gthead.appendChild(gtr);
  gt.appendChild(gthead);
  const gbody = el("tbody");
  for (const v of analysis.verdicts || []) {
    const tr = el("tr");
    if (v.verdict === "BLOCK") tr.className = "vl-block";
    else if (v.verdict === "ESCALATE" || v.verdict === "HALT") tr.className = "vl-warn";
    tr.appendChild(el("td", "mono", v.id));
    tr.appendChild(el("td", null, v.name));
    tr.appendChild(el("td", "mono", v.actual));
    tr.appendChild(el("td", "mono muted", v.threshold));
    tr.appendChild(el("td", null)).appendChild(
      el("span", "verdict-chip v-" + v.verdict.toLowerCase(), v.verdict));
    gbody.appendChild(tr);
  }
  gt.appendChild(gbody);
  govBox.appendChild(gt);
  root.appendChild(govWrap);

  // -- Outcome banner ------------------------------------------------------
  const banner = el("div", "outcome-banner ob-" + (outcome || "").toLowerCase());
  banner.appendChild(el("span", "ob-action", outcome));
  const reasonBits = [decision.reason || ""];
  if ((decision.winning_rules || []).length) {
    reasonBits.push("winning rule(s): " + decision.winning_rules.join(", "));
  }
  banner.appendChild(el("span", "ob-reason", reasonBits.join(" · ")));
  root.appendChild(banner);

  // -- Execution evidence (when applicable) --------------------------------
  if (execResult) {
    const [exWrap, exBox] = sectionBox("EXECUTION EVIDENCE — SIMULATED FILL");
    exBox.appendChild(kvTable([
      ["Status", execResult.status, true],
      ["Side", execResult.side + " " + execResult.symbol, true],
      ["Fill price", Number(execResult.fill_price).toFixed(2), true],
      ["Filled qty", execResult.filled_qty, true],
      ["Filled notional", money(execResult.filled_notional), true],
      ["Fee (USDT)", Number(execResult.fee_usdt).toFixed(2), true],
      ["Slippage", Number(execResult.slippage_bps).toFixed(2) + " bps", true],
    ]));
    if (execResult.portfolio_after) {
      const pa = execResult.portfolio_after;
      const lab = el("div", "section-label", "PORTFOLIO AFTER — CHAINED IN THIS EVENT");
      lab.style.marginTop = "10px";
      exBox.appendChild(lab);
      exBox.appendChild(kvTable([
        ["Cash (USDT)", Number(pa.cash_usdt).toFixed(2), true],
        ["BTC", pa.BTC, true],
        ["ETH", pa.ETH, true],
        ["Day realized PnL (USDT)",
         Number(pa.day_realized_pnl_usdt).toFixed(2), true],
      ]));
    }
    root.appendChild(exWrap);
  }

  // -- Why this decision? ---------------------------------------------------
  const whyBtn = el("button", "btn why-btn", "WHY THIS DECISION?");
  whyBtn.addEventListener("click", () => {
    const existing = root.querySelector(".why-panel");
    if (existing) { existing.remove(); return; }
    root.appendChild(buildWhyPanel(detail, { snap, proposal, analysis,
                                             decision, execResult, byType }));
  });
  root.appendChild(whyBtn);
}

function buildWhyPanel(detail, ctx) {
  const { snap, proposal, analysis, decision, execResult, byType } = ctx;
  const panel = el("div", "why-panel");
  const outcome = decision.action || "PENDING";
  const sym = decision.symbol || proposal.symbol || "—";
  const book = (snap.symbols || {})[sym] || {};

  const parts = [];
  parts.push(
    "The agent saw " + sym + " quoted " + Number(book.bid).toFixed(2)
    + " / " + Number(book.ask).toFixed(2)
    + " (spread " + Number(book.spread_bps).toFixed(2)
    + " bps, source: " + (snap.source || "simulator")
    + ", scenario: " + (snap.scenario || "—") + ").");
  parts.push(
    "The model proposed " + (decision.proposal_action || proposal.action)
    + " " + sym + " for " + money(proposal.quote_notional_usdt)
    + " at confidence " + Number(analysis.confidence).toFixed(2)
    + ": “" + proposal.thesis_plain_english + "”"
    + ((proposal.flags || []).length
       ? " It flagged: " + proposal.flags.join(", ") + "." : ""));

  const v = analysis.verdicts || [];
  const passed = v.filter((x) => x.verdict === "PASS").map((x) => x.id);
  const failed = v.filter((x) => x.verdict !== "PASS")
    .map((x) => x.id + " (" + x.verdict + ": " + x.actual + " vs "
         + x.threshold + ")");
  if (failed.length) {
    parts.push("The deterministic governor evaluated all five rules in fixed "
      + "order: " + passed.length + " passed; triggered: " + failed.join("; ")
      + ". Outcome: " + outcome + " — " + (decision.reason || "") + ".");
  } else {
    parts.push("All five governor rules passed (P-05, P-01, P-02, P-03, P-04). "
      + "Outcome: " + outcome + ".");
  }

  if (execResult) {
    parts.push("A simulated fill executed at " + Number(execResult.fill_price).toFixed(2)
      + " for " + money(execResult.filled_notional)
      + " (fee " + money(execResult.fee_usdt) + ", slippage "
      + Number(execResult.slippage_bps).toFixed(2) + " bps); the resulting "
      + "portfolio state is chained inside the EXECUTION_RESULT event.");
  } else {
    parts.push("No order was simulated for this decision.");
  }

  const decEvent = byType.DECISION;
  if (decEvent) {
    parts.push("This trace is hash-chained: the DECISION record is seq "
      + decEvent.seq + " with hash " + frag(decEvent.record_hash)
      + "; altering any field of any event invalidates every later hash. "
      + "Verify it with VERIFY CHAIN.");
  }

  for (const p of parts) panel.appendChild(el("p", null, p));
  return panel;
}

async function selectDecision(decisionId) {
  state.selected = decisionId;
  markSelectedCard();
  const res = await fetch("/api/decisions/" + encodeURIComponent(decisionId));
  if (!res.ok) return;
  renderDetail(await res.json());
}

// ------------------------------------------------------------- SSE wiring
function upsertDecision(ev) {
  const id = ev.decision_id;
  let dec = state.decisions.get(id);
  if (!dec) {
    dec = { decision_id: id, ts: ev.ts, symbol: null, outcome: null,
            event_types: [] };
    state.decisions.set(id, state.decisions.get(id) || dec);
    state.order.unshift(id);
    if (state.order.length > 80) {
      const dropped = state.order.pop();
      state.decisions.delete(dropped);
    }
  }
  dec.ts = ev.ts;
  if (!dec.event_types.includes(ev.event_type)) {
    dec.event_types.push(ev.event_type);
  }
  if (ev.event_type === "DECISION") {
    dec.outcome = ev.payload.action;
    dec.symbol = ev.payload.symbol;
  }
  return dec;
}

function onAuditEvent(ev) {
  state.eventCount = Math.max(state.eventCount, ev.seq);
  document.getElementById("foot-events").textContent = state.eventCount;
  const dec = upsertDecision(ev);
  rebuildTrace();

  if (ev.event_type === "DECISION") {
    bumpCountersOnDecision(ev.payload.action);
    if (state.follow) {
      selectDecision(dec.decision_id).then(markSelectedCard);
    }
  }
  // Any new event may extend the chain beyond the last verification;
  // re-verify quietly so the banner stays honest (PRD §9).
  if (state.verification) runVerification();
}

let counters = { cycles: 0, execute: 0, block: 0, escalate: 0, halt: 0, hold: 0 };
function bumpCountersOnDecision(outcome) {
  const k = (outcome || "").toLowerCase();
  if (k in counters) counters[k] += 1;
}
function bumpCycleCounter() { counters.cycles += 1; }

function connectSSE() {
  const src = new EventSource("/api/stream");
  src.onmessage = (msg) => {
    let data;
    try { data = JSON.parse(msg.data); } catch { return; }
    if (data.type === "audit_event") {
      if (data.event.event_type === "CYCLE_STARTED") bumpCycleCounter();
      onAuditEvent(data.event);
      renderCounters(counters);
    } else if (data.type === "agent_state") {
      renderAgentState(data.state);
    }
  };
  src.onerror = () => { /* EventSource auto-reconnects */ };
}

// ------------------------------------------------------------- boot strap
async function boot() {
  const health = await (await fetch("/healthz")).json();
  renderAgentState(health.agent_state);
  state.eventCount = health.events;
  document.getElementById("foot-events").textContent = health.events;
  document.getElementById("foot-run").textContent =
    health.run_id + " · " + health.market_source + " + " + health.llm_source;

  const dres = await (await fetch("/api/decisions?limit=50")).json();
  counters = dres.summary;
  renderCounters(counters);

  for (const s of dres.decisions) {
    state.decisions.set(s.decision_id, {
      decision_id: s.decision_id, ts: s.ts, symbol: s.symbol,
      outcome: s.outcome,
      event_types: s.executed
        ? STEP_DEFS.map((x) => x[0])
        : STEP_DEFS.slice(0, 4).map((x) => x[0]),
    });
    state.order.push(s.decision_id);
  }
  rebuildTrace();
  if (state.order.length && state.follow) {
    await selectDecision(state.order[0]);
  }

  connectSSE();
  runVerification();  // visibly fast verification on load (PRD §2, §9)
}

// --------------------------------------------------------------- controls
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.hidden = false;
  setTimeout(() => { t.hidden = true; }, 3200);
}

document.getElementById("btn-tick").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const r = await postJSON("/api/agent/tick");
    if (r.status === 409) {
      toast(r.body.detail || "tick rejected (409): cycle already in progress");
    } else if (!r.body || r.body.status !== "ok") {
      toast("tick failed");
    }
  } finally {
    btn.disabled = document.getElementById("agent-state").textContent !== "RUNNING";
  }
});

document.getElementById("btn-verify").addEventListener("click", runVerification);

document.getElementById("btn-resume").addEventListener("click", async () => {
  const r = await postJSON("/api/agent/control", { action: "resume" });
  if (r.body.agent_state) renderAgentState(r.body.agent_state);
});

document.getElementById("btn-clear-halt").addEventListener("click", async () => {
  const r = await postJSON("/api/agent/control", { action: "halt_clear" });
  if (r.body.agent_state) renderAgentState(r.body.agent_state);
  if (r.status === 409) toast("not halted");
});

document.getElementById("follow-chip").addEventListener("click", (e) => {
  state.follow = true;
  e.currentTarget.hidden = true;
  if (state.order.length) selectDecision(state.order[0]);
});

boot().catch((err) => {
  document.getElementById("detail-empty").textContent =
    "Failed to reach the agent API: " + err;
});
