"""WhatsApp channel abstraction.

The rest of the app only ever calls `send_whatsapp_message()` — it never talks to
a WhatsApp API directly. Which concrete channel that dispatches to is chosen by the
`WHATSAPP_PROVIDER` env var (default `mock`), so swapping the mock for the real Meta
Cloud API at the end of the project is a one-line config change, not a rewrite.

- MockWhatsAppChannel  -> logs to console + persists the outbound message (no network,
                          no quota). Used for all development and the live defense demo
                          until the real API is wired up last.
- CloudApiWhatsAppChannel -> real Meta WhatsApp Cloud API (Phase 7 — not yet implemented).
"""
import logging
from abc import ABC, abstractmethod

from app.core.config import WHATSAPP_PROVIDER
from app.services.message_log import DIRECTION_OUT, log_message

logger = logging.getLogger(__name__)


class WhatsAppChannel(ABC):
    @abstractmethod
    def send(self, phone_number: str, message: str, tracking_number: str | None = None) -> None:
        ...


class MockWhatsAppChannel(WhatsAppChannel):
    """Stand-in for the WhatsApp Business API. Records the outbound message the same
    way the real channel will, so history/dashboards work identically once swapped."""

    def send(self, phone_number: str, message: str, tracking_number: str | None = None) -> None:
        logger.info("WHATSAPP(mock) -> %s: %s", phone_number, message)
        print(f"[WhatsApp mock] to {phone_number}: {message}")
        log_message(phone_number, DIRECTION_OUT, message, tracking_number)


class CloudApiWhatsAppChannel(WhatsAppChannel):
    """Real Meta WhatsApp Cloud API — implemented last (Phase 7) to preserve free
    quota for the defense. Kept here so `WHATSAPP_PROVIDER=cloud` has a target."""

    def send(self, phone_number: str, message: str, tracking_number: str | None = None) -> None:
        raise NotImplementedError(
            "CloudApiWhatsAppChannel is not implemented yet (Phase 7). "
            "Set WHATSAPP_PROVIDER=mock until the real API is wired up."
        )


def _build_channel() -> WhatsAppChannel:
    if WHATSAPP_PROVIDER == "cloud":
        return CloudApiWhatsAppChannel()
    return MockWhatsAppChannel()


# Chosen once at import; provider is a deployment setting, not a per-request choice.
_channel = _build_channel()


def send_whatsapp_message(phone_number: str, message: str,
                          tracking_number: str | None = None) -> None:
    """Public seam every module uses to reach the customer. Dispatches to the
    configured channel and records the outbound message."""
    _channel.send(phone_number, message, tracking_number)
