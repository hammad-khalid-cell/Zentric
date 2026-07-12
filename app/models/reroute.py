from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class Reroute(Base):
    __tablename__ = "reroutes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reroute_id = Column(String, unique=True, nullable=False)
    tracking_number = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, default="requested")
    created_at = Column(DateTime(timezone=True), server_default=func.now())