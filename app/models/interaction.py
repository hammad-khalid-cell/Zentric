from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from sqlalchemy.sql import func
from app.core.database import Base


class Interaction(Base):
    """One row per agent-graph run, capturing the analytics-relevant outcome (not the
    conversation body — that's `Message`). This is what the Phase 3 metrics service
    reads: deflection rate, cost-per-interaction, response time, after-hours %, and
    language reach % all derive from these rows. Written by
    app/services/interaction_log.py::record_interaction(), called right after
    compiled_graph.invoke() from the two route/service call sites — not a graph node,
    since timing and resolution status are only known once the graph run has finished."""

    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_phone = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    decision = Column(String, nullable=True)
    resolved_by = Column(String, nullable=False)   # 'bot' | 'human'
    escalated = Column(Boolean, nullable=False, default=False)
    language = Column(String, nullable=False)      # 'english' | 'roman_urdu'
    response_time_ms = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_interactions_created_at", "created_at"),
    )
