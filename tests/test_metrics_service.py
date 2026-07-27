"""Phase 3 — metrics_service compute_* functions are pure over plain dicts, so these
tests build small known fixtures and assert exact expected numbers, with no DB/mocking
needed (get_metrics_report itself, which does touch the DB, is exercised manually /
via the endpoint rather than unit-tested here)."""
from datetime import datetime, timezone

from app.services import metrics_service


def _interaction(**overrides) -> dict:
    fields = {
        "escalated": False,
        "resolved_by": "bot",
        "response_time_ms": 500,
        "language": "english",
        "created_at": datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),  # noon UTC
    }
    fields.update(overrides)
    return fields


# --- deflection rate --------------------------------------------------------

def test_deflection_rate_empty_is_zero():
    assert metrics_service.compute_deflection_rate([]) == 0.0


def test_deflection_rate_mix():
    interactions = [_interaction(escalated=False), _interaction(escalated=False), _interaction(escalated=True)]
    assert metrics_service.compute_deflection_rate(interactions) == 2 / 3


# --- cost per interaction ---------------------------------------------------

def test_cost_per_interaction_blends_bot_and_human():
    interactions = [_interaction(resolved_by="bot"), _interaction(resolved_by="bot"), _interaction(resolved_by="human")]
    result = metrics_service.compute_cost_per_interaction(interactions, human_cost=30, bot_cost=2)

    assert result["total_pkr"] == 34  # 2 + 2 + 30
    assert result["blended_pkr"] == round(34 / 3, 2)
    assert result["bot_only_pkr"] == 2
    assert result["human_only_pkr"] == 30


def test_cost_per_interaction_empty():
    result = metrics_service.compute_cost_per_interaction([], human_cost=30, bot_cost=2)
    assert result["total_pkr"] == 0.0
    assert result["blended_pkr"] == 0.0


# --- RTO reduction -----------------------------------------------------------

def test_rto_reduction_computes_percentage_and_savings():
    outcomes = [{"outcome": "delivered"}, {"outcome": "delivered"}, {"outcome": "still_failed"}]
    result = metrics_service.compute_rto_reduction(outcomes, rto_cost=450)

    assert result["delivered_count"] == 2
    assert result["still_failed_count"] == 1
    assert result["rto_reduction_pct"] == round(2 / 3 * 100, 1)
    assert result["pkr_saved"] == 900.0


def test_rto_reduction_no_resolved_interventions():
    result = metrics_service.compute_rto_reduction([], rto_cost=450)
    assert result["rto_reduction_pct"] == 0.0
    assert result["pkr_saved"] == 0.0


# --- response time -------------------------------------------------------

def test_response_time_avg_and_median():
    interactions = [_interaction(response_time_ms=100), _interaction(response_time_ms=200), _interaction(response_time_ms=300)]
    result = metrics_service.compute_response_time(interactions)
    assert result["avg_ms"] == 200.0
    assert result["median_ms"] == 200.0


# --- after-hours -----------------------------------------------------------

def test_after_hours_pct_flags_outside_window():
    business_hours = _interaction(created_at=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc))  # noon PKT-ish
    after_hours = _interaction(created_at=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc))      # late night UTC
    interactions = [business_hours, after_hours]

    # Force a fixed, easy-to-reason-about window in UTC directly (tz_name="UTC")
    result = metrics_service.compute_after_hours_pct(interactions, start_hour=9, end_hour=18, tz_name="UTC")
    assert result == 50.0


def test_after_hours_pct_all_within_window():
    interactions = [_interaction(created_at=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))]
    result = metrics_service.compute_after_hours_pct(interactions, start_hour=9, end_hour=18, tz_name="UTC")
    assert result == 0.0


# --- language reach ----------------------------------------------------------

def test_language_reach_pct():
    interactions = [_interaction(language="roman_urdu"), _interaction(language="english"), _interaction(language="roman_urdu")]
    result = metrics_service.compute_language_reach_pct(interactions)
    assert result == round(2 / 3 * 100, 1)


def test_language_reach_pct_empty():
    assert metrics_service.compute_language_reach_pct([]) == 0.0
