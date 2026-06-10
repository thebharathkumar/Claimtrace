"""Cross-checker agent: compare every claim value across all sources.

Status per claim type:
  agreement     at least two sources, one distinct value
  single_source exactly one source reported a value
  contradiction two or more distinct values across sources

Confidence is derived only from source counts and agreement, never invented:
  agreement:     0.55 + 0.10 per corroborating source, capped at 0.95
  single_source: 0.40
  contradiction: 0.50 * (majority source count / total source count)
"""

from __future__ import annotations

import sqlite3

from .. import schema
from ..audit import append_entry
from ..store import add_check


def _group_values(claims: list[dict]) -> list[dict]:
    """Cluster claims of one type by normalized value equality."""
    groups: list[dict] = []
    for claim in claims:
        placed = False
        for group in groups:
            if schema.values_equal(group["normalized"], claim["normalized"]):
                group["claims"].append(claim)
                placed = True
                break
        if not placed:
            groups.append({"normalized": claim["normalized"], "claims": [claim]})
    return groups


def cross_check(claims: list[dict]) -> list[dict]:
    checks = []
    for ctype in schema.CLAIM_TYPES:
        typed = [c for c in claims if c["claim_type"] == ctype]
        if not typed:
            continue
        groups = _group_values(typed)
        for group in groups:
            group["sources"] = sorted({c["source_id"] for c in group["claims"]})
        n_sources = len({c["source_id"] for c in typed})

        if len(groups) == 1:
            if n_sources >= 2:
                status = "agreement"
                confidence = min(0.55 + 0.10 * n_sources, 0.95)
            else:
                status = "single_source"
                confidence = 0.40
        else:
            status = "contradiction"
            majority = max(len(g["sources"]) for g in groups)
            confidence = round(0.50 * majority / n_sources, 2)

        agreed = None
        if status != "contradiction":
            agreed = schema.format_value(ctype, groups[0]["normalized"])

        detail = {
            "values": [
                {
                    "value": schema.format_value(ctype, g["normalized"]),
                    "normalized": g["normalized"],
                    "sources": g["sources"],
                    "claims": [
                        {
                            "claim_id": c["claim_id"],
                            "value_text": c["value_text"],
                            "source_id": c["source_id"],
                            "chunk_id": c["chunk_id"],
                            "span": [c["span_start"], c["span_end"]],
                            "retrieval_score": c["retrieval_score"],
                            "audit_hash": c.get("audit_hash"),
                        }
                        for c in g["claims"]
                    ],
                }
                for g in sorted(groups, key=lambda g: -len(g["sources"]))
            ],
            "source_count": n_sources,
        }
        checks.append({
            "claim_type": ctype,
            "status": status,
            "agreed_value": agreed,
            "confidence": round(confidence, 2),
            "detail": detail,
        })
    return checks


def run_crosscheck(conn: sqlite3.Connection, run_id: str, claims: list[dict], span) -> list[dict]:
    checks = cross_check(claims)
    contradictions = 0
    for check in checks:
        add_check(conn, run_id, check)
        if check["status"] == "contradiction":
            contradictions += 1
        append_entry(conn, run_id, "claim_checked", {
            "claim_type": check["claim_type"],
            "status": check["status"],
            "agreed_value": check["agreed_value"],
            "confidence": check["confidence"],
            "source_count": check["detail"]["source_count"],
            "values": [
                {"value": v["value"], "sources": v["sources"]}
                for v in check["detail"]["values"]
            ],
        })
    conn.commit()
    span.set("claim_types_checked", len(checks))
    span.set("contradictions", contradictions)
    return checks
