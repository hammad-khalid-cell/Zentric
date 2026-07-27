from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class DeliveryAttempt(Base):
    """One row per delivery attempt outcome for a parcel — the raw material for RTO
    metrics. The first attempt (the one that made the parcel 'delayed') is recorded
    organically from the graph/proactive loop when it detects an overdue parcel with a
    delay_reason. Subsequent attempts (did a corrective action actually lead to a
    delivered parcel?) have no real courier feedback in this system yet, so they're
    resolved by the Phase 3 demo-data simulator (app/tools/simulate_outcomes.py) — the
    same seam a real delivery-system webhook would call into later (Phase 6).
    """

    __tablename__ = "delivery_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_number = Column(String, nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    outcome = Column(String, nullable=False)   # 'failed' | 'success'
    failure_reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tracking_number", "attempt_no", name="uq_delivery_attempt_tracking_no"),
    )
