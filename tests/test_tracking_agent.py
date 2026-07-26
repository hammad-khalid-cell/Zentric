"""Parcel resolution: the deterministic 0/1/many branching in _interpret_result,
and the security guard that a tracking-number lookup only returns a parcel the
requesting customer actually owns."""
from app.agents import tracking_agent
from app.agents.tracking_agent import _interpret_result, _build_tools
from tests.conftest import make_parcel


# --- _interpret_result: lookup by tracking number ------------------------

def test_interpret_found_parcel():
    parcel = make_parcel()
    out = _interpret_result("lookup_parcel_by_tracking_number", {"found": True, "parcel": parcel})
    assert out["retrieved_data"] == parcel
    assert out["clarification_needed"] is None


def test_interpret_tracking_number_not_found():
    out = _interpret_result(
        "lookup_parcel_by_tracking_number", {"found": False, "tracking_number": "TRK404"}
    )
    assert out["retrieved_data"] is None
    assert "TRK404" in out["clarification_needed"]


# --- _interpret_result: lookup by phone (0 / 1 / many) -------------------

def test_interpret_zero_parcels_asks_for_tracking_id():
    out = _interpret_result("lookup_parcels_by_phone", {"count": 0, "parcels": []})
    assert out["retrieved_data"] is None
    assert "tracking" in out["clarification_needed"].lower()


def test_interpret_single_parcel_resolves_directly():
    parcel = make_parcel()
    out = _interpret_result("lookup_parcels_by_phone", {"count": 1, "parcels": [parcel]})
    assert out["retrieved_data"] == parcel
    assert out["clarification_needed"] is None


def test_interpret_many_parcels_lists_them_for_clarification():
    p1 = make_parcel(tracking_number="TRK1001")
    p2 = make_parcel(tracking_number="TRK1002")
    out = _interpret_result("lookup_parcels_by_phone", {"count": 2, "parcels": [p1, p2]})
    assert out["retrieved_data"] is None
    assert "TRK1001" in out["clarification_needed"]
    assert "TRK1002" in out["clarification_needed"]


# --- ownership guard on the tracking-number tool -------------------------

def test_lookup_tool_returns_parcel_owned_by_caller(monkeypatch):
    owner = "923001234567"
    monkeypatch.setattr(tracking_agent, "find_parcel",
                        lambda tn: make_parcel(tracking_number=tn, customer_phone=owner))
    lookup_by_tn, _ = _build_tools(owner)
    result = lookup_by_tn.invoke({"tracking_number": "TRK55555"})
    assert result["found"] is True
    assert result["parcel"]["tracking_number"] == "TRK55555"


def test_lookup_tool_hides_parcel_owned_by_someone_else(monkeypatch):
    # A real parcel exists, but it belongs to a DIFFERENT phone number. The
    # caller must not be able to see it — found=False, no parcel leaked.
    monkeypatch.setattr(tracking_agent, "find_parcel",
                        lambda tn: make_parcel(tracking_number=tn, customer_phone="920000000000"))
    lookup_by_tn, _ = _build_tools("923001234567")
    result = lookup_by_tn.invoke({"tracking_number": "TRK55555"})
    assert result["found"] is False
    assert "parcel" not in result
