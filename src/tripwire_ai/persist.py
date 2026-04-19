"""Persistence: atomic JSON state writes + append-only audit log."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from tripwire_ai.types import TripwireState


AUDIT_KEEP_LAST = 5000  # truncated at midnight UTC by external rotator (Task 13)


def save_state(state: TripwireState, path: Path | str) -> None:
    """Atomic write: write to .tmp then os.replace into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), separators=(",", ":"), sort_keys=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))


def load_state(path: Path | str) -> Optional[TripwireState]:
    """Return TripwireState or None on missing/corrupt file."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return TripwireState.from_dict(d)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def append_audit(path: Path | str, entry: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":"), sort_keys=True))
        f.write("\n")


def load_recent_audit(path: Path | str, n: int = 100) -> List[dict]:
    path = Path(path)
    if not path.exists():
        return []
    # Read tail efficiently: load whole file (it's bounded ~5KB/entry × few thousand).
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
