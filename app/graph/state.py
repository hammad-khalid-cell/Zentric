from typing import TypedDict, Optional, Literal


class AgentState(TypedDict):
    # Input
    user_message: str
    customer_id: str


    # Set by Intent Understanding Agent
    intent: Optional[Literal[
        "track_order", "delay_complaint", "faq", "unclear"
    ]]
    tracking_number: Optional[str]

    # Set by Data Retrieval Agent
    retrieved_data: Optional[dict]

    # Set by Decision Making Agent
    decision: Optional[str]
    decision_reason: Optional[str]

    # Set by Action Execution Agent
    action_taken: Optional[str]
    action_result: Optional[dict]

    # Set by Response Generation Agent
    final_response: Optional[str]

       # NEW — set when we can't resolve a parcel and need to ask the customer
    clarification_needed: Optional[str]

    # Escalation
    needs_human_handoff: bool


    # NEW — memory/context
    pending_clarification: Optional[dict]   # what we're waiting on the customer to answer
    session_loaded: Optional[bool]

    escalation_reason: Optional[str]

    # NEW — proactive loop (Phase 2). When a proactive delay message was sent, a
    # parcel-scoped pending action is stored; the customer's reply is interpreted
    # into a structured corrective intent that a DETERMINISTIC policy then acts on.
    # The LLM only interprets free text into `corrective_intent`/`corrective_payload`;
    # it never chooses the business action (that's CORRECTIVE_INTENT_TO_ACTION).
    pending_action: Optional[dict]          # open intervention loaded from the pending-action store
    corrective_intent: Optional[Literal[
        "reschedule", "update_address", "available_window", "cancel", "unclear"
    ]]
    corrective_payload: Optional[dict]      # extracted slots: {"address": ..., "window": ...}