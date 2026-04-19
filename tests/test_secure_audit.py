"""Tamper-evident audit log tests — the foundation of SOC 2 compliance.

Covers:
- Hash chain continuity across sequential appends
- Detection of modification (edit any past entry → invalid)
- Detection of deletion (remove any entry → invalid)
- Detection of reordering (swap two entries → invalid)
- Signature verification when META_AUDIT_KEY is set
- Backward compat with unsigned legacy entries
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tripwire_ai.secure_audit import (
    AuditIntegrityError, GENESIS_HASH, KEY_ENV_VAR,
    append_signed, iter_entries, verify_chain,
)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV_VAR, "test-secret-key-for-unit-tests-only")


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv(KEY_ENV_VAR, raising=False)


# ---------- Basic chain ----------

def test_first_entry_chains_to_genesis(log_path, without_key):
    e = append_signed(log_path, {"event": "TEST", "n": 1})
    assert e["prev_hash"] == GENESIS_HASH
    assert len(e["hash"]) == 64


def test_sequential_entries_chain(log_path, without_key):
    e1 = append_signed(log_path, {"event": "A", "i": 1})
    e2 = append_signed(log_path, {"event": "B", "i": 2})
    e3 = append_signed(log_path, {"event": "C", "i": 3})
    assert e2["prev_hash"] == e1["hash"]
    assert e3["prev_hash"] == e2["hash"]


def test_verify_intact_chain(log_path, without_key):
    for i in range(10):
        append_signed(log_path, {"event": "E", "i": i})
    result = verify_chain(log_path)
    assert result["valid"] is True
    assert result["entries"] == 10


# ---------- Tamper detection ----------

def test_detects_modified_entry(log_path, without_key):
    for i in range(5):
        append_signed(log_path, {"event": "E", "i": i})

    # Mutate line 3's payload
    lines = log_path.read_text().strip().split("\n")
    e = json.loads(lines[2])
    e["i"] = 999  # sneaky change
    lines[2] = json.dumps(e)
    log_path.write_text("\n".join(lines) + "\n")

    r = verify_chain(log_path)
    assert r["valid"] is False
    assert r["first_bad_line"] == 3
    assert "hash does not match" in r["reason"]


def test_detects_deleted_entry(log_path, without_key):
    for i in range(5):
        append_signed(log_path, {"event": "E", "i": i})

    # Delete line 3
    lines = log_path.read_text().strip().split("\n")
    del lines[2]
    log_path.write_text("\n".join(lines) + "\n")

    r = verify_chain(log_path)
    assert r["valid"] is False
    assert "prev_hash mismatch" in r["reason"]


def test_detects_reordered_entries(log_path, without_key):
    for i in range(5):
        append_signed(log_path, {"event": "E", "i": i})

    # Swap lines 2 and 3
    lines = log_path.read_text().strip().split("\n")
    lines[1], lines[2] = lines[2], lines[1]
    log_path.write_text("\n".join(lines) + "\n")

    r = verify_chain(log_path)
    assert r["valid"] is False


def test_detects_injected_entry(log_path, without_key):
    for i in range(3):
        append_signed(log_path, {"event": "E", "i": i})

    # Inject an unsigned malicious entry in the middle
    lines = log_path.read_text().strip().split("\n")
    lines.insert(2, json.dumps({"event": "INJECTED", "hash": "f" * 64,
                                 "prev_hash": "0" * 64}))
    log_path.write_text("\n".join(lines) + "\n")

    r = verify_chain(log_path)
    assert r["valid"] is False


# ---------- Signatures ----------

def test_signatures_written_when_key_present(log_path, with_key):
    e = append_signed(log_path, {"event": "SIGNED"})
    assert e["sig"] is not None
    assert len(e["sig"]) == 64  # hex-encoded HMAC-SHA256


def test_signatures_absent_when_key_missing(log_path, without_key):
    e = append_signed(log_path, {"event": "UNSIGNED"})
    assert e["sig"] is None


def test_verify_detects_tampered_signed_entry(log_path, with_key):
    for i in range(5):
        append_signed(log_path, {"event": "E", "i": i})

    lines = log_path.read_text().strip().split("\n")
    e = json.loads(lines[2])
    e["event"] = "TAMPERED"  # change payload but leave sig alone
    lines[2] = json.dumps(e)
    log_path.write_text("\n".join(lines) + "\n")

    r = verify_chain(log_path)
    assert r["valid"] is False


def test_verify_accepts_correct_signatures(log_path, with_key):
    for i in range(5):
        append_signed(log_path, {"event": "E", "i": i})
    r = verify_chain(log_path)
    assert r["valid"] is True
    assert r["signed"] == 5
    assert r["sig_key_loaded"] is True


# ---------- Legacy compatibility ----------

def test_legacy_unsigned_entries_dont_break_verification(log_path, without_key):
    # Write 2 legacy entries (no hash/sig)
    with log_path.open("a") as f:
        f.write(json.dumps({"event": "LEGACY", "i": 1}) + "\n")
        f.write(json.dumps({"event": "LEGACY", "i": 2}) + "\n")
    # Then 3 new signed-chain entries
    for i in range(3):
        append_signed(log_path, {"event": "NEW", "i": i})

    r = verify_chain(log_path)
    # Legacy entries skipped (no 'hash' field), new ones chain from GENESIS
    assert r["valid"] is True
    assert r["entries"] == 5


# ---------- Iteration ----------

def test_iter_entries_round_trip(log_path, without_key):
    for i in range(3):
        append_signed(log_path, {"event": "E", "i": i})
    events = list(iter_entries(log_path))
    assert len(events) == 3
    assert [e["i"] for e in events] == [0, 1, 2]
