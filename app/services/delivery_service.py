from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.parcel import Parcel
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.models.delivery_attempt import DeliveryAttempt
from app.services import delivery_state

# Where a delivery outcome came from. Required on every write (see below) because these
# rows feed the "RTO prevented" headline, and a number that can be moved by a button
# has to be able to say so.
SOURCE_AGENT = "agent"                    # the graph noticed an overdue parcel
SOURCE_SCANNER = "scanner"                # the proactive scan noticed it
SOURCE_SIMULATOR = "simulator"            # app/tools/simulate_outcomes.py
SOURCE_OPS_CONSOLE = "ops_console"        # a human clicked "run next attempt"
SOURCE_COURIER_WEBHOOK = "courier_webhook"  # a real delivery system (Phase 7)

#: Sources whose outcomes are **modelled, not observed**. Present them exactly the way
#: `docs/PROJECT_PLAN.md` §3 requires `RTO_COST_PKR` and the demo history to be
#: presented — as a model, never as fact. The other sources are the system's own
#: observations of its own traffic, which is a materially different claim.
MODELLED_SOURCES = frozenset({SOURCE_SIMULATOR, SOURCE_OPS_CONSOLE})


def _find_unresolved_intervention(db, tracking_number: str) -> Intervention | None:
    """Most recent Intervention for this parcel that doesn't have an InterventionOutcome
    yet — the one a resolving DeliveryAttempt should be linked to."""
    resolved_ids = {
        row.intervention_id
        for row in db.query(InterventionOutcome.intervention_id)
        .filter(InterventionOutcome.tracking_number == tracking_number)
        .all()
    }
    interventions = (
        db.query(Intervention)
        .filter(Intervention.tracking_number == tracking_number)
        .order_by(Intervention.created_at.desc())
        .all()
    )
    return next((iv for iv in interventions if iv.intervention_id not in resolved_ids), None)


def record_attempt_outcome(tracking_number: str, outcome: str, failure_reason: str | None = None,
                           *, source: str, recorded_by: str | None = None) -> dict:
    """Record a delivery attempt's outcome for a parcel (attempt_no = the parcel's
    current attempt_count, so it lines up with whatever apply_reschedule /
    apply_address_update last set). Deduplicated per (tracking_number, attempt_no) —
    a second call for the same attempt is a no-op, same pattern as
    action_service.record_notification.

    If this resolves an attempt that followed an unresolved corrective Intervention,
    also writes the InterventionOutcome linking that intervention to this outcome
    ('delivered' on success, 'still_failed' on failure) — this is the literal
    "RTO prevented" record the metrics service reads.

    Also advances the parcel through `delivery_state` (Phase 6), so a resolved attempt
    is observable on the parcel rather than only in the audit trail.

    `source` is **keyword-only and required** on purpose. These rows move the headline
    RTO figure, and some of them come from a human clicking a button; a default would
    let an untagged write slip in silently and quietly blur modelled data into observed.
    Use one of the SOURCE_* constants. `recorded_by` attributes the human-triggered ones.
    """
    db = SessionLocal()
    try:
        parcel = db.query(Parcel).filter_by(tracking_number=tracking_number.upper()).first()
        if not parcel:
            return {"recorded": False, "reason": "parcel_not_found"}

        # A parcel that has been delivered or returned takes no further attempts. Refused
        # here rather than in any one caller, so the ops button, the simulator and a real
        # courier webhook all inherit it — an attempt recorded against a finished parcel
        # would sit in the history contradicting its own outcome.
        if delivery_state.is_terminal(parcel.status):
            return {"recorded": False, "reason": "parcel_journey_complete",
                    "status": parcel.status}

        attempt = DeliveryAttempt(
            tracking_number=parcel.tracking_number,
            attempt_no=parcel.attempt_count or 0,
            outcome=outcome,
            failure_reason=failure_reason,
            source=source,
            recorded_by=recorded_by,
        )
        db.add(attempt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"recorded": False, "reason": "duplicate_attempt"}

        # Phase 6 — move the parcel itself, not just the audit trail. Until now a
        # resolved attempt left the parcel sitting at `out_for_delivery` forever, so
        # "did it actually get delivered?" had no answer on the parcel. `attempt_no` is
        # 0-based (it mirrors `attempt_count`), and the rule is stated in 1-based
        # attempts, hence the +1.
        previous_status = parcel.status
        new_status = delivery_state.next_status(
            parcel.status, outcome, attempt.attempt_no + 1
        )
        if new_status:
            parcel.status = new_status
            if new_status == delivery_state.STATUS_DELIVERED:
                # A delivered parcel has no outstanding delay; leaving the reason set
                # would keep it in find_delayed_parcels' sights and re-notify a
                # customer whose parcel is already in their hands.
                parcel.delay_reason = None

        intervention_outcome_id = None
        unresolved = _find_unresolved_intervention(db, parcel.tracking_number)
        if unresolved:
            io_outcome = "delivered" if outcome == "success" else "still_failed"
            io = InterventionOutcome(
                intervention_id=unresolved.intervention_id,
                tracking_number=parcel.tracking_number,
                delivery_attempt_id=attempt.id,
                outcome=io_outcome,
            )
            db.add(io)
            db.flush()
            intervention_outcome_id = io.id

        db.commit()
        return {
            "recorded": True,
            "delivery_attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "intervention_outcome_id": intervention_outcome_id,
            "previous_status": previous_status,
            "status": parcel.status,
            "status_changed": parcel.status != previous_status,
            "attempts_remaining": delivery_state.attempts_remaining(attempt.attempt_no + 1),
        }
    finally:
        db.close()
