"""The delivery state machine.

This is the layer that decides whether a parcel counts as delivered or as an RTO, so
the "RTO prevented" figure in the defense rests on exactly these rules. It is pure —
no DB, no LLM, no randomness — which is the point: every assertion here is a business
rule stated once and checkable, per docs/PROJECT_PLAN.md §5.1.
"""
import pytest

from app.services.delivery_state import (
    MAX_DELIVERY_ATTEMPTS,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    STATUS_ATTEMPT_FAILED,
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_RETURNED_TO_ORIGIN,
    TERMINAL_STATUSES,
    attempts_remaining,
    is_terminal,
    next_status,
)


def test_a_successful_attempt_delivers_the_parcel():
    assert next_status(STATUS_OUT_FOR_DELIVERY, OUTCOME_SUCCESS, 1) == STATUS_DELIVERED


def test_success_delivers_regardless_of_how_many_attempts_it_took():
    """The rescue case, and the one the whole pitch turns on: a parcel on its last
    permitted attempt still delivers rather than being counted as a return."""
    assert next_status(STATUS_ATTEMPT_FAILED, OUTCOME_SUCCESS, MAX_DELIVERY_ATTEMPTS) \
        == STATUS_DELIVERED


def test_an_early_failure_is_recoverable_not_a_return():
    """A failed attempt with attempts left is exactly the window the proactive loop
    acts in — it must not be terminal, or there would be nothing to intervene on."""
    for attempts in range(1, MAX_DELIVERY_ATTEMPTS):
        assert next_status(STATUS_OUT_FOR_DELIVERY, OUTCOME_FAILED, attempts) \
            == STATUS_ATTEMPT_FAILED


def test_the_final_failure_returns_the_parcel_to_origin():
    """The cost centre being incurred. Three attempts is an assumption, not sourced
    data — this test pins the rule, not its correctness as courier practice."""
    assert next_status(STATUS_OUT_FOR_DELIVERY, OUTCOME_FAILED, MAX_DELIVERY_ATTEMPTS) \
        == STATUS_RETURNED_TO_ORIGIN


def test_failures_beyond_the_limit_stay_returned():
    """Defensive: a miscounted attempt must not walk back out of RTO."""
    assert next_status(STATUS_OUT_FOR_DELIVERY, OUTCOME_FAILED, MAX_DELIVERY_ATTEMPTS + 4) \
        == STATUS_RETURNED_TO_ORIGIN


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES))
@pytest.mark.parametrize("outcome", [OUTCOME_SUCCESS, OUTCOME_FAILED])
def test_nothing_moves_a_parcel_out_of_a_terminal_state(terminal, outcome):
    """A delivered parcel that could be re-failed would corrupt the attempt history and
    silently move the RTO metric — so the machine refuses rather than recalculating."""
    assert is_terminal(terminal)
    assert next_status(terminal, outcome, 1) is None


def test_an_unmodelled_outcome_changes_nothing():
    """Same instinct as REASON_TO_DECISION defaulting unknown reasons to `escalate`:
    when the rule doesn't cover the input, do nothing rather than guess."""
    assert next_status(STATUS_OUT_FOR_DELIVERY, "lost_in_a_flood", 1) is None
    assert next_status(STATUS_IN_TRANSIT, "", 1) is None


def test_in_flight_statuses_are_not_terminal():
    for status in (STATUS_IN_TRANSIT, STATUS_OUT_FOR_DELIVERY, STATUS_ATTEMPT_FAILED):
        assert not is_terminal(status)


def test_attempts_remaining_counts_down_and_floors_at_zero():
    """Drives the "1 of 3" label — an operator has to be able to see a parcel running
    out of road before it becomes a return."""
    assert attempts_remaining(0) == MAX_DELIVERY_ATTEMPTS
    assert attempts_remaining(1) == MAX_DELIVERY_ATTEMPTS - 1
    assert attempts_remaining(MAX_DELIVERY_ATTEMPTS) == 0
    assert attempts_remaining(MAX_DELIVERY_ATTEMPTS + 3) == 0


def test_the_machine_is_pure():
    """Same inputs, same answer, no matter how often it is asked — this is what lets
    the RTO number be recomputed from the attempt log and come out the same."""
    calls = [next_status(STATUS_OUT_FOR_DELIVERY, OUTCOME_FAILED, 2) for _ in range(50)]
    assert len(set(calls)) == 1
