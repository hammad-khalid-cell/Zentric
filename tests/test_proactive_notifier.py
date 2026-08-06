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

    # Phase 6 retry accounting. Real DB calls, and the socket guard in conftest cannot
    # catch them — psycopg2 connects through libpq in C, below the Python socket layer
    # it patches — so leaving these unstubbed silently puts Supabase on the path of
    # every test in this file.
    failures = []
    cleared = []
    monkeypatch.setattr(proactive_notifier, "dead_lettered_keys", lambda: set())
    monkeypatch.setattr(proactive_notifier, "record_failure",
                        lambda tn, reason, error: (failures.append((tn, reason, error)),
                                                   {"recorded": True, "attempts": 1,
                                                    "status": "retrying", "dead": False})[1])
    monkeypatch.setattr(proactive_notifier, "clear_failure",
                        lambda tn, reason: (cleared.append((tn, reason)), False)[1])

    def _with_parcels(*parcels, **kwargs):
        monkeypatch.setattr(proactive_notifier, "find_delayed_parcels", lambda: list(parcels))
        return proactive_notifier.scan_and_notify(**kwargs)

    return type("Notifier", (), {
        "run": staticmethod(_with_parcels),
        "sent": sent, "pending": pending, "tickets": tickets,
        "reroutes": reroutes, "attempts": attempts,
        "failures": failures, "cleared": cleared,
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
    result = notifier.run(make_parcel(delay_reason="customer_unavailable"))

    assert result["sent"] == 0
    assert notifier.sent == []
    assert notifier.attempts == []


# --- retries, dead-lettering, and the send cap (Phase 6) ---------------------


def test_a_failed_parcel_is_recorded_rather_than_only_logged(notifier, monkeypatch):
    """The failure this replaces was `logger.exception(...); continue` — a customer who
    was never warned about their delay, indistinguishable from one who was."""
    monkeypatch.setattr(proactive_notifier, "record_notification",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("groq is down")))
    result = notifier.run(make_parcel(tracking_number="TRK10003",
                                      delay_reason="customer_unavailable"))

    assert result["failed"] == 1
    assert result["sent"] == 0
    assert notifier.failures[0][0] == "TRK10003"
    assert "groq is down" in notifier.failures[0][2]


def test_one_bad_parcel_does_not_stop_the_scan(notifier, monkeypatch):
    """The reason the per-parcel handler exists at all: a poisoned parcel must not cost
    every other overdue customer their notification."""
    def explode_on_first(tracking_number, *a, **kw):
        if tracking_number == "TRK_BAD":
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(proactive_notifier, "record_notification", explode_on_first)
    result = notifier.run(
        make_parcel(tracking_number="TRK_BAD", delay_reason="customer_unavailable"),
        make_parcel(tracking_number="TRK_OK", delay_reason="customer_unavailable"),
    )

    assert result == {"sent": 1, "failed": 1, "dead_lettered": 0,
                      "skipped_dead": 0, "capped": False}


def test_a_dead_lettered_parcel_is_skipped_before_any_llm_call(notifier, monkeypatch):
    """The point of giving up is that it stops costing something per run — so the skip
    has to happen before the message is generated, not after."""
    generated = []
    monkeypatch.setattr(proactive_notifier, "_generate_notification_message",
                        lambda *a: generated.append(a) or "should not happen")
    monkeypatch.setattr(proactive_notifier, "dead_lettered_keys",
                        lambda: {("TRK10004", "customer_unavailable")})

    result = notifier.run(make_parcel(tracking_number="TRK10004",
                                      delay_reason="customer_unavailable"))

    assert result["skipped_dead"] == 1
    assert result["sent"] == 0
    assert generated == []
    assert notifier.sent == []


def test_a_recovered_parcel_clears_its_failure_record(notifier):
    """A parcel that failed twice then succeeded is a transient blip; leaving the record
    behind would have someone chasing a dead-letter that already fixed itself."""
    notifier.run(make_parcel(tracking_number="TRK10005", delay_reason="customer_unavailable"))

    assert notifier.cleared == [("TRK10005", "customer_unavailable")]


def test_the_send_cap_bounds_a_run(notifier):
    """Blast-radius control for Phase 7, where every send is real Meta quota: if a clock
    or migration problem suddenly makes a thousand parcels look overdue, the worker must
    not message all thousand before anyone notices."""
    parcels = [make_parcel(tracking_number=f"TRK1100{i}", delay_reason="customer_unavailable")
               for i in range(5)]
    result = notifier.run(*parcels, max_sends=2)

    assert result["sent"] == 2
    assert result["capped"] is True
    assert len(notifier.sent) == 2


def test_no_cap_sends_everything(notifier):
    parcels = [make_parcel(tracking_number=f"TRK1200{i}", delay_reason="customer_unavailable")
               for i in range(4)]
    result = notifier.run(*parcels)

    assert result["sent"] == 4
    assert result["capped"] is False
