"""Frustration / human-handoff detection: rule-based checks first, LLM tone
check only as a fallback, plus the repeated-message counter in the node."""
import pytest

from app.graph import nodes
from app.graph.nodes import rule_based_frustration_check, escalation_check_node
from tests.conftest import base_state


# --- rule_based_frustration_check ----------------------------------------

def test_explicit_human_request():
    assert rule_based_frustration_check("can I talk to a human", 1) == "explicit_human_request"


def test_repeated_query_threshold():
    assert rule_based_frustration_check("where is my parcel", 3) == "repeated_query"
    assert rule_based_frustration_check("where is my parcel", 2) is None


def test_angry_language():
    assert rule_based_frustration_check("this is the worst service", 1) == "angry_language"


def test_no_frustration_signal():
    assert rule_based_frustration_check("thanks, where is TRK12345", 1) is None


# --- escalation_check_node repeat counting -------------------------------

@pytest.fixture
def captured_session(monkeypatch):
    """Stub Redis-backed session helpers; capture what save_session writes."""
    store = {"saved": None}
    monkeypatch.setattr(nodes, "save_session", lambda cid, data: store.__setitem__("saved", data))
    # Default: no LLM-detected tone unless a test overrides it.
    monkeypatch.setattr(nodes, "llm_frustration_check", lambda m: False)
    return store


def test_repeated_identical_message_escalates(monkeypatch, captured_session):
    # Third identical message in a row (prior repeat_count=2) trips repeated_query.
    monkeypatch.setattr(nodes, "get_session",
                        lambda cid: {"last_message": "where is my parcel", "repeat_count": 2})
    out = escalation_check_node(base_state(user_message="where is my parcel"))
    assert out["needs_human_handoff"] is True
    assert out["escalation_reason"] == "repeated_query"
    assert captured_session["saved"]["repeat_count"] == 3


def test_different_message_resets_repeat_count(monkeypatch, captured_session):
    monkeypatch.setattr(nodes, "get_session",
                        lambda cid: {"last_message": "old question", "repeat_count": 2})
    out = escalation_check_node(base_state(user_message="a brand new question"))
    assert out.get("needs_human_handoff") is not True
    assert captured_session["saved"]["repeat_count"] == 1


def test_llm_fallback_used_when_rules_find_nothing(monkeypatch, captured_session):
    monkeypatch.setattr(nodes, "get_session", lambda cid: {})
    monkeypatch.setattr(nodes, "llm_frustration_check", lambda m: True)
    out = escalation_check_node(base_state(user_message="oh great, another delay"))
    assert out["needs_human_handoff"] is True
    assert out["escalation_reason"] == "tone_detected"


def test_rules_short_circuit_before_llm(monkeypatch, captured_session):
    # If a rule already fires, the LLM tone check must not run (cost control).
    monkeypatch.setattr(nodes, "get_session", lambda cid: {})
    monkeypatch.setattr(nodes, "llm_frustration_check",
                        lambda m: pytest.fail("LLM should not run when a rule matched"))
    out = escalation_check_node(base_state(user_message="connect me to an agent"))
    assert out["escalation_reason"] == "explicit_human_request"
