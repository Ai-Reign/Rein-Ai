"""Cryptographically signed audit log — tamper-evident hash chain.

Each entry is signed with HMAC-SHA256 over (prev_hash + entry_payload). Any
mutation of any prior entry invalidates the chain from that point forward and
can be detected in a single pass.

Design choices:
- HMAC-SHA256 over Ed25519: symmetric is enough for a single-operator deployment;
  upgrade to Ed25519 when multi-tenant. Fewer key-management gotchas for the
  MVP.
- Hash chain (each entry includes hash of the previous) gives us append-only
  evidence. Detecting deletion is as simple as verifying the chain.
- Secret key comes from META_AUDIT_KEY env var. Missing key → hashing still
  works (chain integrity) but signatures are disabled and marked as such.
- Fallback-compatible: non-signed entries (from older logs) still parse.

SOC 2 / ISO 27001 references:
- CC6.1 (logical access) — audit logs must be protected from modification.
- CC7.2 (system monitoring) — events must be traceable and integrity-verifiable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Iterator, Optional


GENESIS_HASH = "0" * 64
KEY_ENV_VAR = "META_AUDIT_KEY"


class AuditIntegrityError(Exception):
    """Raised when a chain inconsistency is detected during verification."""


def _get_key() -> Optional[bytes]:
    k = os.environ.get(KEY_ENV_VAR)
    if not k:
        return None
    return k.encode("utf-8")


def _compute_hash(prev_hash: str, payload: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\n")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _compute_signature(key: bytes, prev_hash: str, payload: str) -> str:
    mac = hmac.new(key, digestmod=hashlib.sha256)
    mac.update(prev_hash.encode("utf-8"))
    mac.update(b"\n")
    mac.update(payload.encode("utf-8"))
    return mac.hexdigest()


def _last_hash(path: Path) -> str:
    """Read the last line's `hash` field, or GENESIS_HASH if file doesn't exist."""
    if not path.exists() or path.stat().st_size == 0:
        return GENESIS_HASH
    # Tail read for speed on large logs
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block = min(8192, size)
        f.seek(size - block, os.SEEK_SET)
        tail = f.read().decode("utf-8", errors="ignore")
    lines = [ln for ln in tail.strip().split("\n") if ln.strip()]
    if not lines:
        return GENESIS_HASH
    try:
        entry = json.loads(lines[-1])
    except json.JSONDecodeError:
        return GENESIS_HASH
    return entry.get("hash", GENESIS_HASH)


def append_signed(path: Path | str, entry: dict) -> dict:
    """Append `entry` to the audit log with hash chain + optional HMAC signature.

    Returns the full persisted entry (including `prev_hash`, `hash`, `sig`, `at_signed`).
    Thread-safe only in the sense that the underlying OS append is atomic for small
    writes; use a lock if multiple processes write simultaneously.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    prev_hash = _last_hash(path)
    # Serialize the user payload deterministically
    payload_obj = {k: v for k, v in entry.items()
                   if k not in ("prev_hash", "hash", "sig", "at_signed")}
    payload_obj.setdefault("at", time.time())
    payload = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)

    new_hash = _compute_hash(prev_hash, payload)
    key = _get_key()
    sig = _compute_signature(key, prev_hash, payload) if key else None

    out = {
        **payload_obj,
        "prev_hash": prev_hash,
        "hash": new_hash,
        "sig": sig,
        "at_signed": time.time(),
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, separators=(",", ":"), sort_keys=True))
        f.write("\n")
    return out


def verify_chain(path: Path | str) -> dict:
    """Walk the entire audit log and verify integrity.

    Returns a dict with:
        valid:          bool   — overall chain is intact
        entries:        int    — total entries processed
        first_bad_line: int | None — line number of first broken link (1-indexed)
        reason:         str    — explanation of first break, if any
        signed:         int    — count of entries with HMAC signatures
        sig_key_loaded: bool   — whether META_AUDIT_KEY was present
    """
    path = Path(path)
    if not path.exists():
        return {"valid": True, "entries": 0, "first_bad_line": None,
                "reason": "empty", "signed": 0, "sig_key_loaded": _get_key() is not None}

    key = _get_key()
    prev_hash = GENESIS_HASH
    signed_count = 0
    line_no = 0
    invalid_line = None
    invalid_reason = ""

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line_no += 1
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                invalid_line = line_no
                invalid_reason = "malformed JSON"
                break

            # Legacy / unsigned entries: skip verification but don't break chain
            if "hash" not in entry:
                continue

            if entry.get("prev_hash") != prev_hash:
                invalid_line = line_no
                invalid_reason = (f"prev_hash mismatch — expected {prev_hash[:12]}, "
                                  f"got {(entry.get('prev_hash') or '')[:12]}")
                break

            payload_obj = {k: v for k, v in entry.items()
                           if k not in ("prev_hash", "hash", "sig", "at_signed")}
            payload = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)
            expected_hash = _compute_hash(prev_hash, payload)
            if expected_hash != entry["hash"]:
                invalid_line = line_no
                invalid_reason = "hash does not match payload"
                break

            # Signature check (only if key loaded AND entry has a sig)
            if key and entry.get("sig"):
                expected_sig = _compute_signature(key, prev_hash, payload)
                if not hmac.compare_digest(expected_sig, entry["sig"]):
                    invalid_line = line_no
                    invalid_reason = "HMAC signature mismatch"
                    break
                signed_count += 1
            elif entry.get("sig"):
                # Entry has sig but we have no key — can't verify but don't fail
                signed_count += 1

            prev_hash = entry["hash"]

    return {
        "valid": invalid_line is None,
        "entries": line_no,
        "first_bad_line": invalid_line,
        "reason": invalid_reason or "ok",
        "signed": signed_count,
        "sig_key_loaded": key is not None,
    }


def iter_entries(path: Path | str) -> Iterator[dict]:
    """Stream all entries (unverified)."""
    path = Path(path)
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
