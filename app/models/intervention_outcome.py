from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class InterventionOutcome(Base):
    """Links an Intervention (a corrective action we took) to the parcel's subsequent
    delivery outcome — the literal 'did this prevent an RTO' record. Written by
    app/services/delivery_service.py::record_attempt_outcome() when the next
    DeliveryAttempt after an unresolved intervention comes in."""

    __tablename__ = "intervention_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intervention_id = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=False, index=True)
    delivery_attempt_id = Column(Integer, nullable=False)
    outcome = Column(String, nullable=False)   # 'delivered' | 'still_failed'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
