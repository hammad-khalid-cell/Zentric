"""Shared fixtures/helpers for the test suite.

These tests deliberately avoid touching the network or real infrastructure
(Groq, Postgres, Redis, Chroma). Anything that would make an external call is
monkeypatched at the boundary inside the individual tests. What's left — the
deterministic business logic — is what actually defines "the program behaves
as expected", so that's what we assert on.

That used to be a claim rather than a guarantee: an unmocked boundary would quietly
make a real call, so the suite passed on a good network and failed on a bad one
(`Temporary failure in name resolution`). The `no_network` fixture below now
*enforces* it — an unmocked boundary fails immediately, with a message naming the
problem, instead of hanging on DNS and failing at random. A test that genuinely
needs the network can opt out with `@pytest.mark.allow_network`.
"""
import os
import socket
from datetime import date, timedelta

import pytest

# LangChain/LangSmith tracing is on via .env for local development, which makes every
# graph invoke phone api.smith.langchain.com. Tests drive the graph constantly and
# must not depend on (or wait for) that. Set before any app module is imported —
# conftest is loaded first, and the tracer reads these at import.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

_NETWORK_BLOCKED = (
    "This test tried to open a real network connection. The suite is offline by "
    "design — monkeypatch the boundary (SessionLocal / safe_chat_completion / "
    "get_collection / the Redis or WhatsApp helper) in the module under test. "
    "Use @pytest.mark.allow_network only for a test whose point IS the network."
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: permit real network access in this test",
    )


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Fail fast on any real socket use, so an unmocked external boundary surfaces as
    a clear, deterministic error at the call site rather than as an intermittent
    DNS failure minutes later. FastAPI's TestClient talks to the app in-process over
    ASGI and opens no socket, so it is unaffected."""
    if "allow_network" in request.keywords:
        return

    def _blocked(*args, **kwargs):
        raise RuntimeError(_NETWORK_BLOCKED)

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def make_parcel(**overrides) -> dict:
    """A parcel dict in the exact shape parcel_data._to_dict produces."""
    parcel = {
        "tracking_number": "TRK10001",
        "customer_phone": "923001234567",
        "status": "in_transit",
        "current_hub": "Lahore Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=5),
        "expected_delivery_date": date.today() - timedelta(days=2),  # overdue by default
        "delay_reason": "vehicle_breakdown",
        "address_line": "House 1, Street 2, Karachi",
        "preferred_delivery_window": None,
        "attempt_count": 1,
    }
    parcel.update(overrides)
    return parcel


@pytest.fixture
def parcel():
    return make_parcel()


def base_state(**overrides) -> dict:
    """A minimal AgentState-shaped dict for driving individual nodes."""
    state = {
        "user_message": "hi",
        "customer_id": "923001234567",
    }
    state.update(overrides)
    return state
