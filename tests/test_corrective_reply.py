"""Phase 2 proactive loop — the reply side.

Covers reply interpretation (rules + JSON parsing), the deterministic mapping from a
corrective intent to an action (the LLM interprets, the policy decides), the
ownership-guarded interpret_reply_node, pending-action resolution in action_execution,
and the routing precedence that sends a mid-intervention reply down the corrective path.
"""
from datetime import date

import pytest

from app.graph import nodes
from app.graph.nodes import (
    rule_based_corrective,
    _parse_corrective,
    interpret_reply_node,
    decision_making_node,
    action_execution_node,
)
from app.graph.decision_rules import CORRECTIVE_INTENT_TO_ACTION
from app.graph.build_graph import route_after_intent
from tests.conftest import base_state, make_parcel


# --- reply interpretation -------------------------------------------------

@pytest.mark.parametrize("message", ["Cancel it please", "mujhe nahi chahiye", "wapas bhej do"])
def test_cancel_is_caught_by_high_precision_rule(message):
    # Cancellation must never be inferred loosely — a rule catches it before the LLM.
    assert rule_based_corrective(message) == {"intent": "cancel", "address": None, "window": None}


def test_non_cancel_message_defers_to_llm():
    assert rule_based_corrective("My address is House 5, Lahore") is None


def test_parse_corrective_extracts_slots():
    raw = '{"intent": "update_address", "address": "House 5, Gulberg, Lahore", "window": null}'
    assert _parse_corrective(raw) == {
        "intent": "update_address",
        "address": "House 5, Gulberg, Lahore",
        "window": None,
    }


def test_parse_corrective_unknown_intent_becomes_unclear():
    assert _parse_corrective('{"intent": "buy_pizza"}')["intent"] == "unclear"


def test_parse_corrective_malformed_json_is_unclear():
    assert _parse_corrective("not json at all") == {"intent": "unclear", "address": None, "window": None}


# --- interpret_reply_node (ownership guard) -------------------------------

def test_interpret_reply_node_loads_owned_parcel(monkeypatch):
    parcel = make_parcel(tracking_number="TRK20250", customer_phone="923001234567")
    monkeypatch.setattr(nodes, "find_parcel", lambda tn: parcel)
    monkeypatch.setattr(nodes, "interpret_corrective_reply",
                        lambda msg: {"intent": "update_address", "address": "House 9, Karachi", "window": None})

    state = base_state(
        customer_id="923001234567",
        user_message="House 9, Karachi",
        pending_action={"id": 1, "tracking_number": "TRK20250"},
    )
    out = interpret_reply_node(state)

    assert out["retrieved_data"] == parcel
    assert out["corrective_intent"] == "update_address"
    assert out["corrective_payload"]["address"] == "House 9, Karachi"


def test_interpret_reply_node_rejects_unowned_parcel(monkeypatch):
    # Parcel owned by a different number: never act on it, never leak it.
    parcel = make_parcel(tracking_number="TRK20250", customer_phone="923009999999")
    monkeypatch.setattr(nodes, "find_parcel", lambda tn: parcel)
    # interpret must not even be consulted once ownership fails
    monkeypatch.setattr(nodes, "interpret_corrective_reply",
                        lambda msg: (_ for _ in ()).throw(AssertionError("must not interpret an unowned reply")))

    state = base_state(
        customer_id="923001234567",
        pending_action={"id": 1, "tracking_number": "TRK20250"},
    )
    out = interpret_reply_node(state)

    assert out["retrieved_data"] is None
    assert out["corrective_intent"] == "unclear"


# --- deterministic corrective decision ------------------------------------

@pytest.mark.parametrize("intent,expected", list(CORRECTIVE_INTENT_TO_ACTION.items()))
def test_corrective_intent_maps_to_configured_action(intent, expected):
    payload = {"address": "House 5, Lahore"} if intent == "update_address" else {}
    out = decision_making_node(base_state(corrective_intent=intent, corrective_payload=payload))
    assert out["decision"] == expected


def test_update_address_without_address_downgrades_to_clarify():
    out = decision_making_node(base_state(corrective_intent="update_address", corrective_payload={}))
    assert out["decision"] == "clarify"


def test_cancel_escalates_and_flags_handoff():
    out = decision_making_node(base_state(corrective_intent="cancel", corrective_payload={}))
    assert out["decision"] == "escalate"
    assert out["needs_human_handoff"] is True


# --- action_execution: pending-action resolution --------------------------

def test_successful_address_update_resolves_pending(monkeypatch):
    resolved = []
    monkeypatch.setattr(nodes, "apply_address_update",
                        lambda tn, addr, phone: {"applied": True, "new_delivery_date": date.today()})
    monkeypatch.setattr(nodes, "resolve_pending_action", lambda pid: resolved.append(pid))

    state = base_state(
        corrective_intent="update_address",
        corrective_payload={"address": "House 9, Karachi"},
        decision="update_address",
        retrieved_data=make_parcel(tracking_number="TRK20250"),
        pending_action={"id": 42, "tracking_number": "TRK20250"},
    )
    out = action_execution_node(state)

    assert out["action_taken"] == "address_updated"
    assert resolved == [42]


def test_failed_apply_keeps_pending_open(monkeypatch):
    resolved = []
    monkeypatch.setattr(nodes, "apply_reschedule",
                        lambda tn, phone, window=None: {"applied": False, "reason": "not_found_or_not_owned"})
    monkeypatch.setattr(nodes, "resolve_pending_action", lambda pid: resolved.append(pid))

    state = base_state(
        corrective_intent="reschedule",
        corrective_payload={},
        decision="reschedule",
        retrieved_data=make_parcel(tracking_number="TRK20250"),
        pending_action={"id": 42, "tracking_number": "TRK20250"},
    )
    out = action_execution_node(state)

    assert out["action_taken"] == "reschedule_failed"
    assert resolved == []  # still waiting on the customer


def test_clarify_takes_no_action_and_keeps_pending(monkeypatch):
    resolved = []
    monkeypatch.setattr(nodes, "resolve_pending_action", lambda pid: resolved.append(pid))

    state = base_state(
        corrective_intent="unclear",
        decision="clarify",
        retrieved_data=make_parcel(tracking_number="TRK20250"),
        pending_action={"id": 42, "tracking_number": "TRK20250"},
    )
    out = action_execution_node(state)

    assert out["action_taken"] is None
    assert resolved == []


def test_cancel_creates_ticket_and_resolves_pending(monkeypatch):
    resolved = []
    monkeypatch.setattr(nodes, "create_ticket",
                        lambda tracking_number, reason, decision: {"ticket_id": "TCK-0009", "already_existed": False})
    monkeypatch.setattr(nodes, "resolve_pending_action", lambda pid: resolved.append(pid))

    state = base_state(
        corrective_intent="cancel",
        decision="escalate",
        retrieved_data=make_parcel(tracking_number="TRK20250"),
        pending_action={"id": 42, "tracking_number": "TRK20250"},
    )
    out = action_execution_node(state)

    assert out["action_result"]["ticket_id"] == "TCK-0009"
    assert resolved == [42]


# --- routing precedence ---------------------------------------------------

def test_pending_action_routes_to_interpret_reply_over_intent():
    # Even a message that classifies as track_order is treated as an intervention reply.
    state = {"intent": "track_order", "pending_action": {"id": 1, "tracking_number": "TRK20250"}}
    assert route_after_intent(state) == "interpret_reply"


def test_no_pending_action_uses_normal_intent_routing():
    assert route_after_intent({"intent": "track_order", "pending_action": None}) == "data_retrieval"
