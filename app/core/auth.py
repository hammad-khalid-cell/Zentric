"""Auth for the read-only ops dashboard API (Phase 4).

The dashboard is an *internal ops* surface, not a customer surface: it reads every
customer's conversations and cases, so it must never be reachable without a token.
An unauthenticated per-phone read would be precisely the ownership oracle
`docs/PROJECT_PLAN.md` §5.3 forbids — anyone could enumerate phone numbers and get
back parcels. The customer-facing ownership check (`app/agents/tracking_agent.py`)
is untouched by this and remains the guard on the customer channel.

Fail closed: if `DASHBOARD_TOKEN` is unset the endpoints refuse to serve (503)
instead of defaulting open. A single shared read-only token is enough for an
internal tool with no user table; accounts/roles are Phase 6.
"""
import secrets

from fastapi import Header, HTTPException, status

from app.core import config

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def require_dashboard_token(authorization: str | None = Header(None)) -> None:
    """FastAPI dependency: allow the request only for `Authorization: Bearer <token>`
    matching the configured `DASHBOARD_TOKEN`. Read via the `config` module (not a
    from-import) so it stays monkeypatchable in tests."""
    expected = config.DASHBOARD_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard access is not configured — set DASHBOARD_TOKEN in the environment.",
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers=_UNAUTHORIZED_HEADERS,
        )

    # Constant-time compare so a wrong token can't be recovered by timing.
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard token.",
            headers=_UNAUTHORIZED_HEADERS,
        )
