"""Auth for the ops dashboard API (Phase 4 read, Phase 5 write).

The dashboard is an *internal ops* surface, not a customer surface: it reads every
customer's conversations and cases, so it must never be reachable without a token.
An unauthenticated per-phone read would be precisely the ownership oracle
`docs/PROJECT_PLAN.md` §5.3 forbids — anyone could enumerate phone numbers and get
back parcels. The customer-facing ownership check (`app/agents/tracking_agent.py`)
is untouched by this and remains the guard on the customer channel.

**Two tokens, not one.** Phase 4's case for a single shared token rested on the ops
surface being unable to write anything. Phase 5's "claim/resolve a handoff" ends that,
so rather than quietly widening what the read token can do, reads and writes are
separately credentialled:

- `DASHBOARD_TOKEN`      — reads (`/ops/*` GETs, `/metrics/report`). Also accepted on
                           writes, since a deployment that hands out one token to one
                           ops lead shouldn't be forced to manage two.
- `DASHBOARD_WRITE_TOKEN` — required for anything that mutates state.

The property Phase 4 documented survives in scoped form: **a holder of the read token
alone still cannot write.** That is the claim to defend, not "the dashboard is
read-only", which is no longer true.

Both fail closed — unset means 503, never open. With `DASHBOARD_WRITE_TOKEN` unset the
ops API is exactly as read-only as it was in Phase 4. Real per-user accounts are still
Phase 6, which is why the write endpoints require an explicit `actor` in the body
instead of inferring who acted from the credential.
"""
import secrets

from fastapi import Header, HTTPException, status

from app.core import config

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers=_UNAUTHORIZED_HEADERS,
        )
    return token


def _matches_any(token: str, expected: list[str]) -> bool:
    """Constant-time compare against each candidate, so a wrong token can't be
    recovered by timing. Deliberately does not short-circuit on the first match."""
    matched = False
    for candidate in expected:
        if secrets.compare_digest(token, candidate):
            matched = True
    return matched


def require_dashboard_token(authorization: str | None = Header(None)) -> None:
    """FastAPI dependency for reads: allow the request only for
    `Authorization: Bearer <token>` matching `DASHBOARD_TOKEN` (or the write token,
    which is strictly more privileged). Config is read via the module, not a
    from-import, so it stays monkeypatchable in tests."""
    accepted = [t for t in (config.DASHBOARD_TOKEN, config.DASHBOARD_WRITE_TOKEN) if t]
    if not config.DASHBOARD_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard access is not configured — set DASHBOARD_TOKEN in the environment.",
        )

    if not _matches_any(_bearer(authorization), accepted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard token.",
            headers=_UNAUTHORIZED_HEADERS,
        )


def require_dashboard_write_token(authorization: str | None = Header(None)) -> None:
    """FastAPI dependency for writes. Requires `DASHBOARD_WRITE_TOKEN` specifically —
    the read token is **not** accepted here, which is the entire point of splitting
    them. Unset fails closed, leaving the ops surface as read-only as Phase 4's."""
    expected = config.DASHBOARD_WRITE_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Ops writes are not configured — set DASHBOARD_WRITE_TOKEN in the "
                "environment. Until then the ops API is read-only."
            ),
        )

    if not _matches_any(_bearer(authorization), [expected]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ops write token.",
            headers=_UNAUTHORIZED_HEADERS,
        )
