from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class Handoff(Base):
    """A conversation handed from the bot to a human (Phase 5).

    Scoped to a **customer phone number**, not a parcel. That's the whole point: once a
    human takes a thread the bot must go quiet for that customer across every parcel and
    intent, so a column on `Ticket` (parcel-scoped, and NOT NULL on tracking_number)
    would be the wrong grain. `tracking_number` here is nullable precisely because the
    most common trigger — "let me talk to a human" — carries no parcel at all.

    Durable rather than a Redis key, for the same reasons as `PendingAction`: it's a
    business-affecting state change and therefore auditable (`docs/PROJECT_PLAN.md`
    §5.2), it must survive a restart, and the dashboard queries it from Postgres.

    Lifecycle: open -> claimed -> resolved, with `expired` as a safety valve. Every
    transition stamps who did it and when; nothing flips silently. Only a **claimed**
    handoff suppresses the bot — an `open` one is waiting for a human to pick it up,
    and leaving the customer with no reply at all in the meantime would be worse than
    the bot continuing to help.
    """

    __tablename__ = "handoffs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_phone = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=True)
    reason = Column(String, nullable=False)       # escalation_reason, or the decision that triggered it
    ticket_id = Column(String, nullable=True)     # linked Ticket, when the escalation had a parcel
    status = Column(String, nullable=False, default="open")  # open | claimed | resolved | expired

    # Who did what, when. `*_by` is free-text staff identity — there is no user table
    # yet (accounts are Phase 6), so the write endpoints require an explicit actor
    # rather than defaulting to something anonymous.
    claimed_by = Column(String, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(String, nullable=True)

    # Whether the staff notification for this handoff actually went out — delivery is
    # itself auditable, not assumed.
    notified_at = Column(DateTime(timezone=True), nullable=True)
    notify_failed = Column(Boolean, nullable=False, default=False)

    # Safety valve: a human who claims a thread and walks away would otherwise silence
    # the bot for that customer forever. Lazily swept on read, like PendingAction.
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_handoffs_phone_status", "customer_phone", "status"),
    )
