"""Extractor agent: pull schema claims out of chunks, with hard grounding.

Two backends share one grounding contract: a claim is only kept if its value
text literally appears inside the chunk it points to. A claim with no valid
source pointer is dropped, never kept. Every accepted claim is appended to
the HMAC audit chain with its chunk id, char span, and retrieval score.

Offline backend: per-sentence keyword plus value-pattern matching, so every
extracted value is by construction a substring of a real chunk.
LLM backend: Claude proposes claims as JSON, then each one passes the same
literal-containment check before it is accepted.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

from .. import config, llm, schema
from ..audit import append_entry
from ..embeddings import cosine, get_embedder
from ..store import Chunk, add_claim, get_embedding


def _retrieval_scores(conn: sqlite3.Connection, run_id: str, chunks: list[Chunk]) -> dict:
    """Cosine similarity of each claim-type query against each chunk."""
    embedder = get_embedder()
    queries = list(schema.QUERIES.items())
    qvecs = embedder.embed([q for _, q in queries])
    scores: dict[tuple[str, str], float] = {}
    for chunk in chunks:
        cvec = get_embedding(conn, run_id, chunk.chunk_id)
        for (ctype, _), qvec in zip(queries, qvecs):
            scores[(ctype, chunk.chunk_id)] = round(cosine(qvec, cvec), 4)
    return scores


def _pick_match(matches: list[re.Match], keyword_pos: int) -> re.Match:
    """When a sentence holds several values, take the one nearest the keyword
    (handles sentences like 'revenue of $14.2 billion and a net loss of
    $1.3 billion' without cross-attributing the numbers)."""
    return min(matches, key=lambda m: abs(m.start() - keyword_pos))


def extract_offline(chunks: list[Chunk]) -> list[dict]:
    claims = []
    for chunk in chunks:
        for sent_offset, sentence in schema.split_sentences(chunk.text):
            for ctype in schema.CLAIM_TYPES:
                kw = schema.KEYWORDS[ctype].search(sentence)
                if not kw:
                    continue
                matches = list(schema.VALUE_PATTERNS[ctype].finditer(sentence))
                if not matches:
                    continue
                match = _pick_match(matches, kw.start())
                value_text, normalized = schema.normalize(ctype, match)
                start_in_chunk = sent_offset + match.start()
                claims.append({
                    "claim_type": ctype,
                    "value_text": value_text,
                    "normalized": normalized,
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "span_start": chunk.span_start + start_in_chunk,
                    "span_end": chunk.span_start + start_in_chunk + len(value_text),
                })
    return claims


EXTRACT_SYSTEM = """You extract hard financial claims from document chunks for a due-diligence audit.
Return ONLY a JSON array. Each element:
{"claim_type": one of %s,
 "value_text": the exact verbatim substring of the chunk that states the value,
 "chunk_id": the id of the chunk it came from}
Rules:
- value_text MUST be copied character for character from the chunk text.
- Only emit a claim when the chunk explicitly states the value.
- Never infer, convert, or invent values.""" % json.dumps(schema.CLAIM_TYPES)


def extract_llm(chunks: list[Chunk]) -> tuple[list[dict], dict]:
    corpus = "\n\n".join(
        f"[chunk_id={c.chunk_id}]\n{c.text}" for c in chunks
    )
    text, usage = llm.complete(EXTRACT_SYSTEM, corpus, max_tokens=8192)
    raw = _parse_json_array(text)
    by_id = {c.chunk_id: c for c in chunks}
    claims = []
    for item in raw:
        ctype = item.get("claim_type")
        chunk = by_id.get(item.get("chunk_id", ""))
        value_text = item.get("value_text", "")
        if ctype not in schema.CLAIM_TYPES or chunk is None or not value_text:
            continue
        # Hard grounding: the quoted value must literally exist in the chunk.
        pos = chunk.text.find(value_text)
        if pos < 0:
            continue
        match = schema.VALUE_PATTERNS[ctype].search(value_text)
        if not match:
            continue
        _, normalized = schema.normalize(ctype, match)
        claims.append({
            "claim_type": ctype,
            "value_text": value_text,
            "normalized": normalized,
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "span_start": chunk.span_start + pos,
            "span_end": chunk.span_start + pos + len(value_text),
        })
    return claims, usage


def _parse_json_array(text: str) -> list:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def run_extractor(conn: sqlite3.Connection, run_id: str, chunks: list[Chunk], span) -> list[dict]:
    if config.offline_mode():
        raw_claims = extract_offline(chunks)
        span.set("backend", "offline-grounded")
    else:
        raw_claims, usage = extract_llm(chunks)
        span.add_usage(usage)
        span.set("backend", "anthropic:" + config.model_name())

    scores = _retrieval_scores(conn, run_id, chunks)
    seen: set[tuple] = set()
    accepted = []
    for claim in raw_claims:
        key = (claim["claim_type"], claim["chunk_id"], claim["value_text"], claim["span_start"])
        if key in seen:
            continue
        seen.add(key)
        claim["claim_id"] = uuid.uuid4().hex[:12]
        claim["retrieval_score"] = scores.get((claim["claim_type"], claim["chunk_id"]), 0.0)
        claim["audit_hash"] = append_entry(conn, run_id, "claim_extracted", {
            "claim_id": claim["claim_id"],
            "claim_type": claim["claim_type"],
            "claim_text": claim["value_text"],
            "normalized": claim["normalized"],
            "source_id": claim["source_id"],
            "chunk_id": claim["chunk_id"],
            "span": [claim["span_start"], claim["span_end"]],
            "retrieval_score": claim["retrieval_score"],
        })
        add_claim(conn, run_id, claim)
        accepted.append(claim)
    conn.commit()
    span.set("claims_extracted", len(accepted))
    return accepted
