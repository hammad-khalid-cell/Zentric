from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.ticket import Ticket
from app.models.reroute import Reroute
from app.models.notification import Notification
from app.models.parcel import Parcel
from app.models.intervention import Intervention

# When a corrective action reschedules a delivery, this is how far ahead the next
# attempt is set. Kept as a module constant so it's easy to tune / move to config.
RESCHEDULE_DAYS = 2


def find_open_ticket(tracking_number: str) -> dict | None:
    db = SessionLocal()
    try:
        ticket = (
            db.query(Ticket)
            .filter(Ticket.tracking_number == tracking_number, Ticket.status == "open")
            .first()
        )
        return {"ticket_id": ticket.ticket_id, "status": ticket.status} if ticket else None
    finally:
        db.close()


def find_active_reroute(tracking_number: str) -> dict | None:
    db = SessionLocal()
    try:
        reroute = (
            db.query(Reroute)
            .filter(Reroute.tracking_number == tracking_number, Reroute.status == "requested")
            .first()
        )
        return {"reroute_id": reroute.reroute_id, "status": reroute.status} if reroute else None
    finally:
        db.close()


def create_ticket(tracking_number: str, reason: str, decision: str) -> dict:
    # Guard against the same delay complaint (retry, duplicate message, repeat graph
    # run) spawning a second open ticket for a parcel that already has one.
    existing = find_open_ticket(tracking_number)
    if existing:
        return {**existing, "already_existed": True}

    db = SessionLocal()
    try:
        ticket = Ticket(
            ticket_id="PENDING",
            tracking_number=tracking_number,
            reason=reason,
            decision=decision,
        )
        db.add(ticket)
        db.flush()  # assigns the DB-generated autoincrement id — atomic, no count()-based race
        ticket.ticket_id = f"TCK-{ticket.id:04d}"
        db.commit()

        return {"ticket_id": ticket.ticket_id, "status": "created", "already_existed": False}
    finally:
        db.close()


def create_reroute_request(tracking_number: str, reason: str) -> dict:
    existing = find_active_reroute(tracking_number)
    if existing:
        return {**existing, "already_existed": True}

    db = SessionLocal()
    try:
        reroute = Reroute(
            reroute_id="PENDING",
            tracking_number=tracking_number,
            reason=reason,
            status="requested",
        )
        db.add(reroute)
        db.flush()
        reroute.reroute_id = f"RRT-{reroute.id:04d}"
        db.commit()

        return {"reroute_id": reroute.reroute_id, "status": "requested", "already_existed": False}
    finally:
        db.close()


def _record_intervention(db, tracking_number: str, action: str, detail: str) -> str:
    """Insert an auditable Intervention row inside the caller's session and return
    its human-readable id. Flush (not commit) so it shares the parcel-mutation
    transaction — the write-back and its audit row commit atomically."""
    intervention = Intervention(
        intervention_id="PENDING",
        tracking_number=tracking_number,
        action=action,
        detail=detail,
    )
    db.add(intervention)
    db.flush()  # assigns the autoincrement id, same race-free pattern as tickets
    intervention.intervention_id = f"INT-{intervention.id:04d}"
    return intervention.intervention_id


def apply_address_update(tracking_number: str, new_address: str, customer_phone: str) -> dict:
    """Corrective action: update the parcel's delivery address AND reschedule the next
    attempt (a fixed address is only useful with a fresh delivery attempt). Verifies
    ownership defensively even though the caller already did — a parcel is only ever
    mutated for the phone number that owns it. Writes an Intervention audit row."""
    db = SessionLocal()
    try:
        parcel = db.query(Parcel).filter_by(tracking_number=tracking_number.upper()).first()
        if not parcel or parcel.customer_phone != customer_phone:
            return {"applied": False, "reason": "not_found_or_not_owned"}

        old_address = parcel.address_line
        new_date = date.today() + timedelta(days=RESCHEDULE_DAYS)

        parcel.address_line = new_address
        parcel.expected_delivery_date = new_date
        parcel.attempt_count = (parcel.attempt_count or 0) + 1
        parcel.status = "out_for_delivery"
        parcel.delay_reason = None  # the address problem is resolved

        detail = f"Address updated from '{old_address}' to '{new_address}'; redelivery set for {new_date}."
        intervention_id = _record_intervention(db, parcel.tracking_number, "update_address", detail)
        db.commit()

        return {
            "applied": True,
            "intervention_id": intervention_id,
            "new_address": new_address,
            "new_delivery_date": new_date,
            "attempt_count": parcel.attempt_count,
        }
    finally:
        db.close()


def apply_reschedule(tracking_number: str, customer_phone: str, window: str | None = None) -> dict:
    """Corrective action: reschedule the next delivery attempt (optionally recording a
    preferred window). Ownership-verified; writes an Intervention audit row."""
    db = SessionLocal()
    try:
        parcel = db.query(Parcel).filter_by(tracking_number=tracking_number.upper()).first()
        if not parcel or parcel.customer_phone != customer_phone:
            return {"applied": False, "reason": "not_found_or_not_owned"}

        new_date = date.today() + timedelta(days=RESCHEDULE_DAYS)

        parcel.expected_delivery_date = new_date
        parcel.attempt_count = (parcel.attempt_count or 0) + 1
        parcel.status = "out_for_delivery"
        parcel.delay_reason = None
        if window:
            parcel.preferred_delivery_window = window

        window_note = f" (preferred window: {window})" if window else ""
        detail = f"Delivery rescheduled for {new_date}{window_note}."
        intervention_id = _record_intervention(db, parcel.tracking_number, "reschedule", detail)
        db.commit()

        return {
            "applied": True,
            "intervention_id": intervention_id,
            "new_delivery_date": new_date,
            "preferred_delivery_window": window,
            "attempt_count": parcel.attempt_count,
        }
    finally:
        db.close()


def find_existing_notification(tracking_number: str, delay_reason: str | None) -> dict | None:
    db = SessionLocal()
    try:
        notification = (
            db.query(Notification)
            .filter(
                Notification.tracking_number == tracking_number,
                Notification.delay_reason == delay_reason,
            )
            .first()
        )
        return {"message": notification.message, "sent_at": notification.sent_at} if notification else None
    finally:
        db.close()


def record_notification(tracking_number: str, delay_reason: str | None, decision: str, message: str) -> bool:
    """Returns False (instead of raising) if a notification for this tracking_number +
    delay_reason pair was already recorded by a concurrent run — the unique constraint
    on the table is the real guard, this is just a race window between the earlier
    find_existing_notification() check and this insert."""
    db = SessionLocal()
    try:
        db.add(Notification(
            tracking_number=tracking_number,
            delay_reason=delay_reason,
            decision=decision,
            message=message,
        ))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()
