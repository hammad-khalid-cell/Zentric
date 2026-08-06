"""Phase 3 — delivery_service.record_attempt_outcome: the write side of RTO tracking.
Follows the FakeDB monkeypatch pattern from tests/test_corrective_actions.py, extended
to route different models (Parcel / Intervention / the InterventionOutcome.intervention_id
column select) to different canned results, since record_attempt_outcome issues more
than one distinct query per call.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import delivery_service
from app.models.parcel import Parcel
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.models.delivery_attempt import DeliveryAttempt


class _ColumnRow:
    def __init__(self, intervention_id):
        self.intervention_id = intervention_id


class _FakeQuery:
    def __init__(self, rows, single=None):
        self._rows = rows
        self._single = single

    def filter(self, *a, **k):
        return self

    def filter_by(self, **kw):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._rows

    def first(self):
        if self._single is not None:
            return self._single
        return self._rows[0] if self._rows else None


class FakeDB:
    """Routes db.query(X) to canned results per target: Parcel -> a single row,
    Intervention -> a list, anything else (the InterventionOutcome.intervention_id
    column select) -> a list of resolved intervention ids wrapped as rows."""

    def __init__(self, parcel=None, interventions=None, resolved_intervention_ids=None, flush_raises=None):
        self.parcel = parcel
        self.interventions = interventions or []
        self.resolved_intervention_ids = resolved_intervention_ids or []
        self._flush_raises = flush_raises
        self.added = []
        self.committed = False
        self.rolled_back = False
        self._next_id = 100

    def query(self, target):
        if target is Parcel:
            return _FakeQuery([], single=self.parcel)
        if target is Intervention:
            return _FakeQuery(self.interventions)
        return _FakeQuery([_ColumnRow(i) for i in self.resolved_intervention_ids])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        if self._flush_raises is not None:
            exc, self._flush_raises = self._flush_raises, None
            raise exc
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _parcel(**overrides) -> Parcel:
    fields = {
        "tracking_number": "TRK20250",
        "customer_phone": "923001234567",
        "status": "out_for_delivery",
        "current_hub": "Karachi Local Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=4),
        "expected_delivery_date": date.today() - timedelta(days=1),
        "delay_reason": "incorrect_address",
        "address_line": "House 9, Karachi",
        "preferred_delivery_window": None,
        "attempt_count": 1,
    }
    fields.update(overrides)
    return Parcel(**fields)


def _intervention(**overrides) -> Intervention:
    fields = {
        "intervention_id": "INT-0001",
        "tracking_number": "TRK20250",
        "action": "reschedule",
        "detail": "Delivery rescheduled.",
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return Intervention(**fields)


def test_parcel_not_found_records_nothing(monkeypatch):
    fake = FakeDB(parcel=None)
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK00000", "failed", "incorrect_address", source=delivery_service.SOURCE_SIMULATOR)

    assert result == {"recorded": False, "reason": "parcel_not_found"}
    assert fake.added == []
    assert fake.committed is False


def test_records_failed_attempt_with_no_open_intervention(monkeypatch):
    parcel = _parcel(attempt_count=1)
    fake = FakeDB(parcel=parcel, interventions=[])
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK20250", "failed", "incorrect_address", source=delivery_service.SOURCE_SIMULATOR)

    assert result["recorded"] is True
    assert result["attempt_no"] == 1
    assert result["intervention_outcome_id"] is None
    attempts = [o for o in fake.added if isinstance(o, DeliveryAttempt)]
    assert len(attempts) == 1
    assert attempts[0].outcome == "failed"
    assert attempts[0].failure_reason == "incorrect_address"
    assert not any(isinstance(o, InterventionOutcome) for o in fake.added)
    assert fake.committed is True


def test_success_links_unresolved_intervention_as_delivered(monkeypatch):
    parcel = _parcel(attempt_count=2)
    intervention = _intervention()
    fake = FakeDB(parcel=parcel, interventions=[intervention], resolved_intervention_ids=[])
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK20250", "success", source=delivery_service.SOURCE_SIMULATOR)

    assert result["recorded"] is True
    outcomes = [o for o in fake.added if isinstance(o, InterventionOutcome)]
    assert len(outcomes) == 1
    assert outcomes[0].outcome == "delivered"
    assert outcomes[0].intervention_id == "INT-0001"
    assert result["intervention_outcome_id"] is not None


def test_failed_resolution_marks_still_failed(monkeypatch):
    parcel = _parcel(attempt_count=2)
    intervention = _intervention()
    fake = FakeDB(parcel=parcel, interventions=[intervention], resolved_intervention_ids=[])
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    delivery_service.record_attempt_outcome("TRK20250", "failed", "customer_unavailable", source=delivery_service.SOURCE_SIMULATOR)

    outcomes = [o for o in fake.added if isinstance(o, InterventionOutcome)]
    assert outcomes[0].outcome == "still_failed"


def test_already_resolved_intervention_is_not_linked_again(monkeypatch):
    parcel = _parcel(attempt_count=3)
    intervention = _intervention()
    fake = FakeDB(parcel=parcel, interventions=[intervention], resolved_intervention_ids=["INT-0001"])
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK20250", "success", source=delivery_service.SOURCE_SIMULATOR)

    assert not any(isinstance(o, InterventionOutcome) for o in fake.added)
    assert result["intervention_outcome_id"] is None


def test_duplicate_attempt_is_rejected_and_rolled_back(monkeypatch):
    parcel = _parcel(attempt_count=1)
    fake = FakeDB(parcel=parcel, flush_raises=IntegrityError("dup", None, None))
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK20250", "failed", "incorrect_address", source=delivery_service.SOURCE_SIMULATOR)

    assert result == {"recorded": False, "reason": "duplicate_attempt"}
    assert fake.rolled_back is True
    assert fake.committed is False
