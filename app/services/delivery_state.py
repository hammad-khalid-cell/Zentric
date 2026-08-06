"""The deterministic delivery state machine (Phase 6).

**Why this exists.** Phases 2-3 already changed a parcel when a corrective action
landed — `apply_reschedule` / `apply_address_update` set `out_for_delivery`, bump
`attempt_count` and clear `delay_reason` — and `record_attempt_outcome` already wrote
the `DeliveryAttempt` and `InterventionOutcome` rows the metrics read. What was missing
is an *ending*: nothing ever moved a parcel to `delivered` or declared it a return, so
every parcel sat at `out_for_delivery` forever and the answer to "did it actually get
delivered?" was only ever an audit row. This module supplies that ending, and it is the
literal definition of the RTO the whole project is about.

**It is a lookup, not a judgement.** `docs/PROJECT_PLAN.md` §5.1 — the LLM never
chooses a business action. Nothing here consults a model, reads free text, or takes a
probability: given a status, an outcome and a count, exactly one next status follows.
That is what makes the "RTO prevented" number defensible rather than asserted.

No migration: `Parcel.status` is an existing free-text column, and the two new values
join the six `seed_data.STATUSES` already uses.
"""

# The six already in circulation (app/core/seed_data.py) plus the two this module adds.
STATUS_BOOKED = "booked"
STATUS_PICKED_UP = "picked_up"
STATUS_IN_TRANSIT = "in_transit"
STATUS_ARRIVED_AT_FACILITY = "arrived_at_facility"
STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
STATUS_DELIVERED = "delivered"

# New. `attempt_failed` is the state a parcel sits in *between* a failed attempt and
# the customer fixing whatever caused it — precisely the window the proactive loop
# exists to act in, and previously unrepresentable.
STATUS_ATTEMPT_FAILED = "attempt_failed"
# The cost centre. A parcel shipped out and back with zero cash collected.
STATUS_RETURNED_TO_ORIGIN = "returned_to_origin"

#: Nothing moves a parcel out of these. Guarded rather than assumed: a delivered parcel
#: that could be re-failed would corrupt both the attempt history and the RTO metric.
TERMINAL_STATUSES = frozenset({STATUS_DELIVERED, STATUS_RETURNED_TO_ORIGIN})

#: How many failed attempts before the parcel goes back to the sender.
#:
#: **This is an assumption, not sourced courier data** — hold it to the same standard
#: `docs/PROJECT_PLAN.md` §3 demands of `RTO_COST_PKR`, and present it as tunable.
#: Three is the common industry norm and it is what makes the demo honest: it leaves a
#: real window in which a corrective action can still rescue the parcel, rather than
#: making rescue impossible (2) or implausibly easy (5+). A module constant for now,
#: same as `action_service.RESCHEDULE_DAYS`; move it to config if it ever needs to be
#: tuned live during a defense.
MAX_DELIVERY_ATTEMPTS = 3

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"


#: Still at the origin — nothing has gone out with a rider yet.
AT_ORIGIN_STATUSES = frozenset({STATUS_BOOKED, STATUS_PICKED_UP})


def is_terminal(status: str | None) -> bool:
    """Has this parcel's journey ended? Nothing may transition out of a terminal state."""
    return status in TERMINAL_STATUSES


def can_attempt_delivery(status: str | None) -> bool:
    """Could a rider plausibly have attempted delivery on a parcel in this state?

    Narrower than "not terminal", and deliberately *not* the rule the whole system uses.
    Two different real events write a `DeliveryAttempt`:

    - the scanner noticing an overdue parcel — legitimate at any status, including
      `in_transit`, and that is where most first-failure rows come from;
    - a courier reporting back that a rider tried — which requires the parcel to have
      actually left the origin.

    Only the second is bounded by this, which is why callers opt in
    (`record_attempt_outcome(..., require_dispatched=True)`) rather than it applying
    everywhere and silently starving the RTO metric of its organic failures.
    """
    return not is_terminal(status) and status not in AT_ORIGIN_STATUSES


def next_status(current_status: str | None, outcome: str, attempts_made: int) -> str | None:
    """The status a parcel takes after a delivery attempt resolves, or `None` if the
    attempt should not move it at all.

    `attempts_made` is 1-based — the count *including* the attempt being recorded. Note
    that `DeliveryAttempt.attempt_no` is 0-based (it mirrors `Parcel.attempt_count`,
    which starts at 0 and is bumped by each corrective action), so the caller passes
    `attempt_no + 1`. Keeping this argument 1-based means the rule reads the way the
    business states it: "three attempts and it goes back", not "attempt_no >= 2".

    Returns `None` for a no-op so the caller can distinguish "leave the parcel alone"
    from "set it to what it already was".
    """
    if is_terminal(current_status):
        return None

    if outcome == OUTCOME_SUCCESS:
        return STATUS_DELIVERED

    if outcome == OUTCOME_FAILED:
        if attempts_made >= MAX_DELIVERY_ATTEMPTS:
            return STATUS_RETURNED_TO_ORIGIN
        return STATUS_ATTEMPT_FAILED

    # An outcome we don't model shouldn't silently invent a transition. Same instinct as
    # REASON_TO_DECISION defaulting unknown reasons to `escalate`: when the rule doesn't
    # cover the input, do nothing rather than guess.
    return None


def attempts_remaining(attempts_made: int) -> int:
    """How many attempts are left before this parcel becomes an RTO. Drives the "1 of 3"
    style label on the dashboard, so the operator can see a parcel running out of road."""
    return max(0, MAX_DELIVERY_ATTEMPTS - attempts_made)
