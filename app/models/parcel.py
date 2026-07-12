from sqlalchemy import Column, String, Date
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