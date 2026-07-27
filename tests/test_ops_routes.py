"""Phase 4 — the read-only ops API: its auth gate and the read/shaping logic behind it.

Two things are worth asserting hard here. First, the auth gate *fails closed* — an
unauthenticated per-customer read would be the ownership oracle docs/PROJECT_PLAN.md
§5.3 forbids, so "no token configured" must mean "serve nothing", not "serve
everything". Second, an intervention's status is derived (it has no status column),
so the derivation and the post-shaping status filter are real deterministic logic.

Database access is monkeypatched at the SessionLocal boundary with the FakeDB pattern
used in tests/test_delivery_service.py.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core import auth, config
from app.models.intervention import Intervention
from app.models.message import Message
from app.models.reroute import Reroute
from app.models.ticket import Ticket
from app.routes import ops_routes
from app.services import ops_read

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def ago(minutes: int) -> datetime:
    return NOW - timedelta(minutes=minutes)


# --- auth gate -----------------------------------------------------------

def test_unset_token_fails_closed_with_503(monkeypatch):
    """No DASHBOARD_TOKEN configured must refuse to serve, not default open."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", None)

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_token(authorization="Bearer anything")

    assert exc.value.status_code == 503


def test_missing_authorization_header_rejected(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "s3cret")

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_token(authorization=None)

    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", [
    "s3cret",              # bare token, no scheme
    "Basic s3cret",        # wrong scheme
    "Bearer ",             # scheme with no token
    "Bearer wrong",        # right scheme, wrong token
])
def test_bad_authorization_headers_rejected(monkeypatch, header):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "s3cret")

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_token(authorization=header)

    assert exc.value.status_code == 401


@pytest.mark.parametrize("header", ["Bearer s3cret", "bearer s3cret"])
def test_valid_token_accepted(monkeypatch, header):
    """Scheme is case-insensitive per RFC 7235; the token itself is not."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "s3cret")

    assert auth.require_dashboard_token(authorization=header) is None


# --- fakes ---------------------------------------------------------------

class _FakeQuery:
    def __init__(self, rows, on_filter=None):
        self._rows = rows
        self._on_filter = on_filter

    def filter(self, *a, **k):
        if self._on_filter:
            self._on_filter()
        return self

    def filter_by(self, **kw):
        return self

    def order_by(self, *a, **k):
        return self

    def group_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _OutcomeRow:
    def __init__(self, intervention_id, outcome):
        self.intervention_id = intervention_id
        self.outcome = outcome


class FakeDB:
    """Routes db.query(X) by its first entity: the model classes return their canned
    lists, and the column select (InterventionOutcome.intervention_id, .outcome)
    returns the resolved-outcome rows."""

    def __init__(self, tickets=None, reroutes=None, interventions=None,
                 outcomes=None, messages=None):
        self.tickets = tickets or []
        self.reroutes = reroutes or []
        self.interventions = interventions or []
        self.outcomes = outcomes or []
        self.messages = messages or []
        self.queried = []
        self.filter_calls = 0
        self.closed = False

    def query(self, *entities):
        target = entities[0]
        self.queried.append(target)
        if target is Ticket:
            rows = self.tickets
        elif target is Reroute:
            rows = self.reroutes
        elif target is Intervention:
            rows = self.interventions
        elif target is Message:
            rows = self.messages
        else:
            rows = self.outcomes
        return _FakeQuery(rows, on_filter=self._count_filter)

    def _count_filter(self):
        self.filter_calls += 1

    def close(self):
        self.closed = True


@pytest.fixture
def use_fake_db(monkeypatch):
    def _install(db):
        monkeypatch.setattr(ops_read, "SessionLocal", lambda: db)
        return db
    return _install


def make_ticket(ticket_id="TKT-1", **kw):
    defaults = dict(tracking_number="TRK10001", reason="unreachable",
                    decision="escalate", status="open", created_at=ago(10))
    defaults.update(kw)
    return Ticket(ticket_id=ticket_id, **defaults)


def make_reroute(reroute_id="RRT-1", **kw):
    defaults = dict(tracking_number="TRK10002", reason="incorrect_address",
                    status="requested", created_at=ago(20))
    defaults.update(kw)
    return Reroute(reroute_id=reroute_id, **defaults)


def make_intervention(intervention_id="INT-1", **kw):
    defaults = dict(tracking_number="TRK10003", action="update_address",
                    detail="address updated", created_at=ago(5))
    defaults.update(kw)
    return Intervention(intervention_id=intervention_id, **defaults)


def make_message(id, direction="in", body="hi", **kw):
    defaults = dict(customer_phone="923001234567", tracking_number=None, created_at=ago(id))
    defaults.update(kw)
    return Message(id=id, direction=direction, body=body, **defaults)


# --- derived intervention status ----------------------------------------

def test_intervention_without_outcome_is_open():
    assert ops_read.derive_intervention_status("INT-1", {}) == "open"


@pytest.mark.parametrize("outcome", ["delivered", "still_failed"])
def test_intervention_status_follows_its_outcome(outcome):
    assert ops_read.derive_intervention_status("INT-1", {"INT-1": outcome}) == outcome


# --- list_cases ----------------------------------------------------------

def test_list_cases_merges_all_three_types_newest_first(use_fake_db):
    use_fake_db(FakeDB(
        tickets=[make_ticket(created_at=ago(30))],
        reroutes=[make_reroute(created_at=ago(20))],
        interventions=[make_intervention(created_at=ago(1))],
        outcomes=[_OutcomeRow("INT-1", "delivered")],
    ))

    cases = ops_read.list_cases()

    assert [c["type"] for c in cases] == ["intervention", "reroute", "ticket"]
    assert [c["ref_id"] for c in cases] == ["INT-1", "RRT-1", "TKT-1"]
    # Normalised shape is identical across the three source tables.
    assert all(set(c) == {"type", "ref_id", "tracking_number", "action", "status",
                          "detail", "created_at"} for c in cases)
    assert cases[0]["status"] == "delivered"   # derived from its outcome
    assert cases[0]["action"] == "update_address"
    assert cases[1]["action"] == "reroute"
    assert cases[2]["action"] == "escalate"


def test_list_cases_type_filter_skips_other_tables(use_fake_db):
    db = use_fake_db(FakeDB(
        tickets=[make_ticket()],
        reroutes=[make_reroute()],
        interventions=[make_intervention()],
    ))

    cases = ops_read.list_cases(case_type="ticket")

    assert [c["type"] for c in cases] == ["ticket"]
    assert Reroute not in db.queried and Intervention not in db.queried


def test_list_cases_status_filter_applies_to_derived_intervention_status(use_fake_db):
    use_fake_db(FakeDB(
        interventions=[
            make_intervention("INT-1", created_at=ago(3)),
            make_intervention("INT-2", created_at=ago(2)),
            make_intervention("INT-3", created_at=ago(1)),
        ],
        outcomes=[_OutcomeRow("INT-1", "delivered"), _OutcomeRow("INT-2", "still_failed")],
    ))

    open_cases = ops_read.list_cases(case_type="intervention", status="open")

    assert [c["ref_id"] for c in open_cases] == ["INT-3"]


def test_list_cases_respects_limit_after_merging(use_fake_db):
    use_fake_db(FakeDB(
        tickets=[make_ticket(created_at=ago(30))],
        reroutes=[make_reroute(created_at=ago(20))],
        interventions=[make_intervention(created_at=ago(1))],
    ))

    assert len(ops_read.list_cases(limit=2)) == 2


def test_list_cases_closes_its_session(use_fake_db):
    db = use_fake_db(FakeDB(tickets=[make_ticket()]))

    ops_read.list_cases()

    assert db.closed


# --- get_thread ----------------------------------------------------------

def test_get_thread_returns_newest_messages_in_reading_order(use_fake_db):
    # The DB hands back newest-first (order_by id desc); the thread must read oldest-first.
    use_fake_db(FakeDB(messages=[make_message(3), make_message(2), make_message(1)]))

    thread = ops_read.get_thread("923001234567", limit=3)

    assert [m["id"] for m in thread] == [1, 2, 3]


def test_get_thread_since_id_keeps_ascending_order_and_filters(use_fake_db):
    db = use_fake_db(FakeDB(messages=[make_message(4), make_message(5)]))

    thread = ops_read.get_thread("923001234567", since_id=3)

    assert [m["id"] for m in thread] == [4, 5]
    assert db.filter_calls == 2   # customer_phone, then id > since_id


def test_get_thread_shapes_message_rows(use_fake_db):
    use_fake_db(FakeDB(messages=[make_message(1, direction="out", body="Your parcel is delayed",
                                              tracking_number="TRK10001")]))

    (message,) = ops_read.get_thread("923001234567")

    assert message == {
        "id": 1,
        "direction": "out",
        "body": "Your parcel is delayed",
        "tracking_number": "TRK10001",
        "created_at": ago(1),
    }


# --- conversation summaries ---------------------------------------------

class _AggRow:
    def __init__(self, customer_phone, message_count, inbound_count, outbound_count,
                 last_message_id, last_activity):
        self.customer_phone = customer_phone
        self.message_count = message_count
        self.inbound_count = inbound_count
        self.outbound_count = outbound_count
        self.last_message_id = last_message_id
        self.last_activity = last_activity


def test_shape_conversations_joins_the_last_message():
    rows = [_AggRow("923001234567", 5, 2, 3, 42, NOW)]
    last = {42: make_message(42, direction="out", body="Rescheduled for tomorrow",
                             tracking_number="TRK10001")}

    (summary,) = ops_read.shape_conversations(rows, last)

    assert summary == {
        "customer_phone": "923001234567",
        "message_count": 5,
        "inbound_count": 2,
        "outbound_count": 3,
        "last_message_id": 42,
        "last_activity": NOW,
        "last_direction": "out",
        "last_body": "Rescheduled for tomorrow",
        "last_tracking_number": "TRK10001",
    }


def test_shape_conversations_tolerates_a_missing_last_message():
    rows = [_AggRow("923001234567", 0, 0, 0, None, None)]

    (summary,) = ops_read.shape_conversations(rows, {})

    assert summary["last_body"] is None
    assert summary["message_count"] == 0


# --- route-level validation ---------------------------------------------

def test_cases_route_rejects_unknown_type():
    with pytest.raises(HTTPException) as exc:
        ops_routes.list_cases(type="banana")

    assert exc.value.status_code == 400


@pytest.mark.parametrize("phone", ["not-a-phone", "12", "9230012345678901234", ""])
def test_conversation_route_rejects_malformed_phone(phone):
    with pytest.raises(HTTPException) as exc:
        ops_routes.get_conversation(phone)

    assert exc.value.status_code == 400


def test_conversation_route_reports_latest_id_as_the_next_cursor(monkeypatch):
    monkeypatch.setattr(ops_read, "get_thread",
                        lambda *a, **k: [{"id": 7}, {"id": 9}])

    result = ops_routes.get_conversation("923001234567")

    assert result["latest_id"] == 9
    assert result["customer_phone"] == "923001234567"


def test_conversation_route_keeps_the_cursor_when_nothing_is_new(monkeypatch):
    """An empty since_id poll must not reset the client's cursor to None."""
    monkeypatch.setattr(ops_read, "get_thread", lambda *a, **k: [])

    result = ops_routes.get_conversation("923001234567", since_id=12)

    assert result["latest_id"] == 12
