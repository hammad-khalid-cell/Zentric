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

from app.core.database import SessionLocal
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.models.message import Message
from app.models.reroute import Reroute
from app.models.ticket import Ticket

CASE_TYPE_TICKET = "ticket"
CASE_TYPE_REROUTE = "reroute"
CASE_TYPE_INTERVENTION = "intervention"
CASE_TYPES = (CASE_TYPE_TICKET, CASE_TYPE_REROUTE, CASE_TYPE_INTERVENTION)

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

    # Status is filtered after shaping because an intervention's status is derived,
    # not stored — it can't be pushed down into the query.
    if status:
        cases = [c for c in cases if c["status"] == status]

    return _newest_first(cases)[:limit]
