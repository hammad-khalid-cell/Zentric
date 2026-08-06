"""Phase 6 — the parcel itself moves when a delivery attempt resolves.

Before this, `record_attempt_outcome` wrote the audit rows and left the parcel sitting
at `out_for_delivery` forever, so "did it actually get delivered?" had no answer on the
parcel. These cover the join between that function and the pure state machine in
`delivery_state`; the rules themselves are pinned in tests/test_delivery_state.py.

Reuses the FakeDB harness from tests/test_delivery_service.py rather than duplicating
it — same seam, same monkeypatch of SessionLocal.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.delivery_attempt import DeliveryAttempt
from app.services import delivery_service, delivery_state
from tests.test_delivery_service import FakeDB, _parcel


def _record(monkeypatch, parcel, outcome, reason=None):
    fake = FakeDB(parcel=parcel)
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)
    return delivery_service.record_attempt_outcome("TRK20250", outcome, reason, source=delivery_service.SOURCE_SIMULATOR), fake


def test_a_successful_attempt_marks_the_parcel_delivered(monkeypatch):
    """The moment the demo turns on: an audit row becomes an observable outcome."""
    parcel = _parcel(attempt_count=1, status="out_for_delivery")
    result, _ = _record(monkeypatch, parcel, "success")

    assert parcel.status == delivery_state.STATUS_DELIVERED
    assert result["status"] == delivery_state.STATUS_DELIVERED
    assert result["previous_status"] == "out_for_delivery"
    assert result["status_changed"] is True


def test_delivery_clears_any_outstanding_delay_reason(monkeypatch):
    """A delivered parcel with a delay_reason still set would stay in
    find_delayed_parcels' sights and re-notify a customer who already has the parcel."""
    parcel = _parcel(attempt_count=1, delay_reason="incorrect_address")
    _record(monkeypatch, parcel, "success")

    assert parcel.delay_reason is None


def test_a_recoverable_failure_leaves_the_parcel_actionable(monkeypatch):
    """attempt_count=0 -> attempt_no 0 -> first of three attempts, so this is the state
    the proactive loop is supposed to rescue, not a return."""
    parcel = _parcel(attempt_count=0)
    result, _ = _record(monkeypatch, parcel, "failed", "customer_unavailable")

    assert parcel.status == delivery_state.STATUS_ATTEMPT_FAILED
    assert result["attempts_remaining"] == delivery_state.MAX_DELIVERY_ATTEMPTS - 1


def test_the_final_failure_returns_the_parcel_to_origin(monkeypatch):
    """attempt_count=2 -> attempt_no 2 -> the third attempt. This is the RTO the whole
    project exists to prevent, and the point at which the cost is incurred."""
    parcel = _parcel(attempt_count=delivery_state.MAX_DELIVERY_ATTEMPTS - 1)
    result, _ = _record(monkeypatch, parcel, "failed", "customer_unavailable")

    assert parcel.status == delivery_state.STATUS_RETURNED_TO_ORIGIN
    assert result["attempts_remaining"] == 0


def test_a_finished_parcel_refuses_the_attempt_outright(monkeypatch):
    """Terminal means terminal. The attempt is refused *before* anything is written —
    recording it and then declining to move the parcel would leave a row in the history
    contradicting the parcel's own outcome, and would still feed the RTO metric."""
    parcel = _parcel(attempt_count=1, status=delivery_state.STATUS_DELIVERED)
    result, fake = _record(monkeypatch, parcel, "failed", "customer_unavailable")

    assert result["recorded"] is False
    assert result["reason"] == "parcel_journey_complete"
    assert parcel.status == delivery_state.STATUS_DELIVERED
    assert not any(isinstance(o, DeliveryAttempt) for o in fake.added)
    assert fake.committed is False


def test_a_rejected_duplicate_attempt_does_not_move_the_parcel(monkeypatch):
    """The status change must not survive the rollback that a duplicate attempt causes,
    or the parcel would advance on an attempt that was never recorded."""
    parcel = _parcel(attempt_count=1, status="out_for_delivery")
    fake = FakeDB(parcel=parcel, flush_raises=IntegrityError("dup", None, None))
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    result = delivery_service.record_attempt_outcome("TRK20250", "success", source=delivery_service.SOURCE_SIMULATOR)

    assert result == {"recorded": False, "reason": "duplicate_attempt"}
    assert parcel.status == "out_for_delivery"
    assert fake.committed is False


# --- provenance ---------------------------------------------------------------
#
# These rows feed the "RTO prevented" headline, and some of them are created by a human
# clicking a button. docs/PROJECT_PLAN.md §3 requires modelled data to be presented as
# modelled — so which is which has to be recorded, not assumed.


def test_source_is_required_and_cannot_be_defaulted(monkeypatch):
    """Keyword-only with no default: an untagged outcome must be impossible to write,
    not merely discouraged. A default would silently blur modelled into observed."""
    parcel = _parcel(attempt_count=1)
    fake = FakeDB(parcel=parcel)
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    with pytest.raises(TypeError):
        delivery_service.record_attempt_outcome("TRK20250", "success")


def test_provenance_is_written_onto_the_attempt(monkeypatch):
    parcel = _parcel(attempt_count=1)
    fake = FakeDB(parcel=parcel)
    monkeypatch.setattr(delivery_service, "SessionLocal", lambda: fake)

    delivery_service.record_attempt_outcome(
        "TRK20250", "success",
        source=delivery_service.SOURCE_OPS_CONSOLE, recorded_by="hammad",
    )

    attempt = next(o for o in fake.added if isinstance(o, DeliveryAttempt))
    assert attempt.source == delivery_service.SOURCE_OPS_CONSOLE
    assert attempt.recorded_by == "hammad"


def test_button_and_simulator_outcomes_are_classed_as_modelled():
    """The set that must never be presented as observed courier data. The agent and
    scanner sources are the system observing its own traffic, which is a different and
    stronger claim — they must not drift into this set."""
    assert delivery_service.SOURCE_OPS_CONSOLE in delivery_service.MODELLED_SOURCES
    assert delivery_service.SOURCE_SIMULATOR in delivery_service.MODELLED_SOURCES
    assert delivery_service.SOURCE_AGENT not in delivery_service.MODELLED_SOURCES
    assert delivery_service.SOURCE_SCANNER not in delivery_service.MODELLED_SOURCES
    assert delivery_service.SOURCE_COURIER_WEBHOOK not in delivery_service.MODELLED_SOURCES
