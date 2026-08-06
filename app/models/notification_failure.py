from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class NotificationFailure(Base):
    """A proactive notification that didn't go out, and how many times we've tried.

    **What this replaces.** `scan_and_notify` wrapped each parcel in
    `except Exception: logger.exception(...); continue`. That kept one bad parcel from
    killing the whole scan — correct — but the failure then existed only in a log nobody
    reads, so a customer who was never told about their delay was indistinguishable from
    one who was. For a system whose pitch is "we intervene before the delivery fails",
    silently not intervening is the worst possible failure mode.

    **Retries are inherent, not scheduled.** A parcel that fails before
    `record_notification` stays overdue and un-notified, so the next scan picks it up
    again by itself. What was missing is *counting* those attempts, *giving up* after
    `MAX_NOTIFY_ATTEMPTS` so one poisoned parcel isn't retried forever, and making both
    visible. That's all this table does.

    A row here is not itself an error: `status='retrying'` is a transient blip, usually
    a DNS wobble on the Groq or Postgres call. `status='dead'` is the one an ops lead has
    to see, and it surfaces in the dashboard's case feed.
    """

    __tablename__ = "notification_failures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_number = Column(String, nullable=False, index=True)
    # Mirrors the notifications dedupe key. Never NULL — Postgres treats NULLs as
    # distinct in a unique index, so a nullable column here would quietly allow
    # duplicate rows for the one case (an unknown reason) most likely to recur.
    delay_reason = Column(String, nullable=False, default="unknown")
    attempts = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="retrying", index=True)  # retrying | dead
    last_error = Column(Text, nullable=True)
    first_failed_at = Column(DateTime(timezone=True), server_default=func.now())
    last_failed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tracking_number", "delay_reason",
                         name="uq_notification_failure_tracking_reason"),
    )
