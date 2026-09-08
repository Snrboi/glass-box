# Glass Box

An AI trading agent can **propose** a trade. A deterministic governor
**decides** whether it is allowed. Every material decision record is
**hash-chained** (and HMAC’d at the head) so the trace can be independently
verified.

The implementation lives in [`glass-box-agent/`](glass-box-agent/).

```bash
cd glass-box-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tools/demo_setup.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

Dry-run only. Simulator-first. No API key required for the core demo.
See [`glass-box-agent/README.md`](glass-box-agent/README.md) for architecture,
the tamper demo, and honest limits.
