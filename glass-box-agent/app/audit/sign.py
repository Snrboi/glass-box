"""HMAC-SHA256 chain-head authenticator.

The hash chain detects naïve one-field edits. It does *not* detect a full
rewrite that recomputes every record_hash. The head MAC does: without
``data/chain.key``, an attacker who rewrites the SQLite file cannot
produce a matching ``head_mac``.

This is a MAC, not a public signature. Anyone who has both the database
*and* ``chain.key`` can re-MAC a rewritten chain. Keep the key off the
DB host if that threat matters; the demo key lives next to the DB so
the core flow needs no extra ceremony.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path


def load_or_create_key(path: str | Path) -> bytes:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        return bytes.fromhex(raw)
    key = os.urandom(32)
    path.write_text(key.hex() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def head_mac(key: bytes, seq: int, record_hash: str) -> str:
    msg = f"{int(seq)}:{record_hash}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()
