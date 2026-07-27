"""Read-only ops dashboard API (Phase 4).

Every endpoint here is a GET behind `require_dashboard_token` — the dashboard reads
the system's own records and never writes, so it cannot introduce an unaudited state
change (`docs/PROJECT_PLAN.md` §5.2), and it is not reachable without the ops token
(§5.3, see `app/core/auth.py`).
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_dashboard_token
from app.services import ops_read

# Same shape the customer-facing routes validate against (app/routes/test_routes.py) —
# kept local rather than imported from a route module; a shared validators module is a
# tidy-up for later.
PHONE_PATTERN = re.compile(r"\d{7,15}")

router = APIRouter(prefix="/ops", dependencies=[Depends(require_dashboard_token)])


def _validate_phone(customer_phone: str) -> str:
    customer_phone = customer_phone.strip()
    if not PHONE_PATTERN.fullmatch(customer_phone):
        raise HTTPException(
            status_code=400,
            detail="customer_phone must be digits only (e.g. 923001234567)",
        )
    return customer_phone


@router.get("/conversations")
def list_conversations(limit: int = Query(50, ge=1, le=200)):
    """Customer list for the conversation pane, most recently active first."""
    return {"conversations": ops_read.list_conversations(limit=limit)}


@router.get("/conversations/{customer_phone}")
def get_conversation(
    customer_phone: str,
    limit: int = Query(100, ge=1, le=500),
    since_id: int | None = Query(None, ge=0, description="Only messages newer than this id — the poll cursor"),
):
    """The in/out thread for one customer. With `since_id` this returns just the new
    messages, which is what the dashboard's poll loop requests."""
    customer_phone = _validate_phone(customer_phone)
    messages = ops_read.get_thread(customer_phone, limit=limit, since_id=since_id)
    return {
        "customer_phone": customer_phone,
        "messages": messages,
        "latest_id": messages[-1]["id"] if messages else since_id,
    }


@router.get("/cases")
def list_cases(
    type: str | None = Query(None, description=f"One of {', '.join(ops_read.CASE_TYPES)}"),
    status: str | None = Query(None, description="Exact status match, e.g. open / delivered / still_failed"),
    limit: int = Query(100, ge=1, le=500),
):
    """Tickets, reroutes, and interventions as one normalised feed."""
    if type is not None and type not in ops_read.CASE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of: {', '.join(ops_read.CASE_TYPES)}",
        )
    return {"cases": ops_read.list_cases(case_type=type, status=status, limit=limit)}
