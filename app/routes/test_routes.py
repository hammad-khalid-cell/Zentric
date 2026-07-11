from fastapi import APIRouter
from pydantic import BaseModel
from app.graph.build_graph import compiled_graph

router = APIRouter()


class MessageRequest(BaseModel):
    from_number: str
    message: str


@router.post("/test/message")
def test_message(payload: MessageRequest):
    initial_state = {
        "user_message": payload.message,
        "customer_id": payload.from_number,
    }

    result = compiled_graph.invoke(initial_state)

    return {
        "message": payload.message,
        "intent": result.get("intent"),
        "clarification_needed": result.get("clarification_needed"),
        "decision": result.get("decision"),
        "action_result": result.get("action_result"),
        "needs_human_handoff": result.get("needs_human_handoff", False),
        "reply": result.get("final_response"),
    }