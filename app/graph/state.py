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

    # NEW — human handoff (Phase 5). Conversation-scoped, NOT parcel-scoped: once a
    # human owns a customer's thread the bot must go quiet for that customer across
    # every parcel and intent, so this is keyed by phone number like the session, not
    # by tracking number like `pending_action`.
    #
    # Loaded by memory_load_node from the handoffs store. When it holds a CLAIMED
    # handoff, route_after_memory_load short-circuits the whole graph into
    # handoff_hold — before intent classification, so no LLM call is made and no
    # auto-reply is generated. `handoff_suppressed` records that that happened, which
    # is what distinguishes "the bot chose to say nothing" from "the bot failed".
    human_handoff: Optional[dict]           # open/claimed handoff row for this customer
    handoff_suppressed: Optional[bool]      # True when a human owned the thread and the bot stayed silent