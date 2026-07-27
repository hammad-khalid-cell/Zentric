from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.sql import func
from app.core.database import Base


class Message(Base):
    """Every inbound/outbound WhatsApp message, persisted so the conversation
    thread can be replayed (dashboard) and outbound proactive messages are
    captured even while the WhatsApp channel is mocked. direction is 'in'
    (from customer) or 'out' (from us)."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_phone = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)  # 'in' | 'out'
    body = Column(String, nullable=False)
    tracking_number = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Fast "conversation for this customer, in order" lookups.
    __table_args__ = (
        Index("ix_messages_phone_created", "customer_phone", "created_at"),
    )
