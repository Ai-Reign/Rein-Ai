"""FastAPI admin router tests."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tripwire_ai import Tripwire, TripwireConfig
from tripwire_ai.types import Status
from tripwire_ai.api import build_router


@pytest.fixture
async def client(tmp_path: Path):
    cfg = TripwireConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0,
                     min_samples_for_kill=3, min_samples_for_green=3,
                     exec_min_attempts=3)
    brain = Tripwire(cfg=cfg, persist_dir=tmp_path)
    await brain.start()

    # Seed data
    for i in range(5):
        await brain.record_fill(source="a", series="s", ticker=f"t{i}",
                                filled=True, slippage_cents=0, attempt_at=time.time())
    brain.force_status("a", "s", Status.GREEN, "", ttl_seconds=0)

    app = FastAPI()
    app.include_router(build_router(brain))
    yield TestClient(app), brain
    await brain.shutdown()


async def test_get_state(client):
    c, _ = client
    r = c.get("/tripwire/state")
    assert r.status_code == 200
    assert "health" in r.json()


async def test_get_health_known(client):
    c, _ = client
    r = c.get("/tripwire/health/a/s")
    assert r.status_code == 200
    assert r.json()["status"] == "green"


async def test_get_health_unknown_returns_404(client):
    c, _ = client
    r = c.get("/tripwire/health/ghost/x")
    assert r.status_code == 404


async def test_get_metrics_shape(client):
    c, _ = client
    r = c.get("/tripwire/metrics")
    assert r.status_code == 200
    m = r.json()
    assert "totals_by_status" in m
    assert "kills_24h" in m
    assert "fills_24h" in m
    assert "config" in m
    assert m["config"]["enabled"] is True


async def test_override_and_revive(client):
    c, _ = client
    # Force RED
    r = c.post("/tripwire/override", json={"source": "a", "series": "s",
                                         "status": "red", "reason": "manual"})
    assert r.status_code == 200
    assert c.get("/tripwire/health/a/s").json()["status"] == "red"

    # Revive
    r = c.post("/tripwire/revive", json={"source": "a", "series": "s", "reason": "fixed"})
    assert r.status_code == 200


async def test_halt_and_resume(client):
    c, b = client
    assert c.post("/tripwire/halt", json={"reason": "drill"}).status_code == 200
    assert b._state.halted is True

    assert c.post("/tripwire/resume", json={}).status_code == 200
    assert b._state.halted is False


async def test_override_rejects_invalid_status(client):
    c, _ = client
    r = c.post("/tripwire/override", json={"source": "a", "series": "s",
                                         "status": "purple", "reason": ""})
    assert r.status_code == 400
