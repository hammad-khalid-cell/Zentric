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

    # Escalation
    needs_human_handoff: bool