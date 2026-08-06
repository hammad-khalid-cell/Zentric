"""Inbound WhatsApp webhook. Accepts the real Meta WhatsApp Cloud API payload
shape so that swapping the mock channel for the real API (Phase 7) needs no
change here — Meta just starts POSTing to this same endpoint."""
import secrets

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.core import config
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
    """Meta's webhook verification handshake — echoes `hub.challenge` back.

    Checks `hub.verify_token` **when `WHATSAPP_VERIFY_TOKEN` is configured**, and
    otherwise behaves exactly as it did before (echo to anyone). That opt-in default is
    the point: this half of webhook auth needs nothing from Meta, so it can be built and
    tested now, while requiring it by default would break the local simulator and every
    existing test for no benefit while the provider is `mock` — the endpoint returns a
    string the caller already supplied and touches no state. Set the env var in Phase 7,
    when the URL is public.

    The **signature** check on inbound POSTs (`X-Hub-Signature-256`) is deliberately not
    here: it needs the App Secret, and writing HMAC now with no genuine signature to
    verify against would only test the implementation against itself. That is Phase 7.
    """
    expected = config.WHATSAPP_VERIFY_TOKEN

    if expected:
        # Compared in constant time for the same reason as the dashboard tokens
        # (app/core/auth.py) — a public endpoint that leaks a token through response
        # timing is worth avoiding even when the token is low-value.
        supplied = hub_verify_token or ""
        if not secrets.compare_digest(supplied, expected):
            # 403 is what Meta's own documented flow expects for a bad token, and it is
            # the honest code: the request was understood and refused, not malformed.
            raise HTTPException(status_code=403, detail="Verification token mismatch.")
        # Meta always sends mode=subscribe; anything else isn't the handshake.
        if hub_mode is not None and hub_mode != "subscribe":
            raise HTTPException(status_code=403, detail="Unexpected hub.mode.")

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
