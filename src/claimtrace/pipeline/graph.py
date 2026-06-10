"""LangGraph state machine: ingest -> extract -> crosscheck -> brief -> verify.

Each node runs inside a NodeSpan, so agent-triage receives one OTLP span per
node with latency, token usage, and the tool calls the node made.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .. import config
from ..audit import append_entry
from ..ingest import SourceSpec, ingest_sources
from ..store import Chunk, create_run, finish_run
from ..telemetry import SpanRecorder
from .brief import run_brief
from .crosscheck import run_crosscheck
from .extractor import run_extractor
from .verify import run_verifier


class PipelineState(TypedDict, total=False):
    run_id: str
    chunks: list[Chunk]
    claims: list[dict]
    checks: list[dict]
    brief: str
    verification: dict


class PipelineContext:
    def __init__(self, conn: sqlite3.Connection, run_id: str, specs: list[SourceSpec]) -> None:
        self.conn = conn
        self.run_id = run_id
        self.specs = specs
        self.recorder = SpanRecorder(conn, run_id)

    # -- nodes ---------------------------------------------------------------

    def ingest(self, state: PipelineState) -> PipelineState:
        with self.recorder.node_span("ingest", 1) as span:
            chunks = ingest_sources(self.conn, self.run_id, self.specs)
            span.set("sources", len(self.specs))
            span.set("chunks", len(chunks))
            append_entry(self.conn, self.run_id, "ingested", {
                "sources": [s.source_id for s in self.specs],
                "chunks": len(chunks),
            })
        return {"chunks": chunks}

    def extract(self, state: PipelineState) -> PipelineState:
        with self.recorder.node_span("extractor", 2) as span:
            claims = run_extractor(self.conn, self.run_id, state["chunks"], span)
        return {"claims": claims}

    def crosscheck(self, state: PipelineState) -> PipelineState:
        with self.recorder.node_span("cross_checker", 3) as span:
            checks = run_crosscheck(self.conn, self.run_id, state["claims"], span)
        return {"checks": checks}

    def brief(self, state: PipelineState) -> PipelineState:
        with self.recorder.node_span("brief_writer", 4) as span:
            brief = run_brief(self.conn, self.run_id, state["checks"], span)
        return {"brief": brief}

    def verify(self, state: PipelineState) -> PipelineState:
        with self.recorder.node_span("verifier", 5) as span:
            verification = run_verifier(self.conn, self.run_id, state["brief"], span)
        return {"verification": verification}


def build_graph(ctx: PipelineContext):
    graph = StateGraph(PipelineState)
    graph.add_node("ingest", ctx.ingest)
    graph.add_node("extractor", ctx.extract)
    graph.add_node("cross_checker", ctx.crosscheck)
    graph.add_node("brief_writer", ctx.brief)
    graph.add_node("verifier", ctx.verify)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "extractor")
    graph.add_edge("extractor", "cross_checker")
    graph.add_edge("cross_checker", "brief_writer")
    graph.add_edge("brief_writer", "verifier")
    graph.add_edge("verifier", END)
    return graph.compile()


def run_pipeline(conn: sqlite3.Connection, specs: list[SourceSpec], run_id: str | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    mode = "offline" if config.offline_mode() else "llm:" + config.model_name()
    create_run(conn, run_id, mode)
    ctx = PipelineContext(conn, run_id, specs)
    app = build_graph(ctx)
    state: PipelineState = app.invoke({"run_id": run_id})
    finish_run(conn, run_id, state["brief"], state["verification"])
    exported = ctx.recorder.export()
    return {
        "run_id": run_id,
        "mode": mode,
        "chunks": len(state["chunks"]),
        "claims": len(state["claims"]),
        "checks": state["checks"],
        "brief": state["brief"],
        "verification": state["verification"],
        "triage_export": exported,
    }


def seed_specs(seed_path: Path | None = None) -> list[SourceSpec]:
    seed = seed_path or config.seed_dir()
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    return [
        SourceSpec(
            source_id=item["source_id"],
            name=item["name"],
            kind=item["kind"],
            path=str(seed / item["file"]),
        )
        for item in manifest
    ]
