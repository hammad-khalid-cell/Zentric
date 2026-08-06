"""Inbound webhook: Meta payload parsing and the end-to-end inbound handler
(graph, rate limit, and channel all mocked)."""
import pytest

from fastapi.testclient import TestClient

from app.core import config
from app.main import app
from app.routes import whatsapp_routes
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


# --- verify-token on the handshake (Phase 6) ---------------------------------
#
# The half of webhook auth that needs nothing from Meta, so it is built and tested now.
# The X-Hub-Signature-256 check on inbound POSTs is Phase 7: it needs the App Secret,
# and HMAC written against no real signature only tests itself.

VERIFY_URL = "/webhook/whatsapp"
VERIFY_TOKEN = "meta-verify-me"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def inbound(monkeypatch):
    """Stub the graph-facing handler so the POST test asserts routing, not behaviour."""
    seen = []
    monkeypatch.setattr(whatsapp_routes, "process_inbound_message",
                        lambda phone, text: seen.append((phone, text)) or "ok")
    return seen


@pytest.fixture
def verify_token(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)


def test_handshake_echoes_the_challenge_when_the_token_matches(client, verify_token):
    response = client.get(VERIFY_URL, params={
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "1158201444",
    })
    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.parametrize("supplied", ["wrong-token", "", "meta-verify-m"])
def test_a_wrong_token_is_refused_and_echoes_nothing(client, verify_token, supplied):
    """The failure that matters: a stranger must not be able to complete the handshake
    and point their own Meta app at this URL."""
    response = client.get(VERIFY_URL, params={
        "hub.mode": "subscribe",
        "hub.verify_token": supplied,
        "hub.challenge": "1158201444",
    })
    assert response.status_code == 403
    assert "1158201444" not in response.text


def test_a_missing_token_is_refused_when_one_is_configured(client, verify_token):
    response = client.get(VERIFY_URL, params={"hub.challenge": "1158201444"})
    assert response.status_code == 403


def test_an_unexpected_hub_mode_is_refused(client, verify_token):
    """Meta always sends subscribe; anything else is not the handshake."""
    response = client.get(VERIFY_URL, params={
        "hub.mode": "unsubscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "1158201444",
    })
    assert response.status_code == 403


def test_without_a_configured_token_the_handshake_is_unchanged(client, monkeypatch):
    """The opt-in default, asserted rather than assumed: requiring a token by default
    would break the local simulator and every existing caller for no benefit while the
    provider is `mock`."""
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", None)

    response = client.get(VERIFY_URL, params={"hub.challenge": "1158201444"})
    assert response.status_code == 200
    assert response.text == "1158201444"


def test_the_verify_token_does_not_gate_inbound_messages(client, monkeypatch, inbound):
    """Scope check. `hub.verify_token` is for the *handshake* only — Meta never sends it
    on a message POST, so gating inbound on it would silently drop real traffic in
    Phase 7. Inbound is protected by the signature check, which is Phase 7 work."""
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)

    response = client.post(VERIFY_URL, json=build_text_message_payload("923001234567", "hello"))
    assert response.status_code == 200
    assert response.json()["processed"] == 1
