"""Decision making is deterministic: a parcel's delay_reason maps to a fixed
action via REASON_TO_DECISION, unknown reasons escalate, and a parcel that
isn't actually overdue yields no_action. The LLM only phrases the explanation."""
from datetime import date, timedelta

import pytest

from app.graph import nodes
from app.graph.nodes import decision_making_node
from app.graph.decision_rules import REASON_TO_DECISION
from tests.conftest import base_state, make_parcel


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    # decision_making_node calls the LLM only to write a human-readable sentence;
    # stub it so tests are offline and deterministic.
    monkeypatch.setattr(nodes, "safe_chat_completion", lambda **kw: "stubbed explanation")


def _delayed_state(delay_reason):
    parcel = make_parcel(
        delay_reason=delay_reason,
        status="in_transit",
        expected_delivery_date=date.today() - timedelta(days=3),
    )
    return base_state(intent="delay_complaint", retrieved_data=parcel)


@pytest.mark.parametrize("reason,expected", list(REASON_TO_DECISION.items()))
def test_known_reason_maps_to_configured_decision(reason, expected):
    out = decision_making_node(_delayed_state(reason))
    assert out["decision"] == expected


def test_unknown_reason_defaults_to_escalate():
    # Safe default: a reason code we don't recognise must escalate, not silently notify.
    out = decision_making_node(_delayed_state("meteor_strike"))
    assert out["decision"] == "escalate"


def test_escalate_sets_human_handoff():
    out = decision_making_node(_delayed_state("shipment_damaged"))
    assert out["decision"] == "escalate"
    assert out["needs_human_handoff"] is True


def test_notify_decision_does_not_force_handoff():
    out = decision_making_node(_delayed_state("customer_unavailable"))
    assert out["decision"] == "notify"
    assert out.get("needs_human_handoff") is not True


def test_parcel_not_overdue_yields_no_action():
    parcel = make_parcel(
        status="in_transit",
        expected_delivery_date=date.today() + timedelta(days=2),  # still in the future
    )
    out = decision_making_node(base_state(intent="delay_complaint", retrieved_data=parcel))
    assert out["decision"] == "no_action"


def test_delivered_parcel_is_never_delayed():
    # Past expected date but already delivered => not a delay.
    parcel = make_parcel(
        status="delivered",
        expected_delivery_date=date.today() - timedelta(days=10),
    )
    out = decision_making_node(base_state(intent="delay_complaint", retrieved_data=parcel))
    assert out["decision"] == "no_action"


def test_node_is_noop_for_non_delay_intent():
    out = decision_making_node(base_state(intent="track_order", retrieved_data=make_parcel()))
    assert "decision" not in out


def test_node_is_noop_without_parcel():
    out = decision_making_node(base_state(intent="delay_complaint", retrieved_data=None))
    assert "decision" not in out
