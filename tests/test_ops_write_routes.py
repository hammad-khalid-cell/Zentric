"""Phase 5 — the ops **write** API and the two-token split behind it.

Phase 4's argument for a single shared dashboard token was that the ops surface could
not write anything. "Mark handled" ends that, so the property being defended changes
shape: it is no longer "the dashboard is read-only" but **"a holder of the read token
alone still cannot write."** That is what the auth tests here pin down.

The rest asserts the audit discipline the write surface has to keep: every action is
attributed to a named actor, and an inapplicable transition fails loudly with a 409
rather than silently rewriting a timestamp.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import auth, config, handoffs
from app.services import delivery_service
from app.core.handoffs import STATUS_CLAIMED, STATUS_OPEN, STATUS_RESOLVED
from app.main import app

READ_TOKEN = "read-me"
WRITE_TOKEN = "write-me"


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", READ_TOKEN)
    monkeypatch.setattr(config, "DASHBOARD_WRITE_TOKEN", WRITE_TOKEN)


@pytest.fixture
def client(tokens):
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    """Replace the handoff store so the routes are tested without a database."""
    calls = []

    def fake_claim(handoff_id, actor):
        calls.append(("claim", handoff_id, actor))
        return {"id": handoff_id, "status": STATUS_CLAIMED, "claimed_by": actor}

    def fake_resolve(handoff_id, actor, note=None):
        calls.append(("resolve", handoff_id, actor, note))
        return {"id": handoff_id, "status": STATUS_RESOLVED, "resolved_by": actor,
                "resolution_note": note}

    monkeypatch.setattr(handoffs, "claim_handoff", fake_claim)
    monkeypatch.setattr(handoffs, "resolve_handoff", fake_resolve)
    return calls


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# --- the two-token split -------------------------------------------------

def test_read_token_cannot_write(client, store):
    """The load-bearing test for the Phase 4 -> 5 auth change. If this ever passes with
    a 200, the read token has silently become a write token."""
    response = client.post("/ops/handoffs/1/claim", json={"actor": "hammad"},
                           headers=auth_header(READ_TOKEN))
    assert response.status_code == 401
    assert store == [], "nothing may be mutated by a read-token request"


def test_write_token_can_write(client, store):
    response = client.post("/ops/handoffs/1/claim", json={"actor": "hammad"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 200
    assert response.json()["handoff"]["status"] == STATUS_CLAIMED


def test_write_token_is_also_accepted_for_reads(monkeypatch):
    """A deployment handing one credential to one ops lead shouldn't have to manage
    two. The write token is strictly more privileged, so it reads too."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", READ_TOKEN)
    monkeypatch.setattr(config, "DASHBOARD_WRITE_TOKEN", WRITE_TOKEN)

    auth.require_dashboard_token(authorization=f"Bearer {WRITE_TOKEN}")  # must not raise


def test_writes_fail_closed_when_the_write_token_is_unset(monkeypatch):
    """Unset means the ops API is exactly as read-only as it was in Phase 4 — a 503,
    never an open door."""
    monkeypatch.setattr(config, "DASHBOARD_WRITE_TOKEN", None)

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_write_token(authorization=f"Bearer {WRITE_TOKEN}")

    assert exc.value.status_code == 503
    assert "read-only" in exc.value.detail


@pytest.mark.parametrize("header", [
    None,
    "write-me",             # bare token, no scheme
    "Basic write-me",       # wrong scheme
    "Bearer ",              # scheme with no token
    "Bearer nonsense",      # right scheme, wrong token
])
def test_bad_write_authorization_rejected(monkeypatch, header):
    monkeypatch.setattr(config, "DASHBOARD_WRITE_TOKEN", WRITE_TOKEN)

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_write_token(authorization=header)

    assert exc.value.status_code == 401


def test_read_endpoints_still_fail_closed_without_the_read_token(monkeypatch):
    """Adding a second token must not have loosened the original gate."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", None)
    monkeypatch.setattr(config, "DASHBOARD_WRITE_TOKEN", WRITE_TOKEN)

    with pytest.raises(HTTPException) as exc:
        auth.require_dashboard_token(authorization=f"Bearer {WRITE_TOKEN}")

    assert exc.value.status_code == 503


# --- attribution ---------------------------------------------------------

def test_actor_is_required(client, store):
    """There is no user table yet, so an explicit name is the honest way to record who
    acted. An audit row reading 'ops' would be worse than no claim at all."""
    response = client.post("/ops/handoffs/1/claim", json={},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 422
    assert store == []


@pytest.mark.parametrize("actor", ["", "   "])
def test_blank_actor_is_rejected(client, store, actor):
    response = client.post("/ops/handoffs/1/claim", json={"actor": actor},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 422
    assert store == []


def test_actor_is_trimmed_before_it_is_recorded(client, store):
    client.post("/ops/handoffs/1/claim", json={"actor": "  hammad  "},
                headers=auth_header(WRITE_TOKEN))
    assert store == [("claim", 1, "hammad")]


def test_resolution_note_is_carried_through(client, store):
    response = client.post("/ops/handoffs/4/resolve",
                           json={"actor": "hammad", "note": "Called the customer."},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 200
    assert store == [("resolve", 4, "hammad", "Called the customer.")]


# --- transitions ---------------------------------------------------------

def test_claim_reports_that_the_bot_is_now_suppressed(client, store):
    """The response says so explicitly — this is the transition that silences the bot,
    and the operator clicking it should be told that in the same breath."""
    response = client.post("/ops/handoffs/1/claim", json={"actor": "hammad"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.json()["bot_suppressed"] is True


def test_resolve_reports_that_the_bot_is_back(client, store):
    response = client.post("/ops/handoffs/1/resolve", json={"actor": "hammad"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.json()["bot_suppressed"] is False


@pytest.mark.parametrize("action", ["claim", "resolve"])
def test_inapplicable_transition_is_a_409(client, monkeypatch, action):
    """Loudly, not silently: a repeat click must not rewrite claimed_at/resolved_at."""
    def boom(handoff_id, actor, *a, **k):
        raise handoffs.HandoffTransitionError(
            f"Handoff {handoff_id} is 'resolved'.", current_status=STATUS_RESOLVED)

    monkeypatch.setattr(handoffs, f"{action}_handoff", boom)

    response = client.post(f"/ops/handoffs/1/{action}", json={"actor": "hammad"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 409
    assert "resolved" in response.json()["detail"]


def test_handoff_id_must_be_positive(client, store):
    response = client.post("/ops/handoffs/0/claim", json={"actor": "hammad"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 422


# --- the read side of the queue ------------------------------------------

def test_handoff_queue_is_readable_with_the_read_token(client, monkeypatch):
    monkeypatch.setattr(handoffs, "list_handoffs",
                        lambda status=None, limit=50: [{"id": 1, "status": STATUS_OPEN}])

    response = client.get("/ops/handoffs", headers=auth_header(READ_TOKEN))
    assert response.status_code == 200
    assert response.json()["handoffs"][0]["status"] == STATUS_OPEN


def test_handoff_queue_rejects_an_unknown_status(client):
    response = client.get("/ops/handoffs?status=banana", headers=auth_header(READ_TOKEN))
    assert response.status_code == 400


def test_ops_read_router_declares_no_write_methods():
    """Structural guard on the invariant ops_routes.py documents: that file is GETs
    only, apart from the pure-computation ROI simulate. If a POST that mutates ever
    lands there, it would inherit the *read* token's gate — this fails first."""
    from app.routes import ops_routes

    mutating = [
        (route.path, route.methods)
        for route in ops_routes.router.routes
        if route.methods - {"GET", "HEAD", "OPTIONS"} and route.path != "/ops/roi/simulate"
    ]
    assert mutating == []


# --- the mock delivery system (Phase 6) ----------------------------------
#
# This endpoint stands in for the courier's own system reporting an attempt back. It
# writes a row that feeds "RTO prevented", so on top of the auth and attribution rules
# above it has to record *where the outcome came from* — a headline number a human can
# move by clicking has to be able to say which of its inputs were clicked.

ATTEMPT_URL = "/ops/parcels/TRK20250/attempt"


@pytest.fixture
def attempts(monkeypatch):
    """Replace the delivery service so the route is tested without a database."""
    calls = []

    def fake_record(tracking_number, outcome, failure_reason=None, *, source, recorded_by=None):
        calls.append({"tracking_number": tracking_number, "outcome": outcome,
                      "failure_reason": failure_reason, "source": source,
                      "recorded_by": recorded_by})
        return {"recorded": True, "delivery_attempt_id": 7, "attempt_no": 1,
                "status": "delivered", "previous_status": "out_for_delivery",
                "status_changed": True, "attempts_remaining": 2,
                "intervention_outcome_id": None}

    monkeypatch.setattr(delivery_service, "record_attempt_outcome", fake_record)
    return calls


def test_read_token_cannot_record_a_delivery_attempt(client, attempts):
    """The load-bearing auth test, extended to the endpoint that moves the RTO number.
    A holder of the read token alone must not be able to manufacture a delivery."""
    response = client.post(ATTEMPT_URL, json={"actor": "hammad", "outcome": "success"},
                           headers=auth_header(READ_TOKEN))
    assert response.status_code == 401
    assert attempts == []


def test_write_token_records_the_attempt(client, attempts):
    response = client.post(ATTEMPT_URL, json={"actor": "hammad", "outcome": "success"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 200
    assert response.json()["attempt"]["status"] == "delivered"


def test_the_outcome_is_tagged_as_console_triggered(client, attempts):
    """Provenance is the whole point: this row must never be mistakable for an outcome
    the system observed, and `ops_console` is in delivery_service.MODELLED_SOURCES."""
    client.post(ATTEMPT_URL, json={"actor": "hammad", "outcome": "success"},
                headers=auth_header(WRITE_TOKEN))

    assert attempts[0]["source"] == delivery_service.SOURCE_OPS_CONSOLE
    assert attempts[0]["source"] in delivery_service.MODELLED_SOURCES
    assert attempts[0]["recorded_by"] == "hammad"


def test_attempt_actor_is_required_and_trimmed(client, attempts):
    assert client.post(ATTEMPT_URL, json={"outcome": "success"},
                       headers=auth_header(WRITE_TOKEN)).status_code == 422
    assert client.post(ATTEMPT_URL, json={"actor": "   ", "outcome": "success"},
                       headers=auth_header(WRITE_TOKEN)).status_code == 422

    client.post(ATTEMPT_URL, json={"actor": "  hammad  ", "outcome": "success"},
                headers=auth_header(WRITE_TOKEN))
    assert attempts[-1]["recorded_by"] == "hammad"


@pytest.mark.parametrize("outcome", ["delivered", "maybe", "", "SUCCESS"])
def test_only_the_two_modelled_outcomes_are_accepted(client, attempts, outcome):
    """The caller reports an attempt; it does not get to invent a parcel state. Anything
    outside success/failed is refused at the edge rather than reaching the state machine."""
    response = client.post(ATTEMPT_URL, json={"actor": "hammad", "outcome": outcome},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == 422
    assert attempts == []


def test_failure_reason_is_dropped_on_a_success(client, attempts):
    """A success carrying a failure reason would be self-contradictory in the history."""
    client.post(ATTEMPT_URL,
                json={"actor": "hammad", "outcome": "success",
                      "failure_reason": "customer_unavailable"},
                headers=auth_header(WRITE_TOKEN))
    assert attempts[0]["failure_reason"] is None


def test_failure_reason_is_kept_on_a_failure(client, attempts):
    client.post(ATTEMPT_URL,
                json={"actor": "hammad", "outcome": "failed",
                      "failure_reason": "customer_unavailable"},
                headers=auth_header(WRITE_TOKEN))
    assert attempts[0]["failure_reason"] == "customer_unavailable"


@pytest.mark.parametrize("reason,expected_status", [
    ("parcel_not_found", 404),
    ("parcel_journey_complete", 409),
    ("duplicate_attempt", 409),
])
def test_refusals_map_to_the_right_status(client, monkeypatch, reason, expected_status):
    """Each refusal is a state conflict, not a malformed request — and a finished parcel
    must not quietly accept another attempt."""
    monkeypatch.setattr(
        delivery_service, "record_attempt_outcome",
        lambda *a, **k: {"recorded": False, "reason": reason, "status": "delivered"},
    )
    response = client.post(ATTEMPT_URL, json={"actor": "hammad", "outcome": "failed"},
                           headers=auth_header(WRITE_TOKEN))
    assert response.status_code == expected_status
    assert response.json()["detail"]
