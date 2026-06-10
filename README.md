# claimtrace

Trace every claim back to its source.

Agents fail quietly. They state a number with full confidence, and nobody can say where the number came from, whether the sources even agree on it, or whether the record of what happened was edited after the fact. claimtrace is a demo of the trust stack that answers all three questions at once:

1. **Trace it.** Every agent node emits one OTLP span to [agent-triage](https://github.com/thebharathkumar/agent-triage), with latency, token usage, and tool outcomes. You can watch the pipeline think.
2. **Ground it.** Every extracted claim carries a pointer to the exact source chunk and character span it came from. A claim with no source pointer is dropped, never kept. If a value is not in the sources, it does not appear.
3. **Prove it.** Every claim, cross-check, and verification is an entry in an HMAC-chained audit log. `verify_chain()` recomputes the chain; flipping one byte anywhere in a stored entry makes verification report tampered at exactly that entry.

The result: for every sentence in the generated brief, you can answer "why did the agent say this" with a chunk, a char span, a retrieval score, and an audit hash.

This builds on two earlier pieces of the same thesis: `agent-triage` (PyPI, trace observability for multi-agent systems, dogfooded here over OTLP) and an HMAC-chained audit log over RAG. claimtrace combines trace observability, source grounding, and tamper evidence in one pipeline.

## The demo subject

The seed dataset covers the SpaceX IPO (Nasdaq, ticker SPCX, June 2026), chosen because public reporting on it openly contradicts itself: the valuation is reported as both $1.75 trillion and $1.77 trillion, the raise as both $75 billion and $80 billion, the share count as both 556.6 million and 555.6 million. That gives the contradiction detector real work on day one. The subject is a skin; every core module is company agnostic and accepts any prospectus plus any set of sources.

This is an observability demo, not investment advice.

## Quick start

```
pip install -e .
claimtrace demo
```

Then open http://127.0.0.1:8400/. One command runs the full pipeline on the seed data and serves the viewer. No API key is required: without `ANTHROPIC_API_KEY` the pipeline runs in deterministic offline mode, where claims are extracted by grounded pattern matching over the real chunks. With a key set, extraction and brief writing run on Claude (model configurable via `CLAIMTRACE_MODEL`, default `claude-opus-4-8`), and the same grounding contract is enforced: any claim whose quoted text does not literally appear in its chunk is dropped.

To also see the dogfooded observability layer, run agent-triage next to it:

```
pip install "agent-triage[server]"
uvicorn triage.server:app --port 8765
```

claimtrace POSTs one OTLP span per pipeline node to `http://127.0.0.1:8765/otlp/v1/traces` (configurable via `AGENT_TRIAGE_ENDPOINT`). The viewer renders its own local timeline either way.

## Architecture

```
                         LangGraph state machine
  +---------+   +-----------+   +---------------+   +--------------+   +----------+
  | ingest  |-->| extractor |-->| cross-checker |-->| brief writer |-->| verifier |
  +---------+   +-----------+   +---------------+   +--------------+   +----------+
       |              |                 |                  |                 |
       |   one OTLP span per node, exported to agent-triage (latency,       |
       |   token usage, tool calls)                                         |
       v              v                 v                  v                 v
  +-----------------------------------------------------------------------------+
  |  SQLite: sources, addressable chunks (+ embeddings), claims, checks, spans   |
  |  HMAC-chained audit log: claim_extracted -> claim_checked -> brief_written   |
  |               -> brief_verified, each entry MACed over the previous hash     |
  +-----------------------------------------------------------------------------+
                                        |
                              FastAPI + single-page viewer
            brief with inline refs / contradiction panel / claims table
            drill-down (chunk, span, score, audit hash) / trace timeline
                          verify-audit-chain button
```

Pipeline nodes:

1. **Ingest.** Loads the prospectus and news sources (text, markdown, or PDF, plus pasted text). Chunks on paragraph boundaries with stable ids (`source:index`) and exact character spans, embeds each chunk (dependency-free hashed trigram embedder by default, sentence-transformers optional via the `embeddings` extra), and stores everything in SQLite.
2. **Extractor agent.** Pulls a fixed schema of claims: valuation, raise size, shares offered, offer price, listing date, ticker, exchange, free float, retail allocation, latest annual revenue, latest net loss. Each claim must point at the chunk it came from, and the value text must literally appear in that chunk. Each accepted claim is appended to the audit chain with its span and retrieval score.
3. **Cross-checker agent.** Gathers every value found per claim type across all sources and marks `agreement`, `single_source`, or `contradiction`. Confidence is derived only from source counts and agreement, never invented.
4. **Brief writer agent.** Writes a sourced brief from claims that passed grounding. Every factual sentence carries inline refs of the form `[ref:chunk_id]`, and the brief opens with a not-investment-advice notice.
5. **Verifier agent.** Re-reads the finished brief and checks each factual sentence against the audit log. Any sentence whose values are not backed by a logged, grounded claim gets flagged. This is the self-check that proves the brief did not drift.

## The audit chain

Each entry records the claim text, claim type, source id, char span, retrieval score, and timestamp, and its hash is `HMAC-SHA256(key, previous_entry_hash + canonical_json(payload))`. The tests include a tamper test that flips a single byte in a stored payload and asserts the chain reports tampered at that entry, plus a test showing that recomputing the hash of a mutated entry still breaks the next link. In the viewer, the "verify audit chain" button recomputes the chain live, and "simulate tamper" flips a byte so you can watch detection happen.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/run` | run the pipeline (seed data or pasted sources) |
| GET | `/api/runs/latest` | latest run: brief, checks, claims, spans, verification |
| GET | `/api/runs/{run_id}/audit` | audit entries for a run |
| GET | `/api/runs/{run_id}/chunks/{chunk_id}` | exact source chunk behind a ref |
| POST | `/api/verify-chain` | recompute and verify the full HMAC chain |
| POST | `/api/demo/tamper`, `/api/demo/restore` | tamper demo (gated by `CLAIMTRACE_DEMO`) |

## Tests

```
pip install -e ".[dev]"
pytest
```

Covers chunk addressability, the embedder, the audit chain (including the byte-flip tamper test), cross-check statuses and confidence, and an end-to-end pipeline run on the seed data asserting the valuation and raise-size contradictions, full grounding of every claim, a clean verifier pass, an intact chain, and one span per node.

## Configuration

See `.env.example`. Everything has a working default; the only secret you might set is `ANTHROPIC_API_KEY` to switch from offline mode to Claude-backed extraction.
