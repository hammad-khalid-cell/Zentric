"""Proactive notifier: the outbound half of the RTO loop.

Everything external (parcel lookup, the LLM that phrases the message, the WhatsApp
seam, and every DB write) is monkeypatched at the boundary — what's asserted here is
the deterministic routing: which decision a delay reason produces, whether a pending
action is opened for it, and what gets handed to `send_whatsapp_message()`.
"""
import pytest

from app.services import proactive_notifier
from tests.conftest import make_parcel


@pytest.fixture
def notifier(monkeypatch):
    """Stub every boundary `scan_and_notify` touches and record what it did."""
    sent = []
    pending = []
    tickets = []
    reroutes = []
    attempts = []

    monkeypatch.setattr(proactive_notifier, "_generate_notification_message",
                        lambda parcel, decision, days_overdue: f"delay notice for {parcel['tracking_number']}")
    monkeypatch.setattr(proactive_notifier, "send_whatsapp_message",
                        lambda phone, message, tracking_number=None: sent.append((phone, message, tracking_number)))
    monkeypatch.setattr(proactive_notifier, "create_pending_action",
                        lambda **kw: pending.append(kw))
    monkeypatch.setattr(proactive_notifier, "create_ticket",
                        lambda **kw: tickets.append(kw))
    monkeypatch.setattr(proactive_notifier, "create_reroute_request",
                        lambda **kw: reroutes.append(kw))
    monkeypatch.setattr(proactive_notifier, "record_attempt_outcome",
                        lambda *a, **kw: attempts.append(a))
    # No prior notification for this parcel, and recording the new one succeeds.
    monkeypatch.setattr(proactive_notifier, "find_existing_notification", lambda tn, reason: None)
    monkeypatch.setattr(proactive_notifier, "record_notification", lambda *a, **kw: True)

    def _with_parcels(*parcels):
        monkeypatch.setattr(proactive_notifier, "find_delayed_parcels", lambda: list(parcels))
        return proactive_notifier.scan_and_notify()

    return type("Notifier", (), {
        "run": staticmethod(_with_parcels),
        "sent": sent, "pending": pending, "tickets": tickets,
        "reroutes": reroutes, "attempts": attempts,
    })


def test_proactive_message_carries_the_tracking_number(notifier):
    """The outbound row in `messages` is only tied to a parcel if the tracking number
    is passed through the seam — otherwise the dashboard thread shows an
    unattributed proactive message."""
    parcel = make_parcel(tracking_number="TRK10001", delay_reason="customer_unavailable")
    notifier.run(parcel)

    assert len(notifier.sent) == 1
    phone, _message, tracking_number = notifier.sent[0]
    assert phone == parcel["customer_phone"]
    assert tracking_number == "TRK10001"


def test_notify_reason_opens_a_pending_action(notifier):
    """'notify' reasons are the ones the customer can actually resolve, so their
    reply must route into the corrective loop."""
    notifier.run(make_parcel(tracking_number="TRK10002", delay_reason="incorrect_address"))

    assert len(notifier.pending) == 1
    assert notifier.pending[0]["tracking_number"] == "TRK10002"
    assert notifier.pending[0]["trigger_reason"] == "incorrect_address"


@pytest.mark.parametrize("reason", ["vehicle_breakdown", "shipment_damaged"])
def test_operational_reasons_open_no_pending_action(notifier, reason):
    """reroute/escalate are operational — a customer reply can't fix them, so no
    pending action is opened and their next message is classified fresh."""
    notifier.run(make_parcel(delay_reason=reason))

    assert notifier.pending == []
    assert len(notifier.sent) == 1


def test_already_notified_parcel_is_skipped(notifier, monkeypatch):
    monkeypatch.setattr(proactive_notifier, "find_existing_notification",
                        lambda tn, reason: {"message": "already sent", "sent_at": None})
    count = notifier.run(make_parcel(delay_reason="customer_unavailable"))

    assert count == 0
    assert notifier.sent == []
    assert notifier.attempts == []
