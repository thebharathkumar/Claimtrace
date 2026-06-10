"""FastAPI backend: run the pipeline, expose claims, audit, spans, and the
single-page viewer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, config
from .ingest import SourceSpec
from .pipeline.graph import run_pipeline, seed_specs
from .store import connect, get_checks, get_chunk, get_claims, get_spans

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="claimtrace", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def db() -> sqlite3.Connection:
    return connect(config.db_path())


class RunRequest(BaseModel):
    use_seed: bool = True
    sources: list[dict] | None = None  # [{source_id, name, kind, text}]


class PastedSource(BaseModel):
    source_id: str
    name: str
    kind: str = "pasted"
    text: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/run")
def api_run(req: RunRequest) -> dict:
    if req.use_seed or not req.sources:
        specs = seed_specs()
    else:
        specs = [
            SourceSpec(
                source_id=s["source_id"], name=s.get("name", s["source_id"]),
                kind=s.get("kind", "pasted"), text=s.get("text", ""),
            )
            for s in req.sources
        ]
    conn = db()
    try:
        result = run_pipeline(conn, specs)
    finally:
        conn.close()
    return result


@app.get("/api/runs")
def api_runs() -> list[dict]:
    conn = db()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, status, mode FROM runs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _latest_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM runs WHERE status = 'done' ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


@app.get("/api/runs/latest")
def api_latest_run() -> dict:
    conn = db()
    try:
        run_id = _latest_run_id(conn)
        if not run_id:
            raise HTTPException(404, "no completed runs yet")
        return _run_payload(conn, run_id)
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str) -> dict:
    conn = db()
    try:
        return _run_payload(conn, run_id)
    finally:
        conn.close()


def _run_payload(conn: sqlite3.Connection, run_id: str) -> dict:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"run {run_id} not found")
    return {
        "run_id": run_id,
        "created_at": row["created_at"],
        "status": row["status"],
        "mode": row["mode"],
        "brief": row["brief"],
        "verification": json.loads(row["verification"]) if row["verification"] else None,
        "checks": get_checks(conn, run_id),
        "claims": get_claims(conn, run_id),
        "spans": get_spans(conn, run_id),
        "triage_dashboard": config.triage_dashboard(),
    }


@app.get("/api/runs/{run_id}/audit")
def api_audit(run_id: str) -> list[dict]:
    conn = db()
    try:
        return audit.get_entries(conn, run_id)
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/chunks/{chunk_id}")
def api_chunk(run_id: str, chunk_id: str) -> dict:
    conn = db()
    try:
        row = get_chunk(conn, run_id, chunk_id)
        if not row:
            raise HTTPException(404, f"chunk {chunk_id} not found in run {run_id}")
        return dict(row)
    finally:
        conn.close()


@app.post("/api/verify-chain")
def api_verify_chain() -> dict:
    conn = db()
    try:
        return audit.verify_chain(conn)
    finally:
        conn.close()


@app.post("/api/demo/tamper")
def api_tamper() -> dict:
    if not config.demo_enabled():
        raise HTTPException(403, "demo endpoints disabled (set CLAIMTRACE_DEMO=1)")
    conn = db()
    try:
        return audit.tamper_demo(conn)
    finally:
        conn.close()


@app.post("/api/demo/restore")
def api_restore() -> dict:
    if not config.demo_enabled():
        raise HTTPException(403, "demo endpoints disabled (set CLAIMTRACE_DEMO=1)")
    conn = db()
    try:
        return audit.tamper_restore(conn)
    finally:
        conn.close()
