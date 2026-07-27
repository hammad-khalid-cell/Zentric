"""Inbound webhook: Meta payload parsing and the end-to-end inbound handler
(graph, rate limit, and channel all mocked)."""
import pytest

from app.routes.whatsapp_routes import build_text_message_payload, extract_text_messages
from app.services import whatsapp_inbound
from app.services.whatsapp_inbound import process_inbound_message


class FakeGraph:
    def __init__(self, result=None, boom=False):
        self.result = result or {}
        self.boom = boom
        self.invoked = False

    def invoke(self, state):
        self.invoked = True
        if self.boom:
            raise RuntimeError("graph exploded")
        return self.result


# --- payload parsing -----------------------------------------------------

def test_extract_text_messages_parses_meta_shape():
    payload = build_text_message_payload("923001234567", "TRK12345 status?")
    assert extract_text_messages(payload) == [("923001234567", "TRK12345 status?")]


def test_extract_ignores_non_text_and_malformed():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "923001234567", "type": "image"},          # non-text
        {"type": "text", "text": {"body": "no from field"}},  # missing 'from'
        {"from": "923000000000", "type": "text", "text": {"body": "ok"}},
    ]}}]}]}
    assert extract_text_messages(payload) == [("923000000000", "ok")]


def test_extract_empty_payload():
    assert extract_text_messages({}) == []


# --- process_inbound_message ---------------------------------------------

@pytest.fixture
def patched(monkeypatch):
    logged, sent, interactions = [], [], []
    monkeypatch.setattr(whatsapp_inbound, "log_message",
                        lambda phone, direction, body, tn=None: logged.append((phone, direction, body)))
    monkeypatch.setattr(whatsapp_inbound, "send_whatsapp_message",
                        lambda phone, message, tracking_number=None: sent.append((phone, message)))
    monkeypatch.setattr(whatsapp_inbound, "check_rate_limit", lambda cid: True)
    monkeypatch.setattr(whatsapp_inbound, "record_interaction",
                        lambda state, elapsed_ms: interactions.append((state, elapsed_ms)))
    return {"logged": logged, "sent": sent, "interactions": interactions}


def test_inbound_happy_path_logs_and_replies(monkeypatch, patched):
    graph = FakeGraph(result={"final_response": "Your parcel is in Lahore.", "tracking_number": "TRK1"})
    monkeypatch.setattr(whatsapp_inbound, "compiled_graph", graph)

    reply = process_inbound_message("923001234567", "where is TRK1")

    assert reply == "Your parcel is in Lahore."
    assert graph.invoked is True
    # inbound logged
    assert patched["logged"][0] == ("923001234567", whatsapp_inbound.DIRECTION_IN, "where is TRK1")
    # outbound sent
    assert patched["sent"] == [("923001234567", "Your parcel is in Lahore.")]
    # interaction recorded once, from the graph's final state
    assert len(patched["interactions"]) == 1
    assert patched["interactions"][0][0] is graph.result


def test_inbound_rate_limited_skips_graph(monkeypatch, patched):
    monkeypatch.setattr(whatsapp_inbound, "check_rate_limit", lambda cid: False)
    graph = FakeGraph(result={"final_response": "should not be produced"})
    monkeypatch.setattr(whatsapp_inbound, "compiled_graph", graph)

    reply = process_inbound_message("923001234567", "spam")

    assert reply == whatsapp_inbound.RATE_LIMIT_REPLY
    assert graph.invoked is False
    assert patched["sent"] == [("923001234567", whatsapp_inbound.RATE_LIMIT_REPLY)]


def test_inbound_graph_failure_sends_fallback(monkeypatch, patched):
    monkeypatch.setattr(whatsapp_inbound, "compiled_graph", FakeGraph(boom=True))

    reply = process_inbound_message("923001234567", "hello")

    assert "trouble processing" in reply.lower()
    assert patched["sent"][0][0] == "923001234567"
    # a graph failure is still recorded as an escalated interaction
    assert len(patched["interactions"]) == 1
    assert patched["interactions"][0][0]["needs_human_handoff"] is True
