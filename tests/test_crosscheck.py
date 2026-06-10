"""Cross-checker tests: statuses and count-derived confidence."""

from claimtrace.pipeline.crosscheck import cross_check


def _claim(ctype, value_text, normalized, source, chunk="c:0", cid="x"):
    return {
        "claim_id": cid,
        "claim_type": ctype,
        "value_text": value_text,
        "normalized": normalized,
        "source_id": source,
        "chunk_id": chunk,
        "span_start": 0,
        "span_end": len(value_text),
        "retrieval_score": 0.5,
        "audit_hash": "h",
    }


def test_agreement_across_sources():
    claims = [
        _claim("ticker", "ticker symbol SPCX", "SPCX", "prospectus", cid="a"),
        _claim("ticker", "ticker symbol SPCX", "SPCX", "news1", cid="b"),
        _claim("ticker", "ticker symbol SPCX", "SPCX", "news2", cid="c"),
    ]
    [check] = cross_check(claims)
    assert check["status"] == "agreement"
    assert check["agreed_value"] == "SPCX"
    assert check["detail"]["source_count"] == 3
    assert check["confidence"] == 0.85  # 0.55 + 0.10 * 3


def test_single_source():
    claims = [_claim("net_loss", "net loss of $1.3 billion", 1.3e9, "prospectus")]
    [check] = cross_check(claims)
    assert check["status"] == "single_source"
    assert check["confidence"] == 0.40


def test_contradiction_lists_each_value_with_sources():
    claims = [
        _claim("valuation", "$1.75 trillion", 1.75e12, "prospectus", cid="a"),
        _claim("valuation", "$1.75 trillion", 1.75e12, "news1", cid="b"),
        _claim("valuation", "$1.77 trillion", 1.77e12, "news2", cid="c"),
    ]
    [check] = cross_check(claims)
    assert check["status"] == "contradiction"
    assert check["agreed_value"] is None
    values = check["detail"]["values"]
    assert len(values) == 2
    assert values[0]["sources"] == ["news1", "prospectus"]  # majority first
    assert values[1]["sources"] == ["news2"]
    # confidence derived from counts: 0.5 * majority(2) / total(3)
    assert check["confidence"] == 0.33


def test_numeric_equality_tolerates_float_noise_but_not_real_differences():
    claims = [
        _claim("raise_size", "$75 billion", 75e9, "a", cid="a"),
        _claim("raise_size", "$75 billion", 75.000000001e9, "b", cid="b"),
    ]
    [check] = cross_check(claims)
    assert check["status"] == "agreement"

    claims = [
        _claim("shares_offered", "556.6 million shares", 556.6e6, "a", cid="a"),
        _claim("shares_offered", "555.6 million shares", 555.6e6, "b", cid="b"),
    ]
    [check] = cross_check(claims)
    assert check["status"] == "contradiction"


def test_confidence_is_capped():
    claims = [
        _claim("exchange", "Nasdaq", "Nasdaq", f"s{i}", cid=str(i)) for i in range(8)
    ]
    [check] = cross_check(claims)
    assert check["confidence"] == 0.95
