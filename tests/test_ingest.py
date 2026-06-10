"""Ingestion tests: chunk addressability and the embedding store."""

import pytest

from claimtrace.embeddings import HashedTrigramEmbedder, cosine
from claimtrace.ingest import SourceSpec, chunk_text, ingest_sources
from claimtrace.store import connect, create_run, get_chunks

DOC = (
    "First paragraph about the valuation of the company at $1.75 trillion.\n\n"
    "Second paragraph about the offering of 556.6 million shares.\n\n"
    "Third paragraph about revenue of $14.2 billion in the latest year.\n\n"
    + "Long filler paragraph. " * 80
)


def test_chunk_spans_are_addressable():
    chunks = chunk_text(DOC, "doc")
    assert chunks, "expected at least one chunk"
    for chunk in chunks:
        assert DOC[chunk.span_start:chunk.span_end] == chunk.text


def test_chunk_ids_are_stable_and_source_scoped():
    chunks = chunk_text(DOC, "doc")
    assert [c.chunk_id for c in chunks] == [f"doc:{i}" for i in range(len(chunks))]


def test_chunks_respect_max_chars():
    chunks = chunk_text(DOC, "doc", max_chars=200)
    for chunk in chunks:
        # single paragraphs longer than the cap stay whole; merged groups do not exceed it
        if chunk.text.count("\n\n"):
            assert len(chunk.text) <= 200


def test_ingest_stores_chunks_and_embeddings(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    create_run(conn, "r1", "offline")
    specs = [SourceSpec(source_id="doc", name="Doc", kind="pasted", text=DOC)]
    chunks = ingest_sources(conn, "r1", specs)
    rows = get_chunks(conn, "r1")
    assert len(rows) == len(chunks)
    n_vec = conn.execute("SELECT COUNT(*) AS n FROM embeddings WHERE run_id = 'r1'").fetchone()["n"]
    assert n_vec == len(chunks)
    conn.close()


def test_hashed_embedder_is_deterministic_and_normalized():
    e = HashedTrigramEmbedder()
    a, b = e.embed(["valuation of the company", "valuation of the company"])
    assert a == b
    assert cosine(a, b) == pytest.approx(1.0, abs=1e-9)
    c = e.embed(["completely different words about rockets"])[0]
    assert cosine(a, c) < 0.9


def test_pdf_ingestion_optional(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    pdf_path = tmp_path / "doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    from claimtrace.ingest import load_text

    text = load_text(SourceSpec(source_id="p", name="p", kind="prospectus", path=str(pdf_path)))
    assert isinstance(text, str)
