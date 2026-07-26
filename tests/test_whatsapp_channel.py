"""WhatsApp channel abstraction: provider selection, outbound persistence via the
mock channel, and the send_whatsapp_message() dispatch seam."""
from app.core import whatsapp_client
from app.core.whatsapp_client import (
    CloudApiWhatsAppChannel,
    MockWhatsAppChannel,
    _build_channel,
    send_whatsapp_message,
)


def test_build_channel_defaults_to_mock(monkeypatch):
    monkeypatch.setattr(whatsapp_client, "WHATSAPP_PROVIDER", "mock")
    assert isinstance(_build_channel(), MockWhatsAppChannel)


def test_build_channel_selects_cloud(monkeypatch):
    monkeypatch.setattr(whatsapp_client, "WHATSAPP_PROVIDER", "cloud")
    assert isinstance(_build_channel(), CloudApiWhatsAppChannel)


def test_mock_channel_persists_outbound(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        whatsapp_client, "log_message",
        lambda phone, direction, body, tn=None: captured.update(
            phone=phone, direction=direction, body=body, tn=tn),
    )
    MockWhatsAppChannel().send("923001234567", "your parcel is on the way", "TRK10001")
    assert captured["phone"] == "923001234567"
    assert captured["direction"] == whatsapp_client.DIRECTION_OUT
    assert captured["body"] == "your parcel is on the way"
    assert captured["tn"] == "TRK10001"


def test_send_dispatches_to_configured_channel(monkeypatch):
    calls = []

    class FakeChannel:
        def send(self, phone, message, tracking_number=None):
            calls.append((phone, message, tracking_number))

    monkeypatch.setattr(whatsapp_client, "_channel", FakeChannel())
    send_whatsapp_message("923009999999", "hello", "TRK55555")
    assert calls == [("923009999999", "hello", "TRK55555")]
