"""Tamper-evident audit log.

Each entry is chained to the previous one with an HMAC-SHA256 over
(previous entry hash + canonical JSON of this entry's payload). Flipping a
single byte anywhere in a stored payload breaks every hash from that point
forward, and verify_chain() reports the first broken link.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from . import config
from .store import now_iso

GENESIS = "0" * 64


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, payload_json: str, key: bytes | None = None) -> str:
    key = key or config.audit_key()
    return hmac.new(key, (prev_hash + payload_json).encode("utf-8"), hashlib.sha256).hexdigest()


def last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    return row["entry_hash"] if row else GENESIS


def append_entry(conn: sqlite3.Connection, run_id: str, entry_type: str, payload: dict) -> str:
    payload = dict(payload)
    payload["entry_type"] = entry_type
    payload_json = canonical(payload)
    prev = last_hash(conn)
    entry_hash = compute_hash(prev, payload_json)
    conn.execute(
        "INSERT INTO audit_log (run_id, entry_type, payload, prev_hash, entry_hash, ts)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, entry_type, payload_json, prev, entry_hash, now_iso()),
    )
    conn.commit()
    return entry_hash


def verify_chain(conn: sqlite3.Connection) -> dict:
    """Recompute the full chain and report integrity.

    Returns {"ok": bool, "entries": int, "first_invalid_seq": int | None}.
    """
    rows = conn.execute(
        "SELECT seq, payload, prev_hash, entry_hash FROM audit_log ORDER BY seq"
    ).fetchall()
    prev = GENESIS
    for row in rows:
        if row["prev_hash"] != prev:
            return {"ok": False, "entries": len(rows), "first_invalid_seq": row["seq"]}
        expected = compute_hash(prev, row["payload"])
        if expected != row["entry_hash"]:
            return {"ok": False, "entries": len(rows), "first_invalid_seq": row["seq"]}
        prev = row["entry_hash"]
    return {"ok": True, "entries": len(rows), "first_invalid_seq": None}


def get_entries(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    if run_id:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def tamper_demo(conn: sqlite3.Connection) -> dict:
    """Flip one byte in a stored payload to demonstrate detection. Demo only."""
    row = conn.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq LIMIT 1 OFFSET"
        " (SELECT COUNT(*) / 2 FROM audit_log)"
    ).fetchone()
    if not row:
        return {"tampered": False, "reason": "audit log is empty"}
    original = row["payload"]
    conn.execute(
        "INSERT OR REPLACE INTO tamper_backup (seq, payload) VALUES (?, ?)",
        (row["seq"], original),
    )
    flipped = chr(ord(original[10]) ^ 1)
    mutated = original[:10] + flipped + original[11:]
    conn.execute("UPDATE audit_log SET payload = ? WHERE seq = ?", (mutated, row["seq"]))
    conn.commit()
    return {"tampered": True, "seq": row["seq"]}


def tamper_restore(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT seq, payload FROM tamper_backup").fetchall()
    for r in rows:
        conn.execute("UPDATE audit_log SET payload = ? WHERE seq = ?", (r["payload"], r["seq"]))
    conn.execute("DELETE FROM tamper_backup")
    conn.commit()
    return {"restored": len(rows)}
