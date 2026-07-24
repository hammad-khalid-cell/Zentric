import logging
from datetime import date

from app.core.groq_client import safe_chat_completion
from app.core.whatsapp_client import send_whatsapp_message
from app.graph.decision_rules import REASON_TO_DECISION
from app.services.action_service import (
    create_reroute_request,
    create_ticket,
    find_existing_notification,
    record_notification,
)
from app.services.parcel_data import find_delayed_parcels

logger = logging.getLogger(__name__)


def _generate_notification_message(parcel: dict, decision: str, days_overdue: int) -> str:
    system_prompt = (
        "You are a professional WhatsApp customer support assistant for a "
        "Pakistani courier company. Write a short, proactive outbound message "
        "(1-3 sentences, no markdown, WhatsApp-appropriate) telling the customer "
        "their parcel is delayed and what is being done about it. Be warm, "
        "professional, and reassuring. Write in plain English — there is no "
        "prior customer message to mirror style from."
    )
    user_prompt = (
        f"Tracking number: {parcel['tracking_number']}\n"
        f"Delay reason: {parcel['delay_reason'] or 'unknown'}\n"
        f"Current hub: {parcel['current_hub']}\n"
        f"Days overdue: {days_overdue}\n"
        f"Action being taken: {decision}"
    )
    fallback = (
        f"Your parcel {parcel['tracking_number']} is delayed "
        f"({parcel['delay_reason'] or 'an operational issue'}). We're on it — "
        f"{decision} is in progress and we'll keep you updated."
    )
    return safe_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        fallback=fallback,
    ) or fallback


def scan_and_notify() -> int:
    """Scans for delayed parcels and proactively messages the customer once per
    tracking_number + delay_reason pair. Meant to be run periodically (cron/manual);
    re-running it is safe — already-notified parcels are skipped via the
    notifications table, and ticket/reroute creation is separately deduplicated."""
    sent_count = 0

    for parcel in find_delayed_parcels():
        tracking_number = parcel["tracking_number"]
        try:
            reason_code = parcel["delay_reason"]
            decision = REASON_TO_DECISION.get(reason_code, "escalate")

            if find_existing_notification(tracking_number, reason_code):
                continue

            if decision == "escalate":
                create_ticket(tracking_number=tracking_number, reason=reason_code, decision=decision)
            elif decision == "reroute":
                create_reroute_request(tracking_number=tracking_number, reason=reason_code)

            days_overdue = (date.today() - parcel["expected_delivery_date"]).days
            message = _generate_notification_message(parcel, decision, days_overdue)

            send_whatsapp_message(parcel["customer_phone"], message)
            if record_notification(tracking_number, reason_code, decision, message):
                sent_count += 1
        except Exception:
            logger.exception("Proactive notification failed for parcel %s — skipping", tracking_number)
            continue

    return sent_count


if __name__ == "__main__":
    count = scan_and_notify()
    print(f"Sent {count} proactive delay notifications.")
