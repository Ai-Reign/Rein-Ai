"""Authentication + RBAC for the Tripwire admin API.

Supports two auth modes (non-exclusive):

1. **Bearer token** — simple, set via `META_AUTH_TOKENS` env var as JSON:
       {"tok_abc123...": ["operator"], "tok_readonly...": ["reader"]}
   Client sends `Authorization: Bearer tok_abc123...`

2. **mTLS client certificate** — enforced by the reverse proxy (nginx / Envoy /
   Traefik) which terminates TLS and forwards the client cert subject in a
   trusted header (`X-Client-Cert-Subject`). We never parse raw certificates
   in app code — that stays in the proxy layer where it belongs.
   Mapping from subject → roles comes from `META_AUTH_CERT_ROLES` env var as JSON:
       {"CN=ops-team,O=acme": ["operator"], "CN=readonly-bot,O=acme": ["reader"]}

Three built-in roles:
    reader    — read state, health, metrics, audit
    operator  — everything above + revive, override, halt, resume
    admin     — everything above + rotate keys, change config (future)

If NEITHER env var is set, the router falls back to "open" mode (every caller
is granted all roles). This matches existing localhost-only behavior so
upgrading doesn't break existing deployments.

SOC 2 references:
    CC6.1 — logical access controls
    CC6.2 — unique identifier per principal
    CC6.3 — restrict access by role
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


TOKEN_ENV = "META_AUTH_TOKENS"
CERT_ROLES_ENV = "META_AUTH_CERT_ROLES"
CERT_SUBJECT_HEADER = "X-Client-Cert-Subject"


@dataclass
class AuthContext:
    subject: str
    roles: List[str] = field(default_factory=list)
    auth_method: str = "none"  # "bearer" | "mtls" | "open"

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def _load_tokens() -> dict:
    raw = os.environ.get(TOKEN_ENV)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {str(k): list(v) for k, v in d.items() if isinstance(v, list)}
    except json.JSONDecodeError:
        return {}


def _load_cert_roles() -> dict:
    raw = os.environ.get(CERT_ROLES_ENV)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {str(k): list(v) for k, v in d.items() if isinstance(v, list)}
    except json.JSONDecodeError:
        return {}


def _auth_configured() -> bool:
    """True iff at least one auth mode is actively configured."""
    return bool(os.environ.get(TOKEN_ENV)) or bool(os.environ.get(CERT_ROLES_ENV))


_bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthContext:
    """Resolve the caller's AuthContext from either bearer token or mTLS header.

    Bearer takes precedence if both are present (operator explicitly authed).
    """
    # Open mode — no auth configured, grant full access
    if not _auth_configured():
        return AuthContext(subject="anonymous", roles=["admin"], auth_method="open")

    tokens = _load_tokens()
    cert_roles = _load_cert_roles()

    # Try bearer first
    if credentials and credentials.scheme.lower() == "bearer":
        tok = credentials.credentials
        if tok in tokens:
            return AuthContext(subject=f"token:{_token_id(tok)}",
                                roles=tokens[tok], auth_method="bearer")

    # Try mTLS header
    cert_subj = request.headers.get(CERT_SUBJECT_HEADER)
    if cert_subj and cert_subj in cert_roles:
        return AuthContext(subject=f"cert:{cert_subj}",
                            roles=cert_roles[cert_subj], auth_method="mtls")

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail="authentication required",
                         headers={"WWW-Authenticate": "Bearer"})


def _token_id(tok: str) -> str:
    """Short, non-reversible handle for logging. Never log the token itself."""
    import hashlib
    return hashlib.sha256(tok.encode()).hexdigest()[:12]


def require_role(role: str) -> Callable:
    """FastAPI dependency factory: enforces that the caller has `role`.

    Usage:
        @router.get("/state", dependencies=[Depends(require_role("reader"))])
    """
    async def _dep(ctx: AuthContext = Depends(_resolve_context)) -> AuthContext:
        if not ctx.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{role}' required (subject={ctx.subject})",
            )
        return ctx
    return _dep


def get_auth_context() -> Callable:
    """Dependency that returns the current AuthContext without role enforcement."""
    async def _dep(ctx: AuthContext = Depends(_resolve_context)) -> AuthContext:
        return ctx
    return _dep
