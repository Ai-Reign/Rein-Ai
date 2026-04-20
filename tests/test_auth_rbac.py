"""Auth + RBAC tests for the Rein admin API.

Covers:
- Open mode (no env vars) → backwards compat, all endpoints allowed
- Bearer token auth with reader/operator/admin roles
- Proper 401 on missing/bogus credentials
- Proper 403 on insufficient role
- mTLS subject header auth (proxy scenario)
- Token-id appears in `by` field of mutation responses but raw token never does
- /whoami returns identity for diagnostics
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rein_ai import Rein, ReinConfig
from rein_ai.api import build_router
from rein_ai.auth import CERT_ROLES_ENV, CERT_SUBJECT_HEADER, TOKEN_ENV


@pytest.fixture
async def brain(tmp_path: Path):
    cfg = ReinConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0)
    b = Rein(cfg=cfg, persist_dir=tmp_path)
    await b.start()
    yield b
    await b.shutdown()


def _app(brain) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(brain))
    return TestClient(app)


# ---------- Open mode (backwards compat) ----------

async def test_open_mode_allows_all_endpoints(brain, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(CERT_ROLES_ENV, raising=False)
    c = _app(brain)
    assert c.get("/rein/state").status_code == 200
    assert c.post("/rein/halt", json={"reason": "test"}).status_code == 200
    assert c.post("/rein/resume", json={}).status_code == 200


async def test_open_mode_whoami_reports_open(brain, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(CERT_ROLES_ENV, raising=False)
    c = _app(brain)
    r = c.get("/rein/whoami")
    assert r.status_code == 200
    assert r.json()["auth_method"] == "open"


# ---------- Bearer token ----------

async def test_bearer_reader_can_read_cant_mutate(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({
        "tok_reader_xxx": ["reader"],
        "tok_op_xxx":     ["operator"],
    }))
    c = _app(brain)

    r = c.get("/rein/state",
              headers={"Authorization": "Bearer tok_reader_xxx"})
    assert r.status_code == 200

    r = c.post("/rein/halt", json={"reason": "test"},
               headers={"Authorization": "Bearer tok_reader_xxx"})
    assert r.status_code == 403


async def test_bearer_operator_can_mutate(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({
        "tok_op_xxx": ["operator"],
    }))
    c = _app(brain)
    r = c.post("/rein/halt", json={"reason": "drill"},
               headers={"Authorization": "Bearer tok_op_xxx"})
    assert r.status_code == 200
    assert r.json()["halted"] is True
    # `by` field uses token ID hash, NOT the raw token
    assert "tok_op_xxx" not in r.json()["by"]


async def test_bearer_admin_inherits_all_roles(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({"tok_admin": ["admin"]}))
    c = _app(brain)
    assert c.get("/rein/state",
                 headers={"Authorization": "Bearer tok_admin"}).status_code == 200
    assert c.post("/rein/halt", json={"reason": "t"},
                  headers={"Authorization": "Bearer tok_admin"}).status_code == 200


async def test_missing_bearer_returns_401(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({"tok": ["reader"]}))
    c = _app(brain)
    assert c.get("/rein/state").status_code == 401


async def test_bogus_bearer_returns_401(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({"tok_good": ["reader"]}))
    c = _app(brain)
    r = c.get("/rein/state",
              headers={"Authorization": "Bearer tok_NOT_REAL"})
    assert r.status_code == 401


async def test_whoami_never_leaks_raw_token(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({"tok_secret_12345": ["operator"]}))
    c = _app(brain)
    r = c.get("/rein/whoami",
              headers={"Authorization": "Bearer tok_secret_12345"})
    assert r.status_code == 200
    body = r.json()
    assert "tok_secret_12345" not in json.dumps(body)
    assert body["auth_method"] == "bearer"


# ---------- mTLS subject ----------

async def test_mtls_subject_header_grants_role(brain, monkeypatch):
    monkeypatch.setenv(CERT_ROLES_ENV, json.dumps({
        "CN=ops-bot,O=acme": ["operator"],
    }))
    c = _app(brain)
    r = c.post("/rein/halt", json={"reason": "drill"},
               headers={CERT_SUBJECT_HEADER: "CN=ops-bot,O=acme"})
    assert r.status_code == 200
    assert "cert:CN=ops-bot,O=acme" in r.json()["by"]


async def test_unknown_cert_subject_returns_401(brain, monkeypatch):
    monkeypatch.setenv(CERT_ROLES_ENV, json.dumps({
        "CN=known,O=acme": ["reader"],
    }))
    c = _app(brain)
    r = c.get("/rein/state",
              headers={CERT_SUBJECT_HEADER: "CN=stranger,O=evil"})
    assert r.status_code == 401


# ---------- Priority (bearer wins over mtls) ----------

async def test_bearer_takes_precedence_over_mtls(brain, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, json.dumps({"tok_admin": ["admin"]}))
    monkeypatch.setenv(CERT_ROLES_ENV, json.dumps({
        "CN=readonly,O=acme": ["reader"],
    }))
    c = _app(brain)
    # Client sends BOTH — bearer should win and grant admin role
    r = c.post("/rein/halt", json={"reason": "t"},
               headers={"Authorization": "Bearer tok_admin",
                        CERT_SUBJECT_HEADER: "CN=readonly,O=acme"})
    assert r.status_code == 200
    # whoami confirms bearer
    r = c.get("/rein/whoami",
              headers={"Authorization": "Bearer tok_admin",
                       CERT_SUBJECT_HEADER: "CN=readonly,O=acme"})
    assert r.json()["auth_method"] == "bearer"
