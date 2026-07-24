import logging

logger = logging.getLogger(__name__)


def send_whatsapp_message(phone_number: str, message: str) -> None:
    """Stand-in for the WhatsApp Business API send call — swap the body of this
    function out once that integration exists. Every other module should keep
    calling send_whatsapp_message() rather than talking to WhatsApp directly."""
    logger.info("WHATSAPP -> %s: %s", phone_number, message)
    print(f"[WhatsApp stub] to {phone_number}: {message}")
