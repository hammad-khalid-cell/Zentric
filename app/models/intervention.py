from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class Intervention(Base):
    """Audit row for a corrective action taken on a parcel through the proactive loop
    (e.g. the customer replied with a fixed address or a new delivery window and we
    acted on it). Every business-affecting change is auditable — this is the record for
    reschedule / update_address, the sibling of Ticket (escalate) and Reroute (reroute).

    Phase 3's InterventionOutcome links one of these rows to the parcel's subsequent
    delivery outcome, which is how we compute "RTO prevented".
    """

    __tablename__ = "interventions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intervention_id = Column(String, unique=True, nullable=False)
    tracking_number = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)   # 'update_address' | 'reschedule'
    detail = Column(String, nullable=True)     # human-readable summary of what changed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
