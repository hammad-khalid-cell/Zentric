import logging
from datetime import date

from app.core.groq_client import safe_chat_completion
from app.core.notification_jobs import clear_failure, dead_lettered_keys, record_failure
from app.core.pending_actions import create_pending_action
from app.core.whatsapp_client import send_whatsapp_message
from app.graph.decision_rules import REASON_TO_DECISION
from app.services.action_service import (
    create_reroute_request,
    create_ticket,
    find_existing_notification,
    record_notification,
)
from app.services.delivery_service import SOURCE_SCANNER, record_attempt_outcome
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


def scan_and_notify(max_sends: int | None = None) -> dict:
    """Scans for delayed parcels and proactively messages the customer once per
    tracking_number + delay_reason pair. Meant to be run periodically (the Phase 6
    worker, or by hand); re-running it is safe — already-notified parcels are skipped
    via the notifications table, and ticket/reroute creation is separately deduplicated.

    `max_sends` caps how many notifications one run may send. It exists for Phase 7:
    once `WHATSAPP_PROVIDER=cloud`, every send is real Meta quota, and a scheduled job
    that can send unboundedly is a way to wake up having spent all of it. `None` means
    uncapped, which is the right default for the mock channel.

    Returns counts rather than a bare int — a run that sent nothing because everything
    dead-lettered is a very different event from one that sent nothing because there was
    nothing to do, and the worker log has to be able to tell them apart.
    """
    sent_count = 0
    failed_count = 0
    dead_lettered_count = 0
    skipped_dead = 0
    capped = False

    # One query, not one per parcel. Only ever holds give-ups, so it stays small.
    dead_keys = dead_lettered_keys()

    for parcel in find_delayed_parcels():
        tracking_number = parcel["tracking_number"]
        try:
            reason_code = parcel["delay_reason"]
            decision = REASON_TO_DECISION.get(reason_code, "escalate")

            if find_existing_notification(tracking_number, reason_code):
                continue

            # Already given up on. Skipped before any LLM call — the whole point of
            # dead-lettering is that a poisoned parcel stops costing something per run.
            if (tracking_number, reason_code or "unknown") in dead_keys:
                skipped_dead += 1
                continue

            if max_sends is not None and sent_count >= max_sends:
                capped = True
                break

            # Phase 3 — this scan is what discovers the failed attempt in the first
            # place; record it (deduplicated per attempt_no via the unique constraint).
            record_attempt_outcome(tracking_number, "failed", reason_code, source=SOURCE_SCANNER)

            if decision == "escalate":
                create_ticket(tracking_number=tracking_number, reason=reason_code, decision=decision)
            elif decision == "reroute":
                create_reroute_request(tracking_number=tracking_number, reason=reason_code)

            days_overdue = (date.today() - parcel["expected_delivery_date"]).days
            message = _generate_notification_message(parcel, decision, days_overdue)

            # Pass the tracking number through: it's what ties the outbound row in
            # `messages` to a parcel, so the dashboard thread can say which parcel a
            # proactive message is about. Without it these rows land with a null
            # tracking_number and the conversation view shows an unattributed message.
            send_whatsapp_message(parcel["customer_phone"], message, tracking_number)

            # Only "notify" reasons are ones the CUSTOMER can resolve (unavailable,
            # wrong address, reschedule request). Open a pending action for those so
            # their reply is routed into the corrective loop and can prevent an RTO.
            # reroute/escalate are operational — a customer reply can't fix them.
            if decision == "notify":
                create_pending_action(
                    tracking_number=tracking_number,
                    customer_phone=parcel["customer_phone"],
                    trigger_reason=reason_code,
                )

            if record_notification(tracking_number, reason_code, decision, message):
                sent_count += 1

            # It went out — drop any retry record. Worth logging: a parcel that failed
            # twice and then succeeded is a transient blip, and knowing that is what
            # stops someone chasing a dead-letter that fixed itself.
            if clear_failure(tracking_number, reason_code):
                logger.info("Proactive notification for %s recovered after earlier failures",
                            tracking_number)

        except Exception as error:
            # Still swallowed per-parcel so one bad parcel can't kill the scan — but no
            # longer *only* into a log. The failure is counted, and once it has used up
            # its retries it is dead-lettered and shows up in the dashboard's case feed.
            # A customer who was never warned about their delay is the single most
            # important thing for this system to be able to admit to.
            failed_count += 1
            logger.exception("Proactive notification failed for parcel %s", tracking_number)
            outcome = record_failure(tracking_number, parcel.get("delay_reason"), repr(error))
            if outcome.get("dead"):
                dead_lettered_count += 1
                logger.error(
                    "Proactive notification for %s dead-lettered after %s attempts: %r",
                    tracking_number, outcome.get("attempts"), error,
                )
            continue

    return {
        "sent": sent_count,
        "failed": failed_count,
        "dead_lettered": dead_lettered_count,
        "skipped_dead": skipped_dead,
        "capped": capped,
    }


if __name__ == "__main__":
    result = scan_and_notify()
    print(f"Sent {result['sent']} proactive delay notifications "
          f"({result['failed']} failed, {result['dead_lettered']} dead-lettered, "
          f"{result['skipped_dead']} skipped as already dead-lettered).")
