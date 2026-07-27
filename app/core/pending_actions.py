"""Store for parcel-scoped pending actions — the link between a proactive delay
message and the customer's later reply. Modelled as a durable Postgres table (not a
Redis session key) because it's an auditable record, must outlive the 30-minute
conversational session, and the dashboard/metrics query open interventions from it.

Every function returns plain dicts (never ORM objects) so this stays a clean seam,
matching the other service modules. `get_open_pending_action` lazily expires stale
rows so no separate sweeper job is required for correctness.
"""
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.models.pending_action import PendingAction

# The reply window is deliberately much longer than the 30-min conversational
# session — a customer may not answer a proactive delivery message for a day or two.
PENDING_ACTION_TTL = timedelta(hours=48)


def _to_dict(pa: PendingAction) -> dict:
    return {
        "id": pa.id,
        "tracking_number": pa.tracking_number,
        "customer_phone": pa.customer_phone,
        "trigger_reason": pa.trigger_reason,
        "status": pa.status,
        "expires_at": pa.expires_at,
    }


def create_pending_action(tracking_number: str, customer_phone: str,
                          trigger_reason: str | None) -> dict:
    """Open a pending action for a parcel. Idempotent per tracking_number: if an
    open one already exists (e.g. scan_and_notify re-run), the existing row is
    returned rather than a duplicate created."""
    db = SessionLocal()
    try:
        existing = (
            db.query(PendingAction)
            .filter(PendingAction.tracking_number == tracking_number,
                    PendingAction.status == "open")
            .first()
        )
        if existing:
            return {**_to_dict(existing), "already_existed": True}

        pa = PendingAction(
            tracking_number=tracking_number,
            customer_phone=customer_phone,
            trigger_reason=trigger_reason,
            status="open",
            expires_at=datetime.now(timezone.utc) + PENDING_ACTION_TTL,
        )
        db.add(pa)
        db.commit()
        return {**_to_dict(pa), "already_existed": False}
    finally:
        db.close()


def get_open_pending_action(customer_phone: str) -> dict | None:
    """Return the most recent OPEN, non-expired pending action for this customer,
    or None. Rows whose expiry has passed are flipped to 'expired' in place (lazy
    sweep) so an old, unanswered outreach never silently hijacks a fresh message."""
    db = SessionLocal()
    try:
        pa = (
            db.query(PendingAction)
            .filter(PendingAction.customer_phone == customer_phone,
                    PendingAction.status == "open")
            .order_by(PendingAction.created_at.desc())
            .first()
        )
        if pa is None:
            return None

        expires_at = pa.expires_at
        # Postgres returns tz-aware datetimes; guard the naive case defensively.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            pa.status = "expired"
            db.commit()
            return None

        return _to_dict(pa)
    finally:
        db.close()


def resolve_pending_action(pending_action_id: int) -> None:
    """Mark a pending action resolved once a corrective action has been applied,
    so the customer's next message is classified fresh rather than treated as a
    continuation of the intervention."""
    db = SessionLocal()
    try:
        pa = db.query(PendingAction).filter(PendingAction.id == pending_action_id).first()
        if pa and pa.status == "open":
            pa.status = "resolved"
            db.commit()
    finally:
        db.close()
