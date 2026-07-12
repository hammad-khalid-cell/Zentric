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