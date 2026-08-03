"""Staff notification channel (Phase 5) — how a human finds out a customer needs them.

This is a **separate port from `send_whatsapp_message()`**, deliberately. That seam is
the *customer* channel: its mock persists to `messages` keyed by `customer_phone`, so
routing staff alerts through it would inject internal notices into customer conversation
threads and the dashboard's rendering of them — and in Phase 7 it would spend the real
Meta quota that `docs/PROJECT_PLAN.md` §5.4 exists to protect. Same abstraction shape,
same one-env-var swap, different destination.

- `LogStaffChannel` (default) — structured log line, no network, no quota. Enough for
  development and the live defense: the dashboard's handoff queue is the real console,
  and this is the out-of-band nudge toward it.
- `SlackStaffChannel` / `EmailStaffChannel` — Phase 6, one class each. The seam is what
  matters now; swapping in a webhook later is a config change, not a rewrite.

Selection via `STAFF_NOTIFY_PROVIDER=log|slack|email` (default `log`).

`notify_staff()` never raises. A notification that fails must not roll back the handoff
it belongs to — the durable `Handoff` row is the source of truth and the queue still
shows the case; the failure is recorded on the row (`notify_failed`) so an un-notified
handoff is visible rather than silently lost.
"""
import logging
from abc import ABC, abstractmethod

from app.core import config

logger = logging.getLogger(__name__)


class StaffChannel(ABC):
    @abstractmethod
    def send(self, subject: str, body: str, context: dict | None = None) -> None:
        ...


class LogStaffChannel(StaffChannel):
    """Writes the alert to the application log. The handoff queue on the dashboard is
    the actual work surface; this is the nudge that points at it."""

    def send(self, subject: str, body: str, context: dict | None = None) -> None:
        logger.warning("STAFF ALERT — %s | %s | %s", subject, body, context or {})
        print(f"[staff alert] {subject}: {body}")


class SlackStaffChannel(StaffChannel):
    """Phase 6 — an incoming-webhook POST. Kept here so the env value has a target."""

    def send(self, subject: str, body: str, context: dict | None = None) -> None:
        raise NotImplementedError(
            "SlackStaffChannel is not implemented yet (Phase 6). "
            "Set STAFF_NOTIFY_PROVIDER=log until it is."
        )


class EmailStaffChannel(StaffChannel):
    """Phase 6 — SMTP/provider send. Kept here so the env value has a target."""

    def send(self, subject: str, body: str, context: dict | None = None) -> None:
        raise NotImplementedError(
            "EmailStaffChannel is not implemented yet (Phase 6). "
            "Set STAFF_NOTIFY_PROVIDER=log until it is."
        )


def _build_channel() -> StaffChannel:
    if config.STAFF_NOTIFY_PROVIDER == "slack":
        return SlackStaffChannel()
    if config.STAFF_NOTIFY_PROVIDER == "email":
        return EmailStaffChannel()
    return LogStaffChannel()


# Chosen once at import; the provider is a deployment setting, not a per-call choice —
# same pattern as the WhatsApp channel.
_channel = _build_channel()


REASON_SUMMARIES = {
    "explicit_human_request": "customer asked to speak to a human",
    "repeated_query": "customer repeated the same question three times",
    "angry_language": "customer used angry language",
    "tone_detected": "customer tone flagged as frustrated",
    "escalate": "delay reason requires a human decision",
}


def notify_staff(handoff: dict) -> bool:
    """Alert staff that a conversation needs a human. Returns whether the alert was
    delivered; never raises, so a channel outage can't roll back the handoff itself."""
    reason = handoff.get("reason") or "unknown"
    phone = handoff.get("customer_phone")
    tracking_number = handoff.get("tracking_number")

    subject = f"Handoff #{handoff.get('id')} — {phone}"
    body = (
        f"{REASON_SUMMARIES.get(reason, reason)}."
        + (f" Parcel {tracking_number}." if tracking_number else "")
        + " Open the ops dashboard to take the thread."
    )

    try:
        _channel.send(subject, body, {
            "handoff_id": handoff.get("id"),
            "customer_phone": phone,
            "tracking_number": tracking_number,
            "reason": reason,
            "ticket_id": handoff.get("ticket_id"),
        })
        return True
    except Exception:
        logger.exception("Staff notification failed for handoff %s", handoff.get("id"))
        return False
