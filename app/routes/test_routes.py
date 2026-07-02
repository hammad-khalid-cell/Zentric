from fastapi import APIRouter
from pydantic import BaseModel
from app.agents.router_agent import classify_message

router = APIRouter()


class MessageRequest(BaseModel):
    message: str


@router.post("/test/message")
def test_message(payload: MessageRequest):
    category = classify_message(payload.message)
    return {"message": payload.message, "classified_as": category}