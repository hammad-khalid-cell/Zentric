"""Shared fixtures/helpers for the test suite.

These tests deliberately avoid touching the network or real infrastructure
(Groq, Postgres, Redis, Chroma). Anything that would make an external call is
monkeypatched at the boundary inside the individual tests. What's left — the
deterministic business logic — is what actually defines "the program behaves
as expected", so that's what we assert on.
"""
from datetime import date, timedelta

import pytest


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
