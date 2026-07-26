"""Duplicate-action prevention: create_ticket / create_reroute_request must
return the existing record (and never insert a second one) when an open
ticket / active reroute already exists for the parcel."""
from app.services import action_service


def test_create_ticket_returns_existing_open_ticket(monkeypatch):
    monkeypatch.setattr(action_service, "find_open_ticket",
                        lambda tn: {"ticket_id": "TCK-0007", "status": "open"})
    # If dedup fails and we fall through to a DB insert, SessionLocal() would be
    # touched; make that explode so the test proves we short-circuited first.
    monkeypatch.setattr(action_service, "SessionLocal",
                        lambda: (_ for _ in ()).throw(AssertionError("must not insert a duplicate ticket")))

    result = action_service.create_ticket("TRK10001", reason="shipment_damaged", decision="escalate")
    assert result["already_existed"] is True
    assert result["ticket_id"] == "TCK-0007"


def test_create_reroute_returns_existing_active_reroute(monkeypatch):
    monkeypatch.setattr(action_service, "find_active_reroute",
                        lambda tn: {"reroute_id": "RRT-0003", "status": "requested"})
    monkeypatch.setattr(action_service, "SessionLocal",
                        lambda: (_ for _ in ()).throw(AssertionError("must not insert a duplicate reroute")))

    result = action_service.create_reroute_request("TRK10001", reason="operational_delay")
    assert result["already_existed"] is True
    assert result["reroute_id"] == "RRT-0003"
