"""Observability: one span per pipeline node, exported to agent-triage.

Spans are recorded locally in SQLite for the viewer timeline and exported as
OTLP/HTTP JSON to agent-triage's receiver (POST /otlp/v1/traces). Attribute
keys match what agent-triage parses: agent.id, run.id, turn, action.tool,
action.succeeded, latency.llm_ms, latency.tool_ms. Token usage rides along as
gen_ai.usage.* per OTLP GenAI conventions.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
import uuid

from . import config
from .store import add_span


class SpanRecorder:
    """Collects node spans for one run and exports them in a single batch."""

    def __init__(self, conn: sqlite3.Connection, run_id: str) -> None:
        self.conn = conn
        self.run_id = run_id
        self.trace_id = uuid.uuid4().hex
        self.spans: list[dict] = []

    def node_span(self, name: str, turn: int) -> "NodeSpan":
        return NodeSpan(self, name, turn)

    def export(self) -> bool:
        """POST all spans to agent-triage as OTLP JSON. Failure is non-fatal:
        the local timeline in SQLite keeps working without the collector."""
        if not self.spans:
            return False
        body = json.dumps(self._otlp_payload()).encode("utf-8")
        req = urllib.request.Request(
            config.triage_endpoint(),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _otlp_payload(self) -> dict:
        def attr(key: str, value) -> dict:
            if isinstance(value, bool):
                return {"key": key, "value": {"boolValue": value}}
            if isinstance(value, int):
                return {"key": key, "value": {"intValue": value}}
            if isinstance(value, float):
                return {"key": key, "value": {"doubleValue": value}}
            return {"key": key, "value": {"stringValue": str(value)}}

        otlp_spans = []
        for s in self.spans:
            attrs = [
                attr("agent.id", s["name"]),
                attr("run.id", self.run_id),
                attr("turn", s["turn"]),
                attr("action.tool", s["attrs"].get("tool", s["name"])),
                attr("action.succeeded", s["attrs"].get("succeeded", True)),
                attr("latency.llm_ms", s["attrs"].get("llm_ms", 0)),
                attr("latency.tool_ms", s["attrs"].get("tool_ms", 0)),
                attr("gen_ai.usage.input_tokens", s["attrs"].get("input_tokens", 0)),
                attr("gen_ai.usage.output_tokens", s["attrs"].get("output_tokens", 0)),
            ]
            for k, v in s["attrs"].items():
                if k not in ("tool", "succeeded", "llm_ms", "tool_ms", "input_tokens", "output_tokens"):
                    attrs.append(attr(f"claimtrace.{k}", v))
            otlp_spans.append({
                "traceId": self.trace_id,
                "spanId": s["span_id"],
                "name": s["name"],
                "kind": 1,
                "startTimeUnixNano": str(s["start_ns"]),
                "endTimeUnixNano": str(s["end_ns"]),
                "attributes": attrs,
            })
        return {
            "resourceSpans": [{
                "resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": "claimtrace"}},
                ]},
                "scopeSpans": [{
                    "scope": {"name": "claimtrace.pipeline"},
                    "spans": otlp_spans,
                }],
            }]
        }


class NodeSpan:
    """Context manager wrapping one pipeline node execution."""

    def __init__(self, recorder: SpanRecorder, name: str, turn: int) -> None:
        self.recorder = recorder
        self.name = name
        self.turn = turn
        self.span_id = uuid.uuid4().hex[:16]
        self.attrs: dict = {"tool": name, "succeeded": True}
        self.start_ns = 0
        self.end_ns = 0

    def set(self, key: str, value) -> None:
        self.attrs[key] = value

    def add_usage(self, usage: dict) -> None:
        self.attrs["input_tokens"] = self.attrs.get("input_tokens", 0) + usage.get("input_tokens", 0)
        self.attrs["output_tokens"] = self.attrs.get("output_tokens", 0) + usage.get("output_tokens", 0)
        self.attrs["llm_ms"] = self.attrs.get("llm_ms", 0) + usage.get("llm_ms", 0)

    def __enter__(self) -> "NodeSpan":
        self.start_ns = time.time_ns()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.end_ns = time.time_ns()
        if exc_type is not None:
            self.attrs["succeeded"] = False
            self.attrs["error"] = str(exc)
        total_ms = (self.end_ns - self.start_ns) // 1_000_000
        self.attrs.setdefault("llm_ms", 0)
        self.attrs["tool_ms"] = max(total_ms - self.attrs["llm_ms"], 0)
        record = {
            "span_id": self.span_id,
            "name": self.name,
            "turn": self.turn,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "attrs": dict(self.attrs),
        }
        self.recorder.spans.append(record)
        add_span(
            self.recorder.conn, self.recorder.run_id, self.span_id,
            self.recorder.trace_id, self.name, self.start_ns, self.end_ns,
            self.attrs,
        )
