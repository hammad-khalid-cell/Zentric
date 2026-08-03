"""Phase 5 — human handoff: the store's transitions, the staff-notification seam, and
the escalation paths that raise a handoff.

Follows the established boundary-mocking style: `SessionLocal` is replaced in the
module under test with a fake that records what was written, so the deterministic
lifecycle (idempotency, claim/resolve legality, lazy expiry) is asserted without a
database.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core import handoffs, staff_notifier
from app.core.handoffs import (
    STATUS_CLAIMED,
    STATUS_EXPIRED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    HandoffTransitionError,
)
from app.models.handoff import Handoff


# --- fake db -------------------------------------------------------------

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDB:
    """Returns whatever rows the test seeded, and records adds/commits. Filtering is
    the caller's business — each test seeds exactly the row the query should find."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0

    def query(self, *a, **k):
        return _FakeQuery(self.rows)

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(handoffs, "SessionLocal", lambda: fake)
    return fake


def make_row(**overrides) -> Handoff:
    row = Handoff(
        customer_phone="923001234567",
        tracking_number=None,
        reason="explicit_human_request",
        status=STATUS_OPEN,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
    )
    row.id = 1
    row.notify_failed = False
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


# --- creation & idempotency ----------------------------------------------

def test_create_handoff_opens_a_row(db):
    result = handoffs.create_handoff("923001234567", reason="explicit_human_request")

    assert result["already_existed"] is False
    assert result["status"] == STATUS_OPEN
    assert result["customer_phone"] == "923001234567"
    assert len(db.added) == 1


def test_create_handoff_is_idempotent_while_one_is_live(db):
    """Three angry messages in a row are one problem, not three — otherwise the queue
    fills with duplicates and staff get alerted repeatedly for the same customer."""
    db.rows.append(make_row())

    result = handoffs.create_handoff("923001234567", reason="angry_language")

    assert result["already_existed"] is True
    assert db.added == []


def test_a_claimed_handoff_blocks_a_new_one(db):
    """A fresh escalation must never yank a thread away from the human working it."""
    db.rows.append(make_row(status=STATUS_CLAIMED, claimed_by="hammad"))

    result = handoffs.create_handoff("923001234567", reason="tone_detected")

    assert result["already_existed"] is True
    assert result["claimed_by"] == "hammad"
    assert db.added == []


def test_create_handoff_records_the_parcel_and_ticket_when_there_is_one(db):
    result = handoffs.create_handoff(
        "923001234567", reason="shipment_damaged",
        tracking_number="TRK10001", ticket_id="TCK-0007",
    )
    assert result["tracking_number"] == "TRK10001"
    assert result["ticket_id"] == "TCK-0007"


# --- lookup & expiry ------------------------------------------------------

def test_get_active_handoff_returns_a_live_row(db):
    db.rows.append(make_row(status=STATUS_CLAIMED))
    assert handoffs.get_active_handoff("923001234567")["status"] == STATUS_CLAIMED


def test_get_active_handoff_is_none_when_there_is_nothing(db):
    assert handoffs.get_active_handoff("923001234567") is None


def test_expired_handoff_is_swept_and_returns_the_bot(db):
    """The safety valve: a human who claims a thread and walks away must not silence
    the bot for that customer forever."""
    stale = make_row(status=STATUS_CLAIMED,
                     expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db.rows.append(stale)

    assert handoffs.get_active_handoff("923001234567") is None
    assert stale.status == STATUS_EXPIRED     # flipped in place, not just filtered out
    assert db.commits == 1


def test_naive_expiry_timestamp_is_treated_as_utc(db):
    """Defensive: a naive datetime compared against an aware `now` would raise, and
    silently assuming server-local time would shift the window by hours."""
    naive_future = (datetime.now(timezone.utc) + timedelta(hours=4)).replace(tzinfo=None)
    db.rows.append(make_row(expires_at=naive_future))

    assert handoffs.get_active_handoff("923001234567") is not None


# --- transitions ----------------------------------------------------------

def test_claim_takes_ownership_and_records_who(db):
    row = make_row()
    db.rows.append(row)

    result = handoffs.claim_handoff(1, actor="hammad")

    assert result["status"] == STATUS_CLAIMED
    assert result["claimed_by"] == "hammad"
    assert result["claimed_at"] is not None


def test_claim_refreshes_the_expiry(db):
    """The TTL should run from when a human actually took the thread, not from when it
    was raised — otherwise a handoff raised 7 hours ago lapses almost immediately."""
    row = make_row(expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    db.rows.append(row)

    result = handoffs.claim_handoff(1, actor="hammad")

    assert result["expires_at"] > datetime.now(timezone.utc) + timedelta(hours=7)


def test_claiming_a_claimed_handoff_is_rejected(db):
    """Loudly, not silently: a second click must not overwrite who took it and when."""
    db.rows.append(make_row(status=STATUS_CLAIMED, claimed_by="hammad"))

    with pytest.raises(HandoffTransitionError) as error:
        handoffs.claim_handoff(1, actor="someone_else")
    assert error.value.current_status == STATUS_CLAIMED


def test_resolve_hands_the_thread_back(db):
    db.rows.append(make_row(status=STATUS_CLAIMED, claimed_by="hammad"))

    result = handoffs.resolve_handoff(1, actor="hammad", note="Called the customer.")

    assert result["status"] == STATUS_RESOLVED
    assert result["resolved_by"] == "hammad"
    assert result["resolved_at"] is not None
    assert result["resolution_note"] == "Called the customer."


def test_resolve_is_allowed_directly_from_open(db):
    """A case settled out of band (a phone call) never needed claiming first."""
    db.rows.append(make_row(status=STATUS_OPEN))
    assert handoffs.resolve_handoff(1, actor="hammad")["status"] == STATUS_RESOLVED


def test_resolving_twice_is_rejected(db):
    """Idempotent-looking no-ops would rewrite resolved_at and corrupt the trail."""
    db.rows.append(make_row(status=STATUS_RESOLVED, resolved_by="hammad"))

    with pytest.raises(HandoffTransitionError) as error:
        handoffs.resolve_handoff(1, actor="someone_else")
    assert error.value.current_status == STATUS_RESOLVED


def test_transition_on_a_missing_handoff_is_rejected(db):
    with pytest.raises(HandoffTransitionError):
        handoffs.claim_handoff(999, actor="hammad")


# --- notification delivery is itself auditable ---------------------------

def test_mark_notified_records_success(db):
    row = make_row()
    db.rows.append(row)

    handoffs.mark_notified(1, delivered=True)

    assert row.notified_at is not None
    assert row.notify_failed is False


def test_mark_notified_records_failure(db):
    """A failed alert is stored, not swallowed — an un-notified handoff sitting in the
    queue is exactly what an ops lead needs to be able to see."""
    row = make_row()
    db.rows.append(row)

    handoffs.mark_notified(1, delivered=False)

    assert row.notified_at is None
    assert row.notify_failed is True


# --- the staff notification seam -----------------------------------------

def test_staff_channel_defaults_to_log(monkeypatch):
    monkeypatch.setattr(staff_notifier.config, "STAFF_NOTIFY_PROVIDER", "log")
    assert isinstance(staff_notifier._build_channel(), staff_notifier.LogStaffChannel)


@pytest.mark.parametrize("provider,expected", [
    ("slack", staff_notifier.SlackStaffChannel),
    ("email", staff_notifier.EmailStaffChannel),
])
def test_staff_channel_selection(monkeypatch, provider, expected):
    monkeypatch.setattr(staff_notifier.config, "STAFF_NOTIFY_PROVIDER", provider)
    assert isinstance(staff_notifier._build_channel(), expected)


def test_notify_staff_sends_through_the_configured_channel(monkeypatch):
    sent = []

    class FakeChannel:
        def send(self, subject, body, context=None):
            sent.append((subject, body, context))

    monkeypatch.setattr(staff_notifier, "_channel", FakeChannel())

    delivered = staff_notifier.notify_staff({
        "id": 3, "customer_phone": "923001234567", "reason": "explicit_human_request",
        "tracking_number": "TRK10001", "ticket_id": None,
    })

    assert delivered is True
    subject, body, context = sent[0]
    assert "923001234567" in subject
    assert "speak to a human" in body
    assert "TRK10001" in body
    assert context["handoff_id"] == 3


def test_notify_staff_never_raises(monkeypatch):
    """A channel outage must not roll back the handoff it belongs to — the durable row
    is the source of truth and the queue still shows the case."""
    class BrokenChannel:
        def send(self, subject, body, context=None):
            raise RuntimeError("slack is down")

    monkeypatch.setattr(staff_notifier, "_channel", BrokenChannel())

    assert staff_notifier.notify_staff({"id": 1, "customer_phone": "923001234567",
                                        "reason": "angry_language"}) is False


def test_staff_alerts_do_not_use_the_customer_whatsapp_seam():
    """A separate port on purpose: routing staff alerts through send_whatsapp_message
    would inject internal notices into customer threads (the mock persists to
    `messages` keyed by customer_phone) and would spend real Meta quota in Phase 7.

    Asserted against the module's actual imports rather than its text — the docstring
    legitimately *discusses* the customer seam while the code must never reach for it.
    """
    import ast
    import inspect

    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(staff_notifier))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)

    assert not any("whatsapp" in name.lower() for name in imported), imported
    assert not hasattr(staff_notifier, "send_whatsapp_message")
