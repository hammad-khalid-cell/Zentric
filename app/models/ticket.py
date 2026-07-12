from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(String, unique=True, nullable=False)
    tracking_number = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    decision = Column(String, nullable=True)
    status = Column(String, default="open")
    created_at = Column(DateTime(timezone=True), server_default=func.now())