from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.sql import func
from app.core.database import Base


class PendingAction(Base):
    """A parcel-scoped intervention we're waiting on the customer to respond to.

    Written when a proactive delay message is sent (see proactive_notifier), read
    when that customer's next message arrives so the reply is routed into the
    corrective path instead of being classified from scratch. Deliberately a durable
    DB row rather than a Redis session key: it's an auditable business record, it must
    outlive the 30-minute conversational session, and Phase 3 / the dashboard query
    "open interventions" from here.

    status: 'open' -> awaiting reply · 'resolved' -> a corrective action was applied ·
    'expired' -> the reply window (expires_at) passed with no actionable reply.
    """

    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_number = Column(String, nullable=False, index=True)
    customer_phone = Column(String, nullable=False, index=True)
    trigger_reason = Column(String, nullable=True)   # the delay_reason that prompted the outreach
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Fast "is there an open pending action for this customer?" lookup.
    __table_args__ = (
        Index("ix_pending_actions_phone_status", "customer_phone", "status"),
    )
