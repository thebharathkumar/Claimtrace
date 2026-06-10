"""Audit chain tests, including the byte-flip tamper test."""

import sqlite3

import pytest

from claimtrace import audit
from claimtrace.store import connect


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "test.db"))
    yield c
    c.close()


def _seed_entries(conn: sqlite3.Connection, n: int = 6) -> None:
    for i in range(n):
        audit.append_entry(conn, "run1", "claim_extracted", {
            "claim_id": f"c{i}",
            "claim_type": "valuation",
            "claim_text": f"$1.7{i} trillion",
            "source_id": "prospectus",
            "chunk_id": f"prospectus:{i}",
            "span": [i * 10, i * 10 + 5],
            "retrieval_score": 0.5,
        })


def test_chain_verifies_when_intact(conn):
    _seed_entries(conn)
    result = audit.verify_chain(conn)
    assert result["ok"] is True
    assert result["entries"] == 6
    assert result["first_invalid_seq"] is None


def test_entries_are_chained(conn):
    _seed_entries(conn, 3)
    rows = conn.execute("SELECT prev_hash, entry_hash FROM audit_log ORDER BY seq").fetchall()
    assert rows[0]["prev_hash"] == audit.GENESIS
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert rows[2]["prev_hash"] == rows[1]["entry_hash"]


def test_flipping_one_byte_breaks_the_chain(conn):
    """The acceptance-critical tamper test: mutate one byte of a stored
    payload and the chain must report tampered at exactly that entry."""
    _seed_entries(conn)
    row = conn.execute("SELECT seq, payload FROM audit_log WHERE seq = 3").fetchone()
    payload = row["payload"]
    flipped = payload[:5] + chr(ord(payload[5]) ^ 1) + payload[6:]
    assert flipped != payload
    conn.execute("UPDATE audit_log SET payload = ? WHERE seq = 3", (flipped, ))
    conn.commit()

    result = audit.verify_chain(conn)
    assert result["ok"] is False
    assert result["first_invalid_seq"] == 3


def test_rewriting_hashes_does_not_hide_tampering(conn):
    """Recomputing the hash of a mutated entry without the chain still breaks
    the next link, because each entry MACs over the previous hash."""
    _seed_entries(conn)
    row = conn.execute("SELECT seq, payload, prev_hash FROM audit_log WHERE seq = 3").fetchone()
    mutated = row["payload"].replace("$1.72 trillion", "$9.99 trillion")
    assert mutated != row["payload"]
    recomputed = audit.compute_hash(row["prev_hash"], mutated)
    conn.execute(
        "UPDATE audit_log SET payload = ?, entry_hash = ? WHERE seq = 3",
        (mutated, recomputed),
    )
    conn.commit()

    result = audit.verify_chain(conn)
    assert result["ok"] is False
    assert result["first_invalid_seq"] == 4


def test_deleting_an_entry_breaks_the_chain(conn):
    _seed_entries(conn)
    conn.execute("DELETE FROM audit_log WHERE seq = 2")
    conn.commit()
    result = audit.verify_chain(conn)
    assert result["ok"] is False


def test_tamper_demo_roundtrip(conn):
    _seed_entries(conn)
    assert audit.verify_chain(conn)["ok"] is True
    out = audit.tamper_demo(conn)
    assert out["tampered"] is True
    assert audit.verify_chain(conn)["ok"] is False
    audit.tamper_restore(conn)
    assert audit.verify_chain(conn)["ok"] is True
