from sqlalchemy import Column, Integer, String, Date
from app.core.database import Base


class Parcel(Base):
    __tablename__ = "parcels"

    tracking_number = Column(String, primary_key=True)
    customer_phone = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    current_hub = Column(String, nullable=False)
    destination_city = Column(String, nullable=False)
    dispatch_date = Column(Date, nullable=False)
    expected_delivery_date = Column(Date, nullable=False)
    delay_reason = Column(String, nullable=True)

    # Phase 2 — the proactive loop needs an *updatable* delivery address and a way
    # to record that an attempt was rescheduled. These are what corrective actions
    # (update_address / reschedule) write back to, converting a would-be RTO into a
    # completed delivery.
    address_line = Column(String, nullable=True)             # full street address, updatable
    preferred_delivery_window = Column(String, nullable=True)  # e.g. "tomorrow evening"
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")