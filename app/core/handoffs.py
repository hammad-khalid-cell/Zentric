"""Store for human handoffs — the state that decides whether the bot is allowed to
reply to a given customer (Phase 5).

Deliberately shaped like `app/core/pending_actions.py`: durable Postgres rows, plain
dicts across the seam (never ORM objects), and lazy expiry on read so no sweeper job is
needed for correctness. The difference is the key — pending actions are parcel-scoped,
handoffs are **conversation-scoped**, because human ownership applies to a customer's
whole thread rather than to one parcel.

Only `claimed` suppresses the bot. An `open` handoff means "a human has been notified
but hasn't picked it up yet" — silencing the bot then would leave the customer with
nothing at all, which is worse than the bot continuing to answer while they wait.
"""
from datetime import datetime, timedelta, timezone

from app.core import config
from app.core.database import SessionLocal
from app.models.handoff import Handoff

STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_RESOLVED = "resolved"
STATUS_EXPIRED = "expired"

# Statuses that mean "this handoff is still live" — i.e. not resolved or expired.
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_CLAIMED)


def _ttl() -> timedelta:
    return timedelta(hours=config.HANDOFF_TTL_HOURS)


def _to_dict(handoff: Handoff) -> dict:
    return {
        "id": handoff.id,
        "customer_phone": handoff.customer_phone,
        "tracking_number": handoff.tracking_number,
        "reason": handoff.reason,
        "ticket_id": handoff.ticket_id,
        "status": handoff.status,
        "claimed_by": handoff.claimed_by,
        "claimed_at": handoff.claimed_at,
        "resolved_by": handoff.resolved_by,
        "resolved_at": handoff.resolved_at,
        "resolution_note": handoff.resolution_note,
        "notified_at": handoff.notified_at,
        "notify_failed": handoff.notify_failed,
        "expires_at": handoff.expires_at,
        "created_at": handoff.created_at,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes; the naive guard is defensive so a
    comparison can never silently assume the server's local zone."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_handoff(customer_phone: str, reason: str,
                   tracking_number: str | None = None,
                   ticket_id: str | None = None) -> dict:
    """Open a handoff for a customer. Idempotent per customer while one is still live:
    a customer who sends three angry messages in a row produces one handoff, not three
    — the same dedup discipline as `create_ticket` and `create_pending_action`.

    An already-*claimed* handoff also counts as existing, so a new escalation never
    silently yanks a thread back from the human working it.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(Handoff)
            .filter(Handoff.customer_phone == customer_phone,
                    Handoff.status.in_(ACTIVE_STATUSES))
            .order_by(Handoff.created_at.desc())
            .first()
        )
        if existing:
            return {**_to_dict(existing), "already_existed": True}

        handoff = Handoff(
            customer_phone=customer_phone,
            tracking_number=tracking_number,
            reason=reason,
            ticket_id=ticket_id,
            status=STATUS_OPEN,
            expires_at=datetime.now(timezone.utc) + _ttl(),
        )
        db.add(handoff)
        db.commit()
        return {**_to_dict(handoff), "already_existed": False}
    finally:
        db.close()


def get_active_handoff(customer_phone: str) -> dict | None:
    """The live handoff for this customer, or None. Expired rows are flipped to
    'expired' in place (lazy sweep) so a human who claimed a thread and walked away
    can't silence the bot for that customer indefinitely."""
    db = SessionLocal()
    try:
        handoff = (
            db.query(Handoff)
            .filter(Handoff.customer_phone == customer_phone,
                    Handoff.status.in_(ACTIVE_STATUSES))
            .order_by(Handoff.created_at.desc())
            .first()
        )
        if handoff is None:
            return None

        expires_at = _as_utc(handoff.expires_at)
        if expires_at and expires_at < datetime.now(timezone.utc):
            handoff.status = STATUS_EXPIRED
            db.commit()
            return None

        return _to_dict(handoff)
    finally:
        db.close()


def mark_notified(handoff_id: int, delivered: bool) -> None:
    """Record whether the staff notification actually went out. Failure is stored, not
    swallowed — an un-notified handoff sitting in the queue is exactly the thing an ops
    lead needs to be able to see."""
    db = SessionLocal()
    try:
        handoff = db.query(Handoff).filter(Handoff.id == handoff_id).first()
        if not handoff:
            return
        if delivered:
            handoff.notified_at = datetime.now(timezone.utc)
            handoff.notify_failed = False
        else:
            handoff.notify_failed = True
        db.commit()
    finally:
        db.close()


class HandoffTransitionError(Exception):
    """A claim/resolve that doesn't apply to the row's current state. Surfaced as a
    409 by the write API rather than silently no-op'ing: a repeat click must not
    quietly rewrite `claimed_at`/`resolved_at` and corrupt the audit trail."""

    def __init__(self, message: str, current_status: str | None = None):
        super().__init__(message)
        self.current_status = current_status


def claim_handoff(handoff_id: int, actor: str) -> dict:
    """Take ownership of a thread. This is the transition that silences the bot, so it
    is recorded with who and when, and it refreshes the expiry — the TTL should run
    from the moment a human took it, not from when it was raised."""
    db = SessionLocal()
    try:
        handoff = db.query(Handoff).filter(Handoff.id == handoff_id).first()
        if not handoff:
            raise HandoffTransitionError(f"No handoff with id {handoff_id}.")
        if handoff.status != STATUS_OPEN:
            raise HandoffTransitionError(
                f"Handoff {handoff_id} is '{handoff.status}', so it cannot be claimed.",
                current_status=handoff.status,
            )

        handoff.status = STATUS_CLAIMED
        handoff.claimed_by = actor
        handoff.claimed_at = datetime.now(timezone.utc)
        handoff.expires_at = datetime.now(timezone.utc) + _ttl()
        db.commit()
        return _to_dict(handoff)
    finally:
        db.close()


def resolve_handoff(handoff_id: int, actor: str, note: str | None = None) -> dict:
    """Hand the thread back to the bot. Allowed from `open` too — a case a human
    settled out of band (a phone call) never needs claiming first."""
    db = SessionLocal()
    try:
        handoff = db.query(Handoff).filter(Handoff.id == handoff_id).first()
        if not handoff:
            raise HandoffTransitionError(f"No handoff with id {handoff_id}.")
        if handoff.status not in ACTIVE_STATUSES:
            raise HandoffTransitionError(
                f"Handoff {handoff_id} is already '{handoff.status}'.",
                current_status=handoff.status,
            )

        handoff.status = STATUS_RESOLVED
        handoff.resolved_by = actor
        handoff.resolved_at = datetime.now(timezone.utc)
        handoff.resolution_note = note
        db.commit()
        return _to_dict(handoff)
    finally:
        db.close()


def list_handoffs(status: str | None = None, limit: int = 100) -> list[dict]:
    """Newest-first handoff queue for the dashboard."""
    db = SessionLocal()
    try:
        query = db.query(Handoff)
        if status:
            query = query.filter(Handoff.status == status)
        rows = query.order_by(Handoff.created_at.desc()).limit(limit).all()
        return [_to_dict(h) for h in rows]
    finally:
        db.close()
