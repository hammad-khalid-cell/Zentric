from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.parcel import Parcel
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.models.delivery_attempt import DeliveryAttempt


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


def record_attempt_outcome(tracking_number: str, outcome: str, failure_reason: str | None = None) -> dict:
    """Record a delivery attempt's outcome for a parcel (attempt_no = the parcel's
    current attempt_count, so it lines up with whatever apply_reschedule /
    apply_address_update last set). Deduplicated per (tracking_number, attempt_no) —
    a second call for the same attempt is a no-op, same pattern as
    action_service.record_notification.

    If this resolves an attempt that followed an unresolved corrective Intervention,
    also writes the InterventionOutcome linking that intervention to this outcome
    ('delivered' on success, 'still_failed' on failure) — this is the literal
    "RTO prevented" record the metrics service reads.
    """
    db = SessionLocal()
    try:
        parcel = db.query(Parcel).filter_by(tracking_number=tracking_number.upper()).first()
        if not parcel:
            return {"recorded": False, "reason": "parcel_not_found"}

        attempt = DeliveryAttempt(
            tracking_number=parcel.tracking_number,
            attempt_no=parcel.attempt_count or 0,
            outcome=outcome,
            failure_reason=failure_reason,
        )
        db.add(attempt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"recorded": False, "reason": "duplicate_attempt"}

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
        }
    finally:
        db.close()
