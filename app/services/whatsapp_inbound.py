"""Core inbound-message handling, independent of HTTP/payload shape so it can be
driven by the webhook route, the customer simulator, or tests alike. Logs the
inbound message, runs the agent graph, and sends the reply back through the
WhatsApp channel (which logs the outbound side)."""
import logging

from app.core.memory_store import check_rate_limit
from app.core.whatsapp_client import send_whatsapp_message
from app.graph.build_graph import compiled_graph
from app.services.message_log import DIRECTION_IN, log_message

logger = logging.getLogger(__name__)

RATE_LIMIT_REPLY = "Too many messages — please wait a moment and try again."


def process_inbound_message(from_number: str, text: str) -> str | None:
    """Handle one inbound customer message end to end. Returns the reply text that
    was sent (also delivered via the channel), or None if nothing was sent."""
    log_message(from_number, DIRECTION_IN, text)

    if not check_rate_limit(from_number):
        send_whatsapp_message(from_number, RATE_LIMIT_REPLY)
        return RATE_LIMIT_REPLY

    try:
        result = compiled_graph.invoke({"user_message": text, "customer_id": from_number})
    except Exception:
        logger.exception("Graph run failed for %s", from_number)
        reply = (
            "Sorry, I'm having trouble processing your message right now — "
            "a team member will follow up with you shortly."
        )
        send_whatsapp_message(from_number, reply)
        return reply

    reply = result.get("final_response")
    if reply:
        send_whatsapp_message(from_number, reply, result.get("tracking_number"))
    return reply
