"""Ops **write** API — the first endpoints on this surface that change state (Phase 5).

Kept in its own module, with its own router-level dependency, rather than bolted onto
`ops_routes.py`. That file's invariant — *every endpoint here is a GET behind the read
token, so the dashboard cannot introduce an unaudited state change* — is worth being
able to verify by reading one file, and mixing a POST into it would quietly destroy
that. Everything that mutates lives here and nowhere else.

Three properties this surface must keep (`docs/PROJECT_PLAN.md` §5.2):

1. **Separately credentialled.** `require_dashboard_write_token` demands
   `DASHBOARD_WRITE_TOKEN`; the read token is refused. A holder of the read token
   alone still cannot write.
2. **Attributed.** `actor` is required in every body and is never defaulted. There is
   no user table yet (Phase 6), so an explicit name is the honest way to record who
   acted — an audit row reading "ops" would be worse than no claim at all.
3. **Non-idempotent transitions fail loudly.** Claiming an already-claimed handoff, or
   resolving a resolved one, returns 409 rather than silently rewriting `claimed_at` /
   `resolved_at` and corrupting the trail.

Phase 6 added the delivery-attempt endpoint, which **does** move a parcel — so the older
claim that "nothing here touches a parcel" no longer holds and has been replaced rather
than quietly left to rot. What still holds, and matters more:

- **The customer-side ownership check is untouched.** `tracking_agent.py` is what
  decides whether a *customer* may see a parcel, and nothing here relaxes it. These
  endpoints are reachable only with the ops write token, which is not something a
  customer has.
- **No endpoint here decides an outcome.** The delivery route reports what happened to
  an attempt; `delivery_state.next_status` — a pure lookup — decides what that means for
  the parcel (§5.1). A caller cannot pick the parcel's next state.
- **Anything that moves the RTO figure records where it came from.** See
  `delivery_service.MODELLED_SOURCES`.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.core import handoffs
from app.core.auth import require_dashboard_write_token
from app.services import delivery_service

router = APIRouter(prefix="/ops", dependencies=[Depends(require_dashboard_write_token)])


class ActorRequest(BaseModel):
    """Base for every body on this router. `actor` is required and must be non-blank —
    property 2 above — and inheriting it is what keeps that true of *new* write
    endpoints rather than only the ones that remembered."""

    actor: str = Field(..., min_length=1, max_length=80,
                       description="Who is performing this action (staff name or id)")

    def clean_actor(self) -> str:
        # min_length=1 stops an empty string; this stops "   ", which would otherwise
        # produce an audit row attributing a real state change to nobody.
        actor = self.actor.strip()
        if not actor:
            raise HTTPException(status_code=422, detail="actor must not be blank")
        return actor


class HandoffActionRequest(ActorRequest):
    note: str | None = Field(None, max_length=500,
                             description="Optional free-text resolution note")


def _transition(action, handoff_id: int, *args) -> dict:
    """Run a store transition, mapping an inapplicable state change to 409."""
    try:
        return action(handoff_id, *args)
    except handoffs.HandoffTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/handoffs/{handoff_id}/claim")
def claim_handoff(payload: HandoffActionRequest, handoff_id: int = Path(..., ge=1)):
    """Take ownership of a conversation. **This is the transition that silences the
    bot** — from here until it's resolved (or the TTL lapses), inbound messages from
    this customer are logged for the human to read but get no automated reply."""
    handoff = _transition(handoffs.claim_handoff, handoff_id, payload.clean_actor())
    return {"handoff": handoff, "bot_suppressed": True}


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(payload: HandoffActionRequest, handoff_id: int = Path(..., ge=1)):
    """Mark the conversation handled and hand it back to the bot. Allowed directly
    from `open` as well as from `claimed` — a case settled out of band (a phone call)
    never needed claiming first."""
    handoff = _transition(handoffs.resolve_handoff, handoff_id,
                          payload.clean_actor(), payload.note)
    return {"handoff": handoff, "bot_suppressed": False}


class AttemptOutcomeRequest(ActorRequest):
    """Here `actor` is load-bearing rather than courteous: this endpoint writes a row
    that moves the RTO figure, and it is stored on the attempt as `recorded_by`."""

    outcome: Literal["success", "failed"] = Field(
        ..., description="Whether the delivery attempt succeeded")
    failure_reason: str | None = Field(
        None, max_length=120,
        description="Why it failed — only meaningful when outcome is 'failed'")


#: How `record_attempt_outcome`'s refusals map to HTTP. Everything here is a state
#: conflict rather than a bad request: the call was well-formed, the parcel just wasn't
#: in a position to accept it.
_ATTEMPT_REFUSALS = {
    "parcel_not_found": (404, "No parcel with tracking number {tracking_number}."),
    "parcel_journey_complete": (
        409, "Parcel {tracking_number} is already '{status}' — its journey has ended."),
    "not_dispatched": (
        409, "Parcel {tracking_number} is still '{status}' — it hasn't gone out with a "
             "rider yet, so there is no attempt to report."),
    "duplicate_attempt": (
        409, "Attempt already recorded for the current attempt number on "
             "{tracking_number}. A corrective action (reschedule / address update) has "
             "to schedule the next attempt before another can be recorded."),
}


@router.post("/parcels/{tracking_number}/attempt")
def record_delivery_attempt(payload: AttemptOutcomeRequest,
                            tracking_number: str = Path(..., min_length=3, max_length=40)):
    """Report the outcome of a delivery attempt — **the mock delivery management
    system** (Phase 6). This is the same `record_attempt_outcome` seam a real courier
    webhook would call in Phase 7; the only difference is who pulls the trigger, which
    is why the row records `source='ops_console'` and who clicked it.

    That provenance is not decoration. The outcome written here feeds "RTO prevented",
    so the system has to be able to distinguish a number it observed from one a human
    produced — `docs/PROJECT_PLAN.md` §3's modelled-vs-observed rule applied to the
    metric rather than to a constant.

    The transition itself is not decided here: `delivery_state.next_status` is a pure
    lookup, so the caller cannot choose an outcome for the parcel, only report one for
    the attempt (§5.1).
    """
    result = delivery_service.record_attempt_outcome(
        tracking_number,
        payload.outcome,
        payload.failure_reason if payload.outcome == "failed" else None,
        source=delivery_service.SOURCE_OPS_CONSOLE,
        recorded_by=payload.clean_actor(),
        # This endpoint represents a rider having tried, so it is bounded by where the
        # parcel actually is — marking a parcel that never left the origin "delivered"
        # would be indefensible, and it feeds the RTO figure.
        require_dispatched=True,
    )

    if not result.get("recorded"):
        reason = result.get("reason", "")
        status_code, template = _ATTEMPT_REFUSALS.get(
            reason, (409, "Attempt refused for {tracking_number} ({reason})."))
        raise HTTPException(status_code=status_code, detail=template.format(
            tracking_number=tracking_number.upper(),
            status=result.get("status"),
            reason=reason,
        ))

    return {"attempt": result}
