"""Inbound WhatsApp webhook. Accepts the real Meta WhatsApp Cloud API payload
shape so that swapping the mock channel for the real API (Phase 7) needs no
change here — Meta just starts POSTing to this same endpoint."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.services.whatsapp_inbound import process_inbound_message

router = APIRouter()


def extract_text_messages(payload: dict) -> list[tuple[str, str]]:
    """Pull (from_number, text) pairs out of a Meta webhook payload. Non-text
    messages and malformed entries are skipped rather than raising."""
    pairs: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            for msg in value.get("messages", []) or []:
                if msg.get("type") != "text":
                    continue
                from_number = msg.get("from")
                body = (msg.get("text") or {}).get("body")
                if from_number and body:
                    pairs.append((from_number, body))
    return pairs


def build_text_message_payload(from_number: str, text: str) -> dict:
    """Construct a minimal Meta-shaped inbound text payload — used by the customer
    simulator and tests so they exercise the exact same parsing path as production."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "messages": [{
                        "from": from_number,
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


@router.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    """Meta webhook verification handshake — echoes hub.challenge. Real verify-token
    checking is deferred to Phase 7 when the live API is wired up."""
    if hub_challenge is not None:
        return PlainTextResponse(hub_challenge)
    return {"status": "ok"}


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    payload = await request.json()
    replies = []
    for from_number, text in extract_text_messages(payload):
        reply = process_inbound_message(from_number, text)
        replies.append({"to": from_number, "reply": reply})
    return {"processed": len(replies), "replies": replies}
