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
    same seam a real delivery-system webhook would call into later (Phase 7).

    **Provenance is recorded, because these rows move the headline number** (Phase 6).
    "RTO prevented" is computed from delivery outcomes, and some of those outcomes are
    triggered by a human clicking a button in the ops console rather than observed from
    a courier. That distinction is exactly the modelled-vs-observed discipline
    `docs/PROJECT_PLAN.md` §3 demands of `RTO_COST_PKR`, so it is a column rather than a
    convention: `source` says what reported the outcome, and
    `delivery_service.MODELLED_SOURCES` is the set that must never be presented as
    observed. `recorded_by` attributes the ones a person triggered.
    """

    __tablename__ = "delivery_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_number = Column(String, nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    outcome = Column(String, nullable=False)   # 'failed' | 'success'
    failure_reason = Column(String, nullable=True)
    # See delivery_service.SOURCE_* — nullable so pre-Phase-6 rows read as untagged
    # rather than being retroactively claimed as observed.
    source = Column(String, nullable=True, index=True)
    recorded_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tracking_number", "attempt_no", name="uq_delivery_attempt_tracking_no"),
    )
