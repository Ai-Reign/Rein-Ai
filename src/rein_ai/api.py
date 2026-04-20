"""FastAPI router exposing /rein/* endpoints.

Security model:
    Read endpoints require `reader` role (or higher).
    Mutate endpoints require `operator` role (or higher).
    Admin-only endpoints require `admin` role.

When no auth is configured (META_AUTH_TOKENS / META_AUTH_CERT_ROLES unset), all
endpoints run in "open" mode for backwards compat with localhost-only
deployments. See rein_ai.auth for the configuration contract.
"""
from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rein_ai.auth import AuthContext, get_auth_context, require_role
from rein_ai.brain import Rein
from rein_ai.persist import load_recent_audit
from rein_ai.types import Status


class ReviveBody(BaseModel):
    source: str
    series: str
    reason: str = ""


class HaltBody(BaseModel):
    reason: str = ""


class ResumeBody(BaseModel):
    pass


class OverrideBody(BaseModel):
    source: str
    series: str
    status: str
    reason: str = ""
    ttl_seconds: float = 0.0


def build_router(brain: Rein) -> APIRouter:
    router = APIRouter(prefix="/rein", tags=["rein"])

    _reader = Depends(require_role("reader"))
    _operator = Depends(require_role("operator"))

    @router.get("/state", dependencies=[_reader])
    def get_state():
        return brain.snapshot()

    @router.get("/health/{source}/{series}", dependencies=[_reader])
    def get_health(source: str, series: str):
        sh = brain._state.health.get((source, series))
        if sh is None:
            raise HTTPException(status_code=404, detail="unknown strategy")
        return sh.to_dict()

    @router.get("/regime", dependencies=[_reader])
    def get_regime():
        r = brain._state.regime
        return {**asdict(r), "regime_id": r.regime_id()}

    @router.get("/audit", dependencies=[_reader])
    def get_audit(n: int = 100):
        return load_recent_audit(brain.audit_path, n=min(max(n, 1), 1000))

    @router.get("/metrics", dependencies=[_reader])
    def get_metrics():
        """Aggregated counters for the last 24h — good for Prometheus scraping."""
        cutoff = time.time() - 86400
        audit = load_recent_audit(brain.audit_path, n=10000)
        recent = [e for e in audit if e.get("at", 0) >= cutoff]
        statuses = [e for e in recent if e.get("event") == "STATUS"]
        fills = [e for e in recent if e.get("event") == "FILL"]

        totals = {"green": 0, "yellow": 0, "red": 0, "black": 0}
        for sh in brain._state.health.values():
            totals[sh.status.value] = totals.get(sh.status.value, 0) + 1

        return {
            "at": time.time(),
            "totals_by_status": totals,
            "kills_24h": sum(1 for s in statuses if s.get("to") in ("red", "black")),
            "revivals_24h": sum(1 for s in statuses if s.get("to") == "green"),
            "fills_24h": len(fills),
            "regime_transitions_24h": sum(1 for e in recent if e.get("event") == "REGIME"),
            "portfolio_halted": brain._state.halted,
            "portfolio_drawdown_today": brain._state.portfolio_drawdown_today,
            "config": {
                "enabled": brain.cfg.enabled,
                "shadow_mode": brain.cfg.shadow_mode,
                "edge_red_p": brain.cfg.edge_red_p,
                "exec_red_fill": brain.cfg.exec_red_fill,
                "capital_red_pct": brain.cfg.capital_red_pct,
            },
        }

    @router.get("/whoami")
    def get_whoami(ctx: AuthContext = Depends(get_auth_context())):
        return {"subject": ctx.subject, "roles": ctx.roles,
                "auth_method": ctx.auth_method}

    @router.post("/revive")
    def post_revive(body: ReviveBody,
                    ctx: AuthContext = Depends(require_role("operator"))):
        ok = brain.manual_revive(body.source, body.series, body.reason)
        if not ok:
            raise HTTPException(status_code=404, detail="unknown strategy")
        return {"ok": True, "by": ctx.subject}

    @router.post("/halt")
    def post_halt(body: HaltBody,
                  ctx: AuthContext = Depends(require_role("operator"))):
        brain._state.halted = True
        brain._state.halted_reason = body.reason or f"manual halt by {ctx.subject}"
        return {"ok": True, "halted": True, "by": ctx.subject}

    @router.post("/resume")
    def post_resume(body: ResumeBody,
                    ctx: AuthContext = Depends(require_role("operator"))):
        brain.manual_resume()
        return {"ok": True, "halted": False, "by": ctx.subject}

    @router.post("/override")
    def post_override(body: OverrideBody,
                      ctx: AuthContext = Depends(require_role("operator"))):
        try:
            status = Status(body.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid status {body.status}")
        brain.force_status(body.source, body.series, status, body.reason, body.ttl_seconds)
        return {"ok": True, "status": status.value, "by": ctx.subject}

    return router
