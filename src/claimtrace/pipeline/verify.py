"""Verifier agent: re-read the finished brief and check every factual
sentence against the audit log.

A sentence is factual when it contains a recognizable value token (money,
shares, price per share, percent, or a date). Each such sentence must carry
at least one [ref:chunk_id] and every value token in it must match an
audit-logged, grounded claim attached to one of the referenced chunks.
Anything else gets flagged. This is the self-check that proves the brief
did not drift from its sources.
"""

from __future__ import annotations

import re
import sqlite3

from .. import schema
from ..audit import append_entry, verify_chain
from ..store import get_claims

REF_RE = re.compile(r"\[ref:([^\]\s]+)\]")
ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# token pattern -> (claim types it can verify against, normalizing claim type)
TOKEN_KINDS = [
    (schema.PER_SHARE, ["offer_price"], "offer_price"),
    (schema.SHARES, ["shares_offered"], "shares_offered"),
    (schema.MONEY, ["valuation", "raise_size", "annual_revenue", "net_loss"], "valuation"),
    (schema.PERCENT, ["free_float", "retail_allocation"], "free_float"),
    (schema.DATE, ["listing_date"], "listing_date"),
]


def _sentences(brief: str) -> list[str]:
    out = []
    for line in brief.splitlines():
        for _, sent in schema.split_sentences(line):
            if sent.strip():
                out.append(sent.strip())
    return out


def _tokens(text: str) -> list[tuple[str, list[str], object]]:
    """Extract value tokens: (token_text, candidate claim types, normalized)."""
    found = []
    covered: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(s < end and start < e for s, e in covered)

    for pattern, ctypes, norm_type in TOKEN_KINDS:
        for m in pattern.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            covered.append((m.start(), m.end()))
            _, normalized = schema.normalize(norm_type, m)
            found.append((m.group(0), ctypes, normalized))
    for m in ISO_DATE.finditer(text):
        if not overlaps(m.start(), m.end()):
            found.append((m.group(0), ["listing_date"], m.group(1)))
    return found


def verify_brief(conn: sqlite3.Connection, run_id: str, brief: str) -> dict:
    claims = get_claims(conn, run_id)
    claims_by_chunk: dict[str, list[dict]] = {}
    for c in claims:
        claims_by_chunk.setdefault(c["chunk_id"], []).append(c)

    sentences = _sentences(brief)
    flagged = []
    factual = 0
    verified = 0

    for sentence in sentences:
        refs = REF_RE.findall(sentence)
        stripped = REF_RE.sub("", sentence)
        tokens = _tokens(stripped)
        if not tokens:
            continue
        factual += 1

        if not refs:
            flagged.append({"sentence": sentence, "reason": "factual sentence has no source reference"})
            continue
        bad_refs = [r for r in refs if r not in claims_by_chunk]
        if len(bad_refs) == len(refs):
            flagged.append({"sentence": sentence, "reason": f"no referenced chunk has a logged claim: {bad_refs}"})
            continue

        candidates = []
        for r in refs:
            candidates.extend(claims_by_chunk.get(r, []))

        unbacked = []
        for token_text, ctypes, normalized in tokens:
            backed = any(
                c["claim_type"] in ctypes and schema.values_equal(c["normalized"], normalized)
                for c in candidates
            )
            if not backed:
                unbacked.append(token_text)
        if unbacked:
            flagged.append({
                "sentence": sentence,
                "reason": "values not backed by a logged grounded claim: " + ", ".join(unbacked),
            })
        else:
            verified += 1

    chain = verify_chain(conn)
    result = {
        "total_sentences": len(sentences),
        "factual_sentences": factual,
        "verified_sentences": verified,
        "flagged": flagged,
        "chain_ok": chain["ok"],
        "ok": not flagged and chain["ok"],
    }
    append_entry(conn, run_id, "brief_verified", {
        "factual_sentences": factual,
        "verified_sentences": verified,
        "flagged_count": len(flagged),
        "chain_ok": chain["ok"],
    })
    return result


def run_verifier(conn: sqlite3.Connection, run_id: str, brief: str, span) -> dict:
    result = verify_brief(conn, run_id, brief)
    span.set("factual_sentences", result["factual_sentences"])
    span.set("verified_sentences", result["verified_sentences"])
    span.set("flagged", len(result["flagged"]))
    span.set("succeeded", result["ok"])
    return result
