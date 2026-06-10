"""Ingestion: load documents, chunk with stable addresses, embed, store.

Every chunk is addressable: chunk_id is "{source_id}:{index}" and carries the
exact character span in the original document, so any claim can point back to
the exact text it came from.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .embeddings import get_embedder
from .store import Chunk, add_chunk, add_source

MAX_CHUNK_CHARS = 1100


@dataclass
class SourceSpec:
    source_id: str
    name: str
    kind: str  # prospectus | news | pasted
    path: str | None = None
    text: str | None = None


def load_text(spec: SourceSpec) -> str:
    if spec.text is not None:
        return spec.text
    if not spec.path:
        raise ValueError(f"source {spec.source_id} has neither path nor text")
    path = Path(spec.path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8")


def chunk_text(text: str, source_id: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split on paragraph boundaries, packing paragraphs up to max_chars.

    Spans are character offsets into the original text and the invariant
    text[span_start:span_end] == chunk.text always holds.
    """
    paragraphs: list[tuple[int, int]] = []
    pos = 0
    for para in text.split("\n\n"):
        start = text.index(para, pos) if para else pos
        end = start + len(para)
        if para.strip():
            paragraphs.append((start, end))
        pos = end

    chunks: list[Chunk] = []
    group_start: int | None = None
    group_end: int | None = None

    def flush() -> None:
        nonlocal group_start, group_end
        if group_start is None or group_end is None:
            return
        idx = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{source_id}:{idx}",
                source_id=source_id,
                idx=idx,
                span_start=group_start,
                span_end=group_end,
                text=text[group_start:group_end],
            )
        )
        group_start = group_end = None

    for start, end in paragraphs:
        if group_start is None:
            group_start, group_end = start, end
        elif end - group_start <= max_chars:
            group_end = end
        else:
            flush()
            group_start, group_end = start, end
    flush()
    return chunks


def ingest_sources(conn: sqlite3.Connection, run_id: str, specs: list[SourceSpec]) -> list[Chunk]:
    embedder = get_embedder()
    all_chunks: list[Chunk] = []
    for spec in specs:
        text = load_text(spec)
        add_source(conn, run_id, spec.source_id, spec.name, spec.kind, len(text))
        chunks = chunk_text(text, spec.source_id)
        vectors = embedder.embed([c.text for c in chunks])
        for chunk, vec in zip(chunks, vectors):
            add_chunk(conn, run_id, chunk, vec)
        all_chunks.extend(chunks)
    conn.commit()
    return all_chunks
