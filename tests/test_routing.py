"""Graph edge routing: the two conditional-edge functions that decide the
path through the LangGraph state machine."""
import pytest

from app.graph.build_graph import route_after_intent, route_after_retrieval


@pytest.mark.parametrize("intent,expected", [
    ("track_order", "data_retrieval"),
    ("delay_complaint", "data_retrieval"),
    ("faq", "faq_node"),
    ("unclear", "response_generation"),
    (None, "response_generation"),
])
def test_route_after_intent(intent, expected):
    assert route_after_intent({"intent": intent}) == expected


def test_route_after_retrieval_clarification_wins():
    # A pending clarification short-circuits straight to a reply, even for a
    # delay complaint that would otherwise go to decision_making.
    state = {"intent": "delay_complaint", "clarification_needed": "which parcel?"}
    assert route_after_retrieval(state) == "response_generation"


def test_route_after_retrieval_delay_goes_to_decision():
    state = {"intent": "delay_complaint", "clarification_needed": None}
    assert route_after_retrieval(state) == "decision_making"


def test_route_after_retrieval_track_order_goes_to_response():
    state = {"intent": "track_order", "clarification_needed": None}
    assert route_after_retrieval(state) == "response_generation"
