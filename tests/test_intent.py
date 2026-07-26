"""Intent classification: tracking-number extraction, rule-based routing,
and the pending-clarification continuation flow in intent_understanding_node."""
import pytest

from app.graph import nodes
from app.graph.nodes import (
    extract_tracking_number,
    _contains_keyword,
    rule_based_intent,
    intent_understanding_node,
    DELAY_KEYWORDS,
)
from tests.conftest import base_state


# --- extract_tracking_number ---------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("TRK12345 status?", "TRK12345"),
    ("where is trk12345", "TRK12345"),          # lower-cased input normalised to upper
    ("my parcel AB1234 please", "AB1234"),
    ("check ABCDE1234567890", "ABCDE1234567890"),  # 5 letters + exactly 10 digits: full match
])
def test_extract_tracking_number_matches(message, expected):
    assert extract_tracking_number(message) == expected


@pytest.mark.parametrize("message", [
    "where is my order",   # 'order' has no digits, must not be read as a tracking id
    "hello there",
    "12345",               # digits with no 2-5 letter prefix
    "A1",                  # too short
    "AB123456789012",      # 12 digits exceeds the \d{4,10} cap, no boundary to anchor
])
def test_extract_tracking_number_none(message):
    assert extract_tracking_number(message) is None


# --- _contains_keyword word-boundary safety ------------------------------

def test_contains_keyword_respects_word_boundary():
    # "der" is a delay keyword (Roman Urdu for "late") but must not match
    # inside "order" — this is the exact false-positive the \b guards against.
    assert _contains_keyword("order placed", DELAY_KEYWORDS) is False
    assert _contains_keyword("thora der ho gai", DELAY_KEYWORDS) is True


# --- rule_based_intent ----------------------------------------------------

def test_delay_keyword_wins_over_tracking_number():
    # A message with BOTH a delay word and a tracking number is a delay complaint,
    # because delay keywords are checked first.
    assert rule_based_intent("TRK12345 is delayed") == "delay_complaint"


def test_rule_based_intent_track_order():
    assert rule_based_intent("status of ABC1234") == "track_order"


def test_rule_based_intent_faq():
    assert rule_based_intent("what are your working hours") == "faq"


def test_rule_based_intent_returns_none_when_no_rule_matches():
    # Falls through to None so the node knows to invoke the LLM fallback.
    assert rule_based_intent("hmm okay then") is None


# --- intent_understanding_node -------------------------------------------

def test_node_uses_rule_and_skips_llm(monkeypatch):
    # When a rule matches, the (expensive) LLM classifier must NOT be called.
    monkeypatch.setattr(nodes, "llm_intent",
                        lambda m: pytest.fail("llm_intent should not run when a rule matches"))
    out = intent_understanding_node(base_state(user_message="my parcel is late"))
    assert out["intent"] == "delay_complaint"


def test_node_falls_back_to_llm(monkeypatch):
    called = {}
    def fake_llm(message):
        called["msg"] = message
        return "faq"
    monkeypatch.setattr(nodes, "llm_intent", fake_llm)
    out = intent_understanding_node(base_state(user_message="tell me about something vague"))
    assert out["intent"] == "faq"
    assert called["msg"] == "tell me about something vague"


def test_pending_clarification_with_tracking_number_is_continuation(monkeypatch):
    # If we asked "which parcel?" and the reply carries a tracking number,
    # treat it as a track_order continuation without reclassifying.
    monkeypatch.setattr(nodes, "llm_intent",
                        lambda m: pytest.fail("should not classify a clarification reply"))
    state = base_state(user_message="it's TRK99999", pending_clarification={"type": "clarification"})
    out = intent_understanding_node(state)
    assert out["intent"] == "track_order"
    assert out["tracking_number"] == "TRK99999"


def test_pending_clarification_without_tracking_number_falls_through(monkeypatch):
    # A clarification reply with no tracking number should NOT be forced to
    # track_order — it falls through to normal classification.
    monkeypatch.setattr(nodes, "llm_intent", lambda m: "unclear")
    state = base_state(user_message="huh?", pending_clarification={"type": "clarification"})
    out = intent_understanding_node(state)
    assert out["intent"] == "unclear"
