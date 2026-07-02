from fastapi import APIRouter
from pydantic import BaseModel
from app.agents.router_agent import classify_message
from app.agents.tracking_agent import run_tracking_agent

router = APIRouter()


class MessageRequest(BaseModel):
    from_number: str   # simulates WhatsApp's "from" field
    message: str


@router.post("/test/message")
def test_message(payload: MessageRequest):
    category = classify_message(payload.message)

    if category == "tracking_query":
        reply = run_tracking_agent(payload.message)
    else:
        reply = f"[{category} handling not built yet]"

    return {
        "message": payload.message,
        "classified_as": category,
        "reply": reply,
    }