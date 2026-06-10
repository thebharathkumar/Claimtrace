"""End-to-end pipeline test on the seed dataset, offline mode.

This is the acceptance test: contradictions surface, every claim is grounded
and audit-logged, the verifier passes the brief, the chain is intact, and one
span per node is recorded.
"""

import re
from pathlib import Path

import pytest

from claimtrace import audit
from claimtrace.pipeline.graph import run_pipeline, seed_specs
from claimtrace.pipeline.verify import verify_brief
from claimtrace.store import connect, get_claims, get_spans

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


@pytest.fixture(scope="module")
def run(tmp_path_factory, request):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CLAIMTRACE_OFFLINE", "1")
    monkeypatch.setenv("AGENT_TRIAGE_ENDPOINT", "http://127.0.0.1:1/otlp/v1/traces")
    request.addfinalizer(monkeypatch.undo)
    db = tmp_path_factory.mktemp("db") / "pipeline.db"
    conn = connect(str(db))
    result = run_pipeline(conn, seed_specs(SEED))
    yield conn, result
    conn.close()


def _check(result, claim_type):
    return next(c for c in result["checks"] if c["claim_type"] == claim_type)


def test_contradictions_detected(run):
    _, result = run
    valuation = _check(result, "valuation")
    assert valuation["status"] == "contradiction"
    values = {v["value"] for v in valuation["detail"]["values"]}
    assert values == {"$1.75 trillion", "$1.77 trillion"}

    raise_size = _check(result, "raise_size")
    assert raise_size["status"] == "contradiction"
    values = {v["value"] for v in raise_size["detail"]["values"]}
    assert values == {"$75 billion", "$80 billion"}

    shares = _check(result, "shares_offered")
    assert shares["status"] == "contradiction"


def test_agreements_detected(run):
    _, result = run
    assert _check(result, "ticker")["status"] == "agreement"
    assert _check(result, "ticker")["agreed_value"] == "SPCX"
    assert _check(result, "exchange")["agreed_value"] == "Nasdaq"
    assert _check(result, "listing_date")["agreed_value"] == "2026-06-12"
    assert _check(result, "offer_price")["status"] == "agreement"
    assert _check(result, "annual_revenue")["status"] == "agreement"


def test_every_claim_is_grounded(run):
    conn, result = run
    claims = get_claims(conn, result["run_id"])
    assert claims
    for claim in claims:
        chunk = conn.execute(
            "SELECT * FROM chunks WHERE run_id = ? AND chunk_id = ?",
            (result["run_id"], claim["chunk_id"]),
        ).fetchone()
        assert chunk is not None, f"claim {claim['claim_id']} points to a missing chunk"
        assert claim["value_text"] in chunk["text"], "claim text must appear in its chunk"
        assert claim["audit_hash"], "every accepted claim is audit-logged"
        assert claim["retrieval_score"] > 0


def test_every_factual_sentence_has_refs_and_verifies(run):
    conn, result = run
    brief = result["brief"]
    assert brief.startswith("Not investment advice")
    for line in brief.splitlines()[2:]:
        if line.strip():
            assert re.search(r"\[ref:[^\]]+\]", line), f"unreferenced line: {line}"
    verification = result["verification"]
    assert verification["factual_sentences"] > 0
    assert verification["flagged"] == []
    assert verification["ok"] is True


def test_verifier_flags_a_drifted_sentence(run):
    conn, result = run
    drifted = result["brief"] + "\nValuation is definitely $9.99 trillion [ref:prospectus:0]."
    report = verify_brief(conn, result["run_id"], drifted)
    assert report["flagged"], "an unbacked value must be flagged"
    assert any("$9.99" in f["reason"] or "$9.99" in f["sentence"] for f in report["flagged"])

    naked = result["brief"] + "\nThe raise is $123 billion with no reference at all."
    report = verify_brief(conn, result["run_id"], naked)
    assert any("no source reference" in f["reason"] for f in report["flagged"])


def test_audit_chain_intact_and_tamper_detected(run):
    conn, result = run
    assert audit.verify_chain(conn)["ok"] is True
    out = audit.tamper_demo(conn)
    assert out["tampered"]
    assert audit.verify_chain(conn)["ok"] is False
    audit.tamper_restore(conn)
    assert audit.verify_chain(conn)["ok"] is True


def test_one_span_per_node(run):
    conn, result = run
    spans = get_spans(conn, result["run_id"])
    names = [s["name"] for s in spans]
    assert names == ["ingest", "extractor", "cross_checker", "brief_writer", "verifier"]
    for span in spans:
        assert span["end_ns"] > span["start_ns"]
        assert "tool_ms" in span["attrs"]
