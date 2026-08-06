"""Read-only queries backing the ops dashboard (Phase 4).

Everything here is a SELECT. The dashboard never mutates domain state — business
changes only ever originate from the graph or the proactive loop, so the audit trail
(`docs/PROJECT_PLAN.md` §5.2) stays complete and there is no "acted-on" exposure to
reason about. Access is gated by `app/core/auth.py`; see §5.3 there.

Row shaping is split into pure helpers (`_ticket_to_case`, `derive_intervention_status`,
`shape_conversations`, ...) so the deterministic parts are unit-testable without a
database, matching the `compute_*` style in `app/services/metrics_service.py`.
"""
from datetime import datetime, timezone

from sqlalchemy import case, func

from app.core import notification_jobs
from app.core.database import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.models.message import Message
from app.models.reroute import Reroute
from app.models.parcel import Parcel
from app.models.ticket import Ticket
from app.services import delivery_service, delivery_state

CASE_TYPE_TICKET = "ticket"
CASE_TYPE_REROUTE = "reroute"
CASE_TYPE_INTERVENTION = "intervention"
# Phase 6. A proactive notification that never went out belongs in the same feed as the
# actions that did: a customer who was never warned about their delay is an ops event,
# and the whole reason for dead-lettering was to stop that being invisible.
CASE_TYPE_NOTIFICATION_FAILURE = "notification_failure"
CASE_TYPES = (CASE_TYPE_TICKET, CASE_TYPE_REROUTE, CASE_TYPE_INTERVENTION,
              CASE_TYPE_NOTIFICATION_FAILURE)

# An Intervention is an audit row with no status column of its own — whether the
# corrective action actually worked is only known once a DeliveryAttempt resolves it
# into an InterventionOutcome. That makes its status *derived*, not stored, which is
# also exactly the "RTO prevented" signal the KPI panel reports.
INTERVENTION_STATUS_OPEN = "open"

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


# --- pure shaping helpers ------------------------------------------------

def derive_intervention_status(intervention_id: str, resolved_outcomes: dict[str, str]) -> str:
    """'delivered' / 'still_failed' once an InterventionOutcome exists for this
    intervention, otherwise 'open' (the corrective action is awaiting its next
    delivery attempt)."""
    return resolved_outcomes.get(intervention_id, INTERVENTION_STATUS_OPEN)


def _ticket_to_case(ticket: Ticket) -> dict:
    return {
        "type": CASE_TYPE_TICKET,
        "ref_id": ticket.ticket_id,
        "tracking_number": ticket.tracking_number,
        "action": "escalate",
        "status": ticket.status or "open",
        "detail": ticket.reason,
        "created_at": ticket.created_at,
    }


def _notification_failure_to_case(failure: dict) -> dict:
    """`retrying` is a transient blip — usually a DNS wobble — and resolves itself.
    `dead` means the system gave up, and nobody will be told about that delay unless a
    human acts, which is why it is in this feed rather than only in a log."""
    attempts = failure["attempts"]
    return {
        "type": CASE_TYPE_NOTIFICATION_FAILURE,
        "ref_id": f"NTF-{failure['id']:04d}",
        "tracking_number": failure["tracking_number"],
        "action": "notify",
        "status": failure["status"],
        "detail": (f"{attempts} attempt{'s' if attempts != 1 else ''} failed "
                   f"({failure['delay_reason']}): {failure['last_error'] or 'unknown error'}"),
        "created_at": failure["last_failed_at"],
    }


def _reroute_to_case(reroute: Reroute) -> dict:
    return {
        "type": CASE_TYPE_REROUTE,
        "ref_id": reroute.reroute_id,
        "tracking_number": reroute.tracking_number,
        "action": "reroute",
        "status": reroute.status or "requested",
        "detail": reroute.reason,
        "created_at": reroute.created_at,
    }


def _intervention_to_case(intervention: Intervention, resolved_outcomes: dict[str, str]) -> dict:
    return {
        "type": CASE_TYPE_INTERVENTION,
        "ref_id": intervention.intervention_id,
        "tracking_number": intervention.tracking_number,
        "action": intervention.action,
        "status": derive_intervention_status(intervention.intervention_id, resolved_outcomes),
        "detail": intervention.detail,
        "created_at": intervention.created_at,
    }


def _newest_first(cases: list[dict]) -> list[dict]:
    """Sort merged cases across three tables by recency. `created_at` is a server
    default and always populated on rows read back from Postgres; the fallback only
    guards against a None sneaking in and raising on comparison."""
    return sorted(cases, key=lambda c: c["created_at"] or _EPOCH, reverse=True)


def shape_conversations(agg_rows, last_messages_by_id: dict[int, Message]) -> list[dict]:
    """Turn the per-customer aggregate rows + their last Message into the summary
    rows the dashboard's conversation list renders."""
    conversations = []
    for row in agg_rows:
        last = last_messages_by_id.get(row.last_message_id)
        conversations.append({
            "customer_phone": row.customer_phone,
            "message_count": int(row.message_count or 0),
            "inbound_count": int(row.inbound_count or 0),
            "outbound_count": int(row.outbound_count or 0),
            "last_message_id": row.last_message_id,
            "last_activity": row.last_activity,
            "last_direction": last.direction if last else None,
            "last_body": last.body if last else None,
            "last_tracking_number": last.tracking_number if last else None,
        })
    return conversations


def _message_to_dict(message: Message) -> dict:
    return {
        "id": message.id,
        "direction": message.direction,
        "body": message.body,
        "tracking_number": message.tracking_number,
        "created_at": message.created_at,
    }


# --- queries -------------------------------------------------------------

def list_conversations(limit: int = 50) -> list[dict]:
    """One summary row per customer who has ever messaged, most recently active
    first. `last_message_id` doubles as the polling cursor for the dashboard: the
    client re-fetches a thread only when that id changes."""
    db = SessionLocal()
    try:
        agg_rows = (
            db.query(
                Message.customer_phone.label("customer_phone"),
                func.count(Message.id).label("message_count"),
                func.sum(case((Message.direction == "in", 1), else_=0)).label("inbound_count"),
                func.sum(case((Message.direction == "out", 1), else_=0)).label("outbound_count"),
                func.max(Message.id).label("last_message_id"),
                func.max(Message.created_at).label("last_activity"),
            )
            .group_by(Message.customer_phone)
            .order_by(func.max(Message.id).desc())
            .limit(limit)
            .all()
        )

        last_ids = [row.last_message_id for row in agg_rows if row.last_message_id is not None]
        last_messages_by_id = {}
        if last_ids:
            last_messages_by_id = {
                m.id: m for m in db.query(Message).filter(Message.id.in_(last_ids)).all()
            }

        return shape_conversations(agg_rows, last_messages_by_id)
    finally:
        db.close()


def get_thread(customer_phone: str, limit: int = 100, since_id: int | None = None) -> list[dict]:
    """Oldest-first message thread for one customer.

    Without `since_id` this returns the *most recent* `limit` messages (in reading
    order) — unlike `message_log.get_conversation`, which returns the oldest ones and
    is kept as-is for its existing callers. With `since_id` it returns only messages
    newer than that id, which is what the dashboard's poll loop uses so a live thread
    costs one small query per tick.
    """
    db = SessionLocal()
    try:
        query = db.query(Message).filter(Message.customer_phone == customer_phone)
        if since_id is not None:
            rows = query.filter(Message.id > since_id).order_by(Message.id.asc()).limit(limit).all()
        else:
            rows = query.order_by(Message.id.desc()).limit(limit).all()
            rows = list(reversed(rows))
        return [_message_to_dict(m) for m in rows]
    finally:
        db.close()


def _resolved_outcomes(db, intervention_ids: list[str]) -> dict[str, str]:
    """intervention_id -> 'delivered' | 'still_failed' for those that have resolved."""
    if not intervention_ids:
        return {}
    rows = (
        db.query(InterventionOutcome.intervention_id, InterventionOutcome.outcome)
        .filter(InterventionOutcome.intervention_id.in_(intervention_ids))
        .all()
    )
    return {row.intervention_id: row.outcome for row in rows}


def list_cases(case_type: str | None = None, status: str | None = None,
               limit: int = 100) -> list[dict]:
    """Tickets (escalations), reroutes, and interventions (corrective actions) as one
    normalised, newest-first feed — the dashboard renders them in a single table with
    a type filter. Each source table is limited before merging, so the merge is
    bounded even when one type dominates."""
    db = SessionLocal()
    try:
        cases: list[dict] = []

        if case_type in (None, CASE_TYPE_TICKET):
            tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).limit(limit).all()
            cases.extend(_ticket_to_case(t) for t in tickets)

        if case_type in (None, CASE_TYPE_REROUTE):
            reroutes = db.query(Reroute).order_by(Reroute.created_at.desc()).limit(limit).all()
            cases.extend(_reroute_to_case(r) for r in reroutes)

        if case_type in (None, CASE_TYPE_INTERVENTION):
            interventions = (
                db.query(Intervention).order_by(Intervention.created_at.desc()).limit(limit).all()
            )
            outcomes = _resolved_outcomes(db, [iv.intervention_id for iv in interventions])
            cases.extend(_intervention_to_case(iv, outcomes) for iv in interventions)
    finally:
        db.close()

    # Its own store (app/core/notification_jobs.py), so it is fetched outside the session
    # above rather than reaching into another module's table from here.
    if case_type in (None, CASE_TYPE_NOTIFICATION_FAILURE):
        cases.extend(_notification_failure_to_case(f)
                     for f in notification_jobs.list_failures(limit=limit))

    # Status is filtered after shaping because an intervention's status is derived,
    # not stored — it can't be pushed down into the query.
    if status:
        cases = [c for c in cases if c["status"] == status]

    return _newest_first(cases)[:limit]


# --- deliveries (Phase 6) ------------------------------------------------

#: Actionable parcels first. The pane exists to answer "what needs a delivery attempt
#: run against it", so a finished parcel sorts below one that is still in play.
_DELIVERY_SORT_PRIORITY = {
    delivery_state.STATUS_ATTEMPT_FAILED: 0,
    delivery_state.STATUS_OUT_FOR_DELIVERY: 1,
    delivery_state.STATUS_RETURNED_TO_ORIGIN: 8,
    delivery_state.STATUS_DELIVERED: 9,
}
_DELIVERY_SORT_DEFAULT = 5


def _attempt_to_dict(attempt: DeliveryAttempt) -> dict:
    return {
        "attempt_no": attempt.attempt_no,
        "outcome": attempt.outcome,
        "failure_reason": attempt.failure_reason,
        # NULL on rows written before provenance existed. Reported as-is rather than
        # defaulted, because "we don't know" is the honest answer for those.
        "source": attempt.source,
        "recorded_by": attempt.recorded_by,
        "modelled": attempt.source in delivery_service.MODELLED_SOURCES,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
    }


def _next_attempt_availability(parcel: Parcel, attempts: list[DeliveryAttempt]) -> dict:
    """Whether `POST /ops/parcels/{tn}/attempt` would be accepted right now, and why not.

    **Advisory only** — the write endpoint stays the authority and re-checks everything.
    This exists so the pane can explain a refusal *before* the operator clicks, rather
    than surfacing a bare 409: the "an attempt needs a corrective action to schedule it"
    rule is non-obvious, and a button that just fails looks broken.
    """
    if delivery_state.is_terminal(parcel.status):
        return {"allowed": False, "reason": "journey_complete",
                "detail": f"Already {parcel.status.replace('_', ' ')}."}

    if not delivery_state.can_attempt_delivery(parcel.status):
        return {"allowed": False, "reason": "not_dispatched",
                "detail": "Still at the origin — nothing has gone out with a rider yet."}

    current_attempt_no = parcel.attempt_count or 0
    if any(a.attempt_no == current_attempt_no for a in attempts):
        return {"allowed": False, "reason": "awaiting_reschedule",
                "detail": "Attempt already recorded. A reschedule or address update "
                          "has to schedule the next one."}

    return {"allowed": True, "reason": None, "detail": None}


def list_deliveries(status: str | None = None, limit: int = 50) -> list[dict]:
    """Parcels with their delivery-attempt history — the Phase 6 deliveries pane.

    Read-only, like everything else in this module: it reports the parcel state that
    `delivery_state` produced, and never computes a transition of its own.
    """
    db = SessionLocal()
    try:
        query = db.query(Parcel)
        if status:
            query = query.filter(Parcel.status == status)
        parcels = query.order_by(Parcel.expected_delivery_date.desc()).limit(limit).all()

        tracking_numbers = [p.tracking_number for p in parcels]
        attempts_by_parcel: dict[str, list[DeliveryAttempt]] = {tn: [] for tn in tracking_numbers}
        if tracking_numbers:
            # One query for every parcel's attempts rather than N — the pane polls.
            rows = (
                db.query(DeliveryAttempt)
                .filter(DeliveryAttempt.tracking_number.in_(tracking_numbers))
                .order_by(DeliveryAttempt.attempt_no.asc(), DeliveryAttempt.id.asc())
                .all()
            )
            for row in rows:
                attempts_by_parcel[row.tracking_number].append(row)

        deliveries = []
        for parcel in parcels:
            attempts = attempts_by_parcel[parcel.tracking_number]
            attempts_made = parcel.attempt_count or 0
            deliveries.append({
                "tracking_number": parcel.tracking_number,
                "customer_phone": parcel.customer_phone,
                "status": parcel.status,
                "terminal": delivery_state.is_terminal(parcel.status),
                "delay_reason": parcel.delay_reason,
                "destination_city": parcel.destination_city,
                "address_line": parcel.address_line,
                "preferred_delivery_window": parcel.preferred_delivery_window,
                "expected_delivery_date": parcel.expected_delivery_date.isoformat()
                    if parcel.expected_delivery_date else None,
                "attempt_count": attempts_made,
                "max_attempts": delivery_state.MAX_DELIVERY_ATTEMPTS,
                "attempts_remaining": delivery_state.attempts_remaining(attempts_made),
                "attempts": [_attempt_to_dict(a) for a in attempts],
                # Surfaced per parcel so the pane can mark a timeline that contains a
                # human-triggered outcome, rather than the operator having to remember.
                "has_modelled_outcome": any(
                    a.source in delivery_service.MODELLED_SOURCES for a in attempts),
                "next_attempt": _next_attempt_availability(parcel, attempts),
            })
    finally:
        db.close()

    deliveries.sort(key=lambda d: (
        _DELIVERY_SORT_PRIORITY.get(d["status"], _DELIVERY_SORT_DEFAULT),
        d["expected_delivery_date"] or "",
    ))
    return deliveries
