from app.core.database import SessionLocal
from app.core.language_detect import detect_language
from app.models.interaction import Interaction


def record_interaction(state: dict, elapsed_ms: int) -> None:
    """Log one row per finished graph run for the Phase 3 metrics service. Called
    right after `compiled_graph.invoke(...)` returns — not a graph node, since
    response time and resolution status are only known once the run has finished.
    `state` is the final AgentState dict; reads only fields every path sets
    (user_message, customer_id) or safely defaults (intent/decision/tracking_number/
    needs_human_handoff)."""
    escalated = bool(state.get("needs_human_handoff"))
    db = SessionLocal()
    try:
        db.add(Interaction(
            customer_phone=state["customer_id"],
            tracking_number=state.get("tracking_number"),
            intent=state.get("intent"),
            decision=state.get("decision"),
            resolved_by="human" if escalated else "bot",
            escalated=escalated,
            language=detect_language(state["user_message"]),
            response_time_ms=elapsed_ms,
        ))
        db.commit()
    finally:
        db.close()
