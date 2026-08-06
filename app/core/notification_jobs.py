"""Retry accounting and the dead-letter queue for proactive notifications (Phase 6).

The proactive scan is the only part of this system that acts without a customer having
said anything, which makes its failures uniquely invisible: nobody is waiting on a reply
to notice it didn't happen. Before this, a failed notification was a `logger.exception`
and nothing else.

Deliberately small. Retrying is not scheduled here — a parcel that failed stays overdue
and un-notified, so the next scan retries it for free. This module only:

  1. counts the attempts, so a parcel that fails forever can be recognised;
  2. gives up after `MAX_NOTIFY_ATTEMPTS`, so one poisoned parcel doesn't consume the
     scan (and an LLM call) on every run, for ever;
  3. keeps the give-ups somewhere the dashboard can read, because a customer who was
     never warned about their delay is exactly the case an ops lead has to see.

Same durable + lazy shape as `pending_actions.py` and `handoffs.py`: state in Postgres,
no background sweeper.
"""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from app.core.database import SessionLocal
from app.models.notification_failure import NotificationFailure

logger = logging.getLogger(__name__)

STATUS_RETRYING = "retrying"
STATUS_DEAD = "dead"

#: Attempts before a parcel's notification is dead-lettered. Three scans at the default
#: five-minute interval is roughly fifteen minutes of trying — long enough to ride out
#: the DNS wobbles this project actually sees on Groq/Supabase/Upstash, short enough that
#: a genuinely broken parcel stops burning an LLM call every run.
MAX_NOTIFY_ATTEMPTS = 3


def _key(delay_reason: str | None) -> str:
    return delay_reason or "unknown"


def record_failure(tracking_number: str, delay_reason: str | None, error: str) -> dict:
    """Count a failed notification attempt, dead-lettering it once it has used up its
    retries. Returns the row's state so the caller can log the transition.

    Never raises: this runs inside the scan's own exception handler, and a failure to
    record a failure must not take the scan down with it.
    """
    reason = _key(delay_reason)
    # Errors can carry a whole traceback-ish string; the column is Text but there's no
    # value in storing an essay per row.
    error = (error or "")[:1000]

    db = SessionLocal()
    try:
        row = (
            db.query(NotificationFailure)
            .filter_by(tracking_number=tracking_number, delay_reason=reason)
            .first()
        )
        if row is None:
            row = NotificationFailure(
                tracking_number=tracking_number, delay_reason=reason,
                attempts=1, status=STATUS_RETRYING, last_error=error,
            )
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                # A concurrent scan inserted it first — fall back to incrementing.
                db.rollback()
                row = (
                    db.query(NotificationFailure)
                    .filter_by(tracking_number=tracking_number, delay_reason=reason)
                    .first()
                )
                if row is None:
                    return {"recorded": False, "reason": "race_lost"}
                row.attempts += 1
                row.last_error = error
        else:
            row.attempts += 1
            row.last_error = error

        row.last_failed_at = func.now()
        if row.attempts >= MAX_NOTIFY_ATTEMPTS:
            row.status = STATUS_DEAD

        attempts, status = row.attempts, row.status
        db.commit()
        return {"recorded": True, "attempts": attempts, "status": status,
                "dead": status == STATUS_DEAD}
    except Exception:
        logger.exception("Could not record notification failure for %s", tracking_number)
        db.rollback()
        return {"recorded": False, "reason": "record_failed"}
    finally:
        db.close()


def clear_failure(tracking_number: str, delay_reason: str | None) -> bool:
    """Drop the failure record once the notification finally goes out. Returns whether
    there was one — the caller logs a recovery, which is worth seeing."""
    db = SessionLocal()
    try:
        deleted = (
            db.query(NotificationFailure)
            .filter_by(tracking_number=tracking_number, delay_reason=_key(delay_reason))
            .delete(synchronize_session=False)
        )
        db.commit()
        return bool(deleted)
    finally:
        db.close()


def dead_lettered_keys() -> set[tuple[str, str]]:
    """Every `(tracking_number, delay_reason)` the scan should stop attempting.

    Fetched once per scan rather than queried per parcel: the scan already iterates
    every overdue parcel, and this set is small (it only ever holds give-ups).
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(NotificationFailure.tracking_number, NotificationFailure.delay_reason)
            .filter(NotificationFailure.status == STATUS_DEAD)
            .all()
        )
        return {(tn, reason) for tn, reason in rows}
    finally:
        db.close()


def list_failures(status: str | None = None, limit: int = 100) -> list[dict]:
    """Newest-first, for the dashboard's case feed."""
    db = SessionLocal()
    try:
        query = db.query(NotificationFailure)
        if status:
            query = query.filter(NotificationFailure.status == status)
        rows = query.order_by(NotificationFailure.last_failed_at.desc()).limit(limit).all()
        return [
            {
                "id": row.id,
                "tracking_number": row.tracking_number,
                "delay_reason": row.delay_reason,
                "attempts": row.attempts,
                "status": row.status,
                "last_error": row.last_error,
                "first_failed_at": row.first_failed_at,
                "last_failed_at": row.last_failed_at,
            }
            for row in rows
        ]
    finally:
        db.close()
