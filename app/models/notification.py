from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("tracking_number", "delay_reason", name="uq_notification_tracking_delay_reason"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_number = Column(String, nullable=False)
    delay_reason = Column(String, nullable=True)
    decision = Column(String, nullable=False)
    message = Column(String, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
