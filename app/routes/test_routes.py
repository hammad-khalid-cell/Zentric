import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.memory_store import check_rate_limit
from app.graph.build_graph import compiled_graph

router = APIRouter()

PHONE_PATTERN = re.compile(r"\d{7,15}")


class MessageRequest(BaseModel):
    from_number: str = Field(..., min_length=7, max_length=20)
    message: str = Field(..., min_length=1, max_length=1000)

    @field_validator("from_number")
    @classmethod
    def validate_from_number(cls, v: str) -> str:
        v = v.strip()
        if not PHONE_PATTERN.fullmatch(v):
            raise ValueError("from_number must be digits only (e.g. 923001234567)")
        return v

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty or whitespace-only")
        return v


@router.post("/test/message")
def test_message(payload: MessageRequest):
    if not check_rate_limit(payload.from_number):
        raise HTTPException(
            status_code=429,
            detail="Too many messages — please wait a moment and try again.",
        )

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
