"""Read-only ops dashboard API (Phase 4).

**Every endpoint in this file is a GET behind `require_dashboard_token`** and writes
nothing, so it cannot introduce an unaudited state change (`docs/PROJECT_PLAN.md`
§5.2), and it is not reachable without the ops token (§5.3, see `app/core/auth.py`).
The single exception is `POST /ops/roi/simulate`, a POST only because it takes a
request body — it is pure computation and touches no state.

Phase 5 added the first genuine writes to the ops surface (claim/resolve a handoff).
They are deliberately **not** here: they live in `app/routes/ops_write_routes.py`
behind a separate `DASHBOARD_WRITE_TOKEN`, precisely so the invariant in the paragraph
above stays true of this file and can be checked by reading it.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import handoffs
from app.core.auth import require_dashboard_token
from app.services import ops_read, roi_service
from app.services.metrics_service import get_metrics_report

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


HANDOFF_STATUSES = (
    handoffs.STATUS_OPEN, handoffs.STATUS_CLAIMED,
    handoffs.STATUS_RESOLVED, handoffs.STATUS_EXPIRED,
)


@router.get("/handoffs")
def list_handoffs(
    status: str | None = Query(None, description=f"One of {', '.join(HANDOFF_STATUSES)}"),
    limit: int = Query(50, ge=1, le=200),
):
    """The human-handoff queue (Phase 5), newest first. Read-only: taking or resolving
    one is a write and lives in `ops_write_routes.py` behind the write token."""
    if status is not None and status not in HANDOFF_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(HANDOFF_STATUSES)}",
        )
    return {"handoffs": handoffs.list_handoffs(status=status, limit=limit)}


class RoiRequest(BaseModel):
    """Every field is optional — the calculator posts only the sliders the panel has
    touched, and the rest fall back to the model defaults (roi_service.ROI_DEFAULTS)
    and the configured cost assumptions. Bounds keep a stray keystroke from producing
    a headline figure the model can't defend."""

    monthly_parcels: int | None = Field(None, ge=0, le=100_000_000)
    cod_share_pct: float | None = Field(None, ge=0, le=100)
    first_attempt_failure_rate_pct: float | None = Field(None, ge=0, le=100)
    preventable_share_pct: float | None = Field(None, ge=0, le=100)
    intervention_success_rate_pct: float | None = Field(None, ge=0, le=100)
    wismo_contacts_per_parcel: float | None = Field(None, ge=0, le=20)
    deflection_rate_pct: float | None = Field(None, ge=0, le=100)
    human_cost_per_query_pkr: float | None = Field(None, ge=0, le=100_000)
    bot_cost_per_query_pkr: float | None = Field(None, ge=0, le=100_000)
    rto_cost_pkr: float | None = Field(None, ge=0, le=1_000_000)


@router.get("/roi/assumptions")
def roi_assumptions():
    """What the ROI calculator loads with: the model defaults, plus what the system
    has measured about itself (with sample sizes, reported separately — see
    roi_service.get_roi_assumptions)."""
    return roi_service.get_roi_assumptions(measured=get_metrics_report())


@router.post("/roi/simulate")
def roi_simulate(payload: RoiRequest):
    """Project savings from the panel's inputs. A POST for the request body only —
    this is pure computation and writes nothing, so the ops surface stays read-only."""
    return roi_service.simulate(payload.model_dump(exclude_unset=True))
