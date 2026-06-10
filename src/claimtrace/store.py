"""SQLite storage layer. One file, no external services."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    mode TEXT NOT NULL DEFAULT 'offline',
    brief TEXT,
    verification TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, source_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (run_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS embeddings (
    run_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    vector TEXT NOT NULL,
    PRIMARY KEY (run_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    value_text TEXT NOT NULL,
    normalized TEXT NOT NULL,
    source_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    retrieval_score REAL NOT NULL,
    audit_hash TEXT
);
CREATE TABLE IF NOT EXISTS claim_checks (
    run_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    status TEXT NOT NULL,
    agreed_value TEXT,
    confidence REAL NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_type)
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER NOT NULL,
    attrs TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tamper_backup (
    seq INTEGER PRIMARY KEY,
    payload TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    idx: int
    span_start: int
    span_end: int
    text: str


def create_run(conn: sqlite3.Connection, run_id: str, mode: str) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, created_at, status, mode) VALUES (?, ?, 'running', ?)",
        (run_id, now_iso(), mode),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, brief: str, verification: dict) -> None:
    conn.execute(
        "UPDATE runs SET status = 'done', brief = ?, verification = ? WHERE run_id = ?",
        (brief, json.dumps(verification), run_id),
    )
    conn.commit()


def add_source(conn: sqlite3.Connection, run_id: str, source_id: str, name: str, kind: str, char_count: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sources (source_id, run_id, name, kind, char_count) VALUES (?, ?, ?, ?, ?)",
        (source_id, run_id, name, kind, char_count),
    )


def add_chunk(conn: sqlite3.Connection, run_id: str, chunk: Chunk, vector: list[float]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO chunks (chunk_id, run_id, source_id, idx, span_start, span_end, text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chunk.chunk_id, run_id, chunk.source_id, chunk.idx, chunk.span_start, chunk.span_end, chunk.text),
    )
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (run_id, chunk_id, vector) VALUES (?, ?, ?)",
        (run_id, chunk.chunk_id, json.dumps(vector)),
    )


def get_chunks(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chunks WHERE run_id = ? ORDER BY source_id, idx", (run_id,)
    ).fetchall()


def get_chunk(conn: sqlite3.Connection, run_id: str, chunk_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM chunks WHERE run_id = ? AND chunk_id = ?", (run_id, chunk_id)
    ).fetchone()


def get_embedding(conn: sqlite3.Connection, run_id: str, chunk_id: str) -> list[float]:
    row = conn.execute(
        "SELECT vector FROM embeddings WHERE run_id = ? AND chunk_id = ?", (run_id, chunk_id)
    ).fetchone()
    return json.loads(row["vector"]) if row else []


def add_claim(conn: sqlite3.Connection, run_id: str, claim: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO claims (claim_id, run_id, claim_type, value_text, normalized,"
        " source_id, chunk_id, span_start, span_end, retrieval_score, audit_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            claim["claim_id"], run_id, claim["claim_type"], claim["value_text"],
            json.dumps(claim["normalized"]), claim["source_id"], claim["chunk_id"],
            claim["span_start"], claim["span_end"], claim["retrieval_score"],
            claim.get("audit_hash"),
        ),
    )


def get_claims(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM claims WHERE run_id = ?", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["normalized"] = json.loads(d["normalized"])
        out.append(d)
    return out


def add_check(conn: sqlite3.Connection, run_id: str, check: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO claim_checks (run_id, claim_type, status, agreed_value, confidence, detail)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            run_id, check["claim_type"], check["status"], check.get("agreed_value"),
            check["confidence"], json.dumps(check["detail"]),
        ),
    )


def get_checks(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM claim_checks WHERE run_id = ?", (run_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"])
        out.append(d)
    return out


def add_span(conn: sqlite3.Connection, run_id: str, span_id: str, trace_id: str,
             name: str, start_ns: int, end_ns: int, attrs: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO spans (span_id, run_id, trace_id, name, start_ns, end_ns, attrs)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (span_id, run_id, trace_id, name, start_ns, end_ns, json.dumps(attrs)),
    )
    conn.commit()


def get_spans(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM spans WHERE run_id = ? ORDER BY start_ns", (run_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["attrs"] = json.loads(d["attrs"])
        out.append(d)
    return out
