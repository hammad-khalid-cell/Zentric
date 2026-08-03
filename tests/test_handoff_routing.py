"""Phase 5 — handoff routing and bot suppression.

Two questions this file answers:
  1. Does an escalation actually put a human on it? (Before Phase 5 a conversational
     escalation set a flag and left no durable record at all.)
  2. Once a human owns the thread, does the bot really go quiet — without losing the
     customer's message, and without the turn counting as a deflection?
"""
import pytest

from app.core.handoffs import STATUS_CLAIMED, STATUS_OPEN, STATUS_RESOLVED
from app.graph import nodes
from app.graph.build_graph import route_after_memory_load
from app.graph.nodes import escalation_check_node, handoff_hold_node, raise_handoff
from app.services import whatsapp_inbound
from tests.conftest import base_state, make_parcel


# --- route_after_memory_load ---------------------------------------------

def test_a_claimed_handoff_short_circuits_the_graph():
    state = base_state(human_handoff={"id": 1, "status": STATUS_CLAIMED, "claimed_by": "hammad"})
    assert route_after_memory_load(state) == "handoff_hold"


def test_an_open_handoff_does_not_suppress_the_bot():
    """Staff have been alerted but nobody has picked it up. Going silent here would
    leave the customer with nothing at all while they wait — worse than the bot
    continuing to help."""
    state = base_state(human_handoff={"id": 1, "status": STATUS_OPEN})
    assert route_after_memory_load(state) == "intent_understanding"


@pytest.mark.parametrize("handoff", [
    None,
    {"id": 1, "status": STATUS_RESOLVED},
])
def test_no_live_handoff_routes_normally(handoff):
    """A resolved handoff hands the thread back — the next message is classified
    fresh, exactly as if nothing had happened."""
    assert route_after_memory_load(base_state(human_handoff=handoff)) == "intent_understanding"


# --- handoff_hold_node ----------------------------------------------------

def test_hold_node_sends_nothing():
    """No auto-reply. A 'someone will be with you shortly' on *every* message would
    interleave bot text into a conversation a person is actively handling; the
    acknowledgement already went out on the turn that escalated."""
    out = handoff_hold_node(base_state(
        human_handoff={"id": 1, "status": STATUS_CLAIMED, "reason": "angry_language",
                       "claimed_by": "hammad"}))

    assert out["final_response"] is None
    assert out["handoff_suppressed"] is True
    assert out["action_taken"] == "suppressed_human_owns_thread"


def test_hold_node_books_the_turn_to_the_human():
    """record_interaction derives resolved_by from needs_human_handoff, so this is
    what keeps a human-owned turn out of the deflection rate."""
    out = handoff_hold_node(base_state(human_handoff={"id": 1, "status": STATUS_CLAIMED}))
    assert out["needs_human_handoff"] is True


# --- escalation raises a handoff -----------------------------------------

@pytest.fixture
def handoff_spy(monkeypatch):
    """Capture handoff creation and staff alerts at the boundary."""
    created, alerted, notified = [], [], []

    def fake_create(**kwargs):
        created.append(kwargs)
        return {"id": 7, "already_existed": kwargs.pop("_existing", False), **kwargs}

    monkeypatch.setattr(nodes, "create_handoff", fake_create)
    monkeypatch.setattr(nodes, "notify_staff", lambda h: (alerted.append(h), True)[1])
    monkeypatch.setattr(nodes, "mark_notified", lambda hid, ok: notified.append((hid, ok)))
    return {"created": created, "alerted": alerted, "notified": notified}


@pytest.fixture
def quiet_session(monkeypatch):
    monkeypatch.setattr(nodes, "save_session", lambda cid, data: None)
    monkeypatch.setattr(nodes, "get_session", lambda cid: {})
    monkeypatch.setattr(nodes, "llm_frustration_check", lambda m: False)


@pytest.mark.parametrize("message,reason", [
    ("can I talk to a human", "explicit_human_request"),
    ("this is the worst service", "angry_language"),
])
def test_conversational_escalation_raises_a_handoff(handoff_spy, quiet_session, message, reason):
    """The Phase 5 gap: this path used to set a flag, produce a soothing reply, and
    leave nothing for a human to act on — no ticket (those need a parcel), no queue
    entry, nothing."""
    out = escalation_check_node(base_state(user_message=message))

    assert out["needs_human_handoff"] is True
    assert out["escalation_reason"] == reason
    assert handoff_spy["created"][0]["reason"] == reason
    assert handoff_spy["created"][0]["customer_phone"] == "923001234567"
    assert handoff_spy["alerted"], "staff must actually be told"


def test_no_escalation_raises_no_handoff(handoff_spy, quiet_session):
    escalation_check_node(base_state(user_message="thanks, where is TRK12345"))
    assert handoff_spy["created"] == []
    assert handoff_spy["alerted"] == []


def test_staff_are_alerted_once_per_handoff_not_once_per_message(monkeypatch):
    """create_handoff is idempotent while one is live, and the alert follows the row
    rather than the message — three angry messages are one problem."""
    alerted = []
    monkeypatch.setattr(nodes, "create_handoff",
                        lambda **kw: {"id": 7, "already_existed": True, **kw})
    monkeypatch.setattr(nodes, "notify_staff", lambda h: (alerted.append(h), True)[1])
    monkeypatch.setattr(nodes, "mark_notified", lambda hid, ok: None)

    raise_handoff(base_state(), reason="angry_language")

    assert alerted == []


def test_notification_outcome_is_recorded(handoff_spy):
    raise_handoff(base_state(), reason="explicit_human_request")
    assert handoff_spy["notified"] == [(7, True)]


def test_handoff_failure_does_not_break_the_reply(monkeypatch):
    """The customer still gets answered even if the handoff store is unreachable —
    which, on this machine, is a live possibility mid-run."""
    monkeypatch.setattr(nodes, "create_handoff",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))

    assert raise_handoff(base_state(), reason="angry_language") is None


def test_delay_escalation_links_the_handoff_to_its_ticket(monkeypatch, handoff_spy):
    """The ticket is the parcel-scoped audit row; the handoff is the conversation-scoped
    queue entry that actually puts a human on it. The dashboard shows them as one case."""
    monkeypatch.setattr(nodes, "create_ticket",
                        lambda **kw: {"ticket_id": "TCK-0007", "already_existed": False})

    state = base_state(
        decision="escalate",
        retrieved_data=make_parcel(delay_reason="shipment_damaged"),
    )
    nodes.action_execution_node(state)

    assert handoff_spy["created"][0]["ticket_id"] == "TCK-0007"
    assert handoff_spy["created"][0]["reason"] == "shipment_damaged"


# --- end to end through the inbound handler ------------------------------

class _HoldingGraph:
    """Stands in for compiled_graph taking the handoff_hold path."""

    def __init__(self):
        self.invoked = False

    def invoke(self, state):
        self.invoked = True
        return {**state, "final_response": None, "handoff_suppressed": True,
                "needs_human_handoff": True, "action_taken": "suppressed_human_owns_thread"}


@pytest.fixture
def inbound(monkeypatch):
    logged, sent, interactions = [], [], []
    monkeypatch.setattr(whatsapp_inbound, "log_message",
                        lambda phone, direction, body, tn=None: logged.append((phone, direction, body)))
    monkeypatch.setattr(whatsapp_inbound, "send_whatsapp_message",
                        lambda phone, message, tn=None: sent.append((phone, message)))
    monkeypatch.setattr(whatsapp_inbound, "check_rate_limit", lambda phone: True)
    monkeypatch.setattr(whatsapp_inbound, "record_interaction",
                        lambda state, elapsed_ms: interactions.append(state))
    graph = _HoldingGraph()
    monkeypatch.setattr(whatsapp_inbound, "compiled_graph", graph)
    return {"logged": logged, "sent": sent, "interactions": interactions, "graph": graph}


def test_suppressed_turn_sends_nothing_to_the_customer(inbound):
    reply = whatsapp_inbound.process_inbound_message("923001234567", "hello?")

    assert reply is None
    assert inbound["sent"] == [], "the bot must stay silent while a human owns the thread"


def test_suppressed_turn_still_logs_the_inbound_message(inbound):
    """Critical: the human has to be able to read what the customer just said. The
    message is logged before the graph runs, so suppression never loses it."""
    whatsapp_inbound.process_inbound_message("923001234567", "are you there?")

    assert inbound["logged"] == [("923001234567", "in", "are you there?")]


def test_suppressed_turn_is_booked_as_human_resolved(inbound):
    """It must not inflate the deflection rate — a human handled this conversation."""
    whatsapp_inbound.process_inbound_message("923001234567", "hello?")

    recorded = inbound["interactions"][0]
    assert recorded["needs_human_handoff"] is True
    assert recorded["handoff_suppressed"] is True
