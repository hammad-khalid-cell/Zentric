"""Phase 2 proactive loop — the write-back side.

Corrective actions mutate the parcel (updatable address + rescheduled attempt) and
write an auditable Intervention row, always ownership-verified. Plus the pending-action
store: dedup, lazy expiry, and resolution. External boundary (Postgres) is faked so the
deterministic logic is what's under test.
"""
from datetime import date, datetime, timedelta, timezone

from app.services import action_service
from app.services.action_service import apply_address_update, apply_reschedule
from app.models.parcel import Parcel
from app.models.intervention import Intervention
from app.models.pending_action import PendingAction
from app.core import pending_actions


# --- fake SQLAlchemy session ---------------------------------------------

class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter_by(self, **kw):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self._result


class FakeDB:
    """Minimal stand-in for a SessionLocal() — returns a fixed row for any query and
    records adds/commit so tests can assert what was written."""

    def __init__(self, query_result=None):
        self.query_result = query_result
        self.added = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self.query_result)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 1

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _seed_parcel(**overrides) -> Parcel:
    fields = {
        "tracking_number": "TRK20250",
        "customer_phone": "923001234567",
        "status": "out_for_delivery",
        "current_hub": "Karachi Local Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=4),
        "expected_delivery_date": date.today() - timedelta(days=1),
        "delay_reason": "incorrect_address",
        "address_line": "House 9 (incomplete), Karachi",
        "preferred_delivery_window": None,
        "attempt_count": 1,
    }
    fields.update(overrides)
    return Parcel(**fields)


# --- apply_address_update -------------------------------------------------

def test_address_update_mutates_parcel_and_writes_audit_row(monkeypatch):
    parcel = _seed_parcel()
    fake = FakeDB(query_result=parcel)
    monkeypatch.setattr(action_service, "SessionLocal", lambda: fake)

    result = apply_address_update("TRK20250", "House 15, Gulshan, Karachi", "923001234567")

    assert result["applied"] is True
    assert parcel.address_line == "House 15, Gulshan, Karachi"
    assert parcel.attempt_count == 2               # a fresh attempt was scheduled
    assert parcel.delay_reason is None             # the address problem is resolved
    assert parcel.expected_delivery_date > date.today()
    assert result["intervention_id"].startswith("INT-")
    assert any(isinstance(o, Intervention) and o.action == "update_address" for o in fake.added)
    assert fake.committed is True


def test_address_update_rejects_unowned_parcel(monkeypatch):
    parcel = _seed_parcel(customer_phone="923009999999")
    fake = FakeDB(query_result=parcel)
    monkeypatch.setattr(action_service, "SessionLocal", lambda: fake)

    result = apply_address_update("TRK20250", "House 15, Karachi", "923001234567")

    assert result["applied"] is False
    assert parcel.address_line == "House 9 (incomplete), Karachi"  # untouched
    assert fake.added == []
    assert fake.committed is False


def test_address_update_missing_parcel(monkeypatch):
    fake = FakeDB(query_result=None)
    monkeypatch.setattr(action_service, "SessionLocal", lambda: fake)

    result = apply_address_update("TRK00000", "wherever", "923001234567")
    assert result["applied"] is False


# --- apply_reschedule -----------------------------------------------------

def test_reschedule_bumps_attempt_and_records_window(monkeypatch):
    parcel = _seed_parcel(delay_reason="customer_unavailable")
    fake = FakeDB(query_result=parcel)
    monkeypatch.setattr(action_service, "SessionLocal", lambda: fake)

    result = apply_reschedule("TRK20250", "923001234567", window="tomorrow evening")

    assert result["applied"] is True
    assert parcel.attempt_count == 2
    assert parcel.preferred_delivery_window == "tomorrow evening"
    assert parcel.expected_delivery_date > date.today()
    assert any(isinstance(o, Intervention) and o.action == "reschedule" for o in fake.added)


def test_reschedule_rejects_unowned_parcel(monkeypatch):
    parcel = _seed_parcel(customer_phone="923009999999")
    fake = FakeDB(query_result=parcel)
    monkeypatch.setattr(action_service, "SessionLocal", lambda: fake)

    result = apply_reschedule("TRK20250", "923001234567")
    assert result["applied"] is False
    assert fake.committed is False


# --- pending-action store -------------------------------------------------

def test_create_pending_action_dedups_open_row(monkeypatch):
    existing = PendingAction(
        id=7, tracking_number="TRK20250", customer_phone="923001234567",
        trigger_reason="incorrect_address", status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
    )
    fake = FakeDB(query_result=existing)
    monkeypatch.setattr(pending_actions, "SessionLocal", lambda: fake)

    result = pending_actions.create_pending_action("TRK20250", "923001234567", "incorrect_address")
    assert result["already_existed"] is True
    assert result["id"] == 7
    assert fake.added == []  # no duplicate inserted


def test_create_pending_action_inserts_when_none_open(monkeypatch):
    fake = FakeDB(query_result=None)
    monkeypatch.setattr(pending_actions, "SessionLocal", lambda: fake)

    result = pending_actions.create_pending_action("TRK20250", "923001234567", "incorrect_address")
    assert result["already_existed"] is False
    assert any(isinstance(o, PendingAction) for o in fake.added)
    assert fake.committed is True


def test_get_open_pending_action_lazily_expires_stale_row(monkeypatch):
    stale = PendingAction(
        id=3, tracking_number="TRK20250", customer_phone="923001234567",
        trigger_reason="incorrect_address", status="open",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # already expired
    )
    fake = FakeDB(query_result=stale)
    monkeypatch.setattr(pending_actions, "SessionLocal", lambda: fake)

    assert pending_actions.get_open_pending_action("923001234567") is None
    assert stale.status == "expired"   # swept in place
    assert fake.committed is True


def test_get_open_pending_action_returns_active_row(monkeypatch):
    active = PendingAction(
        id=4, tracking_number="TRK20250", customer_phone="923001234567",
        trigger_reason="incorrect_address", status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=10),
    )
    fake = FakeDB(query_result=active)
    monkeypatch.setattr(pending_actions, "SessionLocal", lambda: fake)

    result = pending_actions.get_open_pending_action("923001234567")
    assert result is not None
    assert result["tracking_number"] == "TRK20250"


def test_resolve_pending_action_marks_resolved(monkeypatch):
    row = PendingAction(
        id=5, tracking_number="TRK20250", customer_phone="923001234567",
        trigger_reason="incorrect_address", status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=10),
    )
    fake = FakeDB(query_result=row)
    monkeypatch.setattr(pending_actions, "SessionLocal", lambda: fake)

    pending_actions.resolve_pending_action(5)
    assert row.status == "resolved"
    assert fake.committed is True
