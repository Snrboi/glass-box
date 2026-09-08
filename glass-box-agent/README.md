# Glass Box — Agent Audit Console

An AI trading agent can propose a trade — but a **deterministic governor
decides whether it is allowed**. Every material decision record is
**cryptographically chained** so the resulting decision trace can be
**independently verified**.

Built for the Binance AgentOS Mini Hackathon — Track A. Solo builder +
AI coding agent · dry-run only · simulator-first · $0 infrastructure ·
vanilla JS frontend, no build step.

> Glass Box does not merely show **what** an AI trading agent did. It shows
> the **evidence** for how the decision was produced, what controls approved
> it, and whether that record has been altered.

## The problem

AI trading agents can make autonomous decisions, but a conventional
exchange/order view primarily shows the resulting order. That leaves a
trust gap: **what data did the agent see, what did the model propose, which
risk rules were applied, and why was the final action permitted or
blocked?**

This product closes the gap between *order visibility* and *decision
visibility*. It shows the model's explicit **structured output** (a
tool-use JSON proposal plus an explicit thesis field) and the
**deterministic controls** applied to it — it does **not** claim to expose
hidden model chain-of-thought. The vocabulary here is deliberate:
decision trace, structured rationale, audit record.

## Architecture

```
SIMULATED MARKET            (deterministic fixtures — reproducible judging)
        |
        v
AI ANALYST (Claude / MockLLM, structured JSON)     ← advisory, not sovereign
        |
        v
   DecisionProposal
        |
        v
RISK GOVERNOR (deterministic, fixed order, 5 rules)
        |
+-------+--------+-----------+
v       v        v           v
EXECUTE BLOCK   ESCALATE / HALT
        |
        v
SIMULATED VENUE             (dry-run fills + portfolio effects only)
        |
        v
HASH-CHAINED AUDIT STORE (SQLite INSERT-only + JSONL)
        |  SSE / REST
        v
GLASS BOX CONSOLE (feed -> decision -> verify)
```

- **LLM is advisory, not sovereign.** The model never decides whether a
  proposal is safe to execute.
- **Policy is deterministic.** Same inputs → same verdicts and outcome.
  Five rules, fixed evaluation order, visible in code and UI:
  `P-05 DRAWDOWN_BREAKER → P-01 CONF_MIN → P-02 NOTIONAL_CAP →
  P-03 EXPOSURE_CAP → P-04 SPREAD_SANITY`, with precedence
  `HALT/ESCALATE → first BLOCK in fixed order → HOLD → EXECUTE`.
- **Audit is first-class.** Append-only SQLite events are linked with
  SHA-256 hashes and mirrored to JSONL. The hash protects the **complete
  event envelope** — sequence, timestamp, run/decision IDs, event type,
  payload **and** previous hash — not merely the payload. Retyping an
  event's `event_type` or `run_id` breaks verification exactly like
  editing its payload.
- **Simulation is intentional.** Deterministic market fixtures make the
  judging experience reproducible, and the UI is explicit about
  simulation (`SIMULATED DATA` badge, `simulated` source labels).
- **Demo reliability beats optional complexity.** The real Claude adapter
  (`app/llm/client.py`, enabled with `GLASSBOX_LLM=claude` +
  `ANTHROPIC_API_KEY`) and the optional MCP market source are adapters
  that never gate the core demo.

## 30-second quickstart

```bash
cd glass-box-agent
python -m venv .venv && source .venv/bin/activate   # (1)
pip install -r requirements.txt                      # (2)
python tools/demo_setup.py                           # (3) seed deterministic demo data
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000  # (4)
# open http://localhost:8000                          # (5)
```

The core demo works entirely with the simulator + MockLLM — **no external
API key is required**. Press **RUN TICK** to drive one decision cycle;
each press deterministically walks the demo rotation
EXECUTE → BLOCK (low confidence) → ESCALATE (prompt injection) →
ESCALATE (wide spread) → HOLD.

## Verify the chain (one command)

```bash
python tools/verify_chain.py           # SQLite chain
python tools/verify_chain.py --jsonl   # the JSONL export
python -m pytest -q                    # ~39 tests: chain, tamper, policy, behavior
```

Response shape: `{ "valid": false, "checked": 183, "first_break_seq": 183 }`.

## The money shot: tamper detection

1. In the console: **VERIFY CHAIN** → `✓ VERIFIED — N / N EVENTS`.
2. In a shell (simulating an attacker with raw database file access):

   ```bash
   python tools/tamper_demo.py            # edits one protected event field
   ```
3. Back in the console: **VERIFY CHAIN** →
   `✕ CHAIN BROKEN — FIRST BREAK: SEQ n` with the **Expected vs Actual**
   hash fragments.
4. Re-seed with `python tools/demo_setup.py`.

Tamper coverage (all detected): `payload`, `event_type`, `ts`, `run_id`,
`decision_id`, `seq` (modified or removed), `prev_hash`.

## What the console shows

Every screen element answers one of four questions: What did the agent
see? What did it propose? What controls were applied? What happened —
and can I verify it?

- **Left:** the live decision trace — `CYCLE STARTED → DATA SNAPSHOT →
  ANALYSIS + RISK REVIEW → DECISION → EXECUTION`, each step appearing as
  it happens (SSE).
- **Right (drill-down):** action/symbol/notional/confidence header; the
  market snapshot with source and timestamp; the model's thesis and
  factors; confidence as a prominent number with a simple bar — visually
  separated from **RISK GOVERNOR APPROVAL**, because *confidence is not
  permission to trade*; the five-rule governor table with **actual value
  and threshold** (not just PASS/BLOCK); the outcome banner with the
  winning rule; simulated fill evidence including `portfolio_after`
  chained inside the event; and a one-click **Why this decision?**
  plain-English summary.
- **Footer:** cycle / execute / block / escalate-halt counters.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness |
| GET | `/` | Dashboard |
| GET | `/api/stream` | SSE event feed |
| GET | `/api/decisions?limit=N` | Recent decisions |
| GET | `/api/decisions/{id}` | Full decision cycle |
| GET | `/api/chain/verify` | Verify chain integrity |
| POST | `/api/agent/tick` | Run one dry-run cycle |
| POST | `/api/agent/control` | `pause` / `resume` / `halt_clear` |
| GET | `/api/export/decisions.jsonl` | Export complete chain |

Concurrent manual ticks are rejected with HTTP 409 — two simultaneous
ticks cannot corrupt cycle state or append an inconsistent sequence.

## Security posture

- Market data is **untrusted**: it enters the model inside a
  `<market_data>` fence as data, never as instructions; injection-style
  text is flagged and escalated, and is rendered in the UI with
  `textContent` only (never `innerHTML`). No order is placed from a
  prompt-injection attempt.
- Malformed model output → one retry → second failure escalates
  `LLM_UNRELIABLE`. Adapter failures escalate the same way.
- The `events` table has SQLite triggers rejecting `UPDATE` and `DELETE`;
  the application exposes no event-edit API.
- **Dry-run only.** No live execution path exists anywhere in this
  repository.

## Honest limits

Dry-run, single-user, two-symbol watchlist (BTCUSDT, ETHUSDT), simulated
fills. Not a financial product, not a production portfolio system. Fills,
fees and PnL are deterministic simulations.

## Layout

```
glass-box-agent/
├── app/
│   ├── api/          # routes.py, sse.py
│   ├── agent/        # loop.py, cycle.py
│   ├── policy/       # engine.py, rules.py
│   ├── audit/        # chain.py, verify.py, jsonl.py
│   ├── market/       # base.py, simulator.py, mcp_source.py (optional stub)
│   ├── llm/          # client.py (real Claude adapter), mock.py, prompts.py
│   └── exec/         # venue.py
├── frontend/         # index.html, app.js, style.css (vanilla, no build)
├── fixtures/         # policy-cases.json (5), llm-responses.json (4)
├── tools/            # verify_chain.py, demo_setup.py, tamper_demo.py
├── tests/            # pytest suite
├── policy.yaml       # the five deterministic rules + thresholds
└── data/             # local SQLite + decisions.jsonl (runtime)
```
