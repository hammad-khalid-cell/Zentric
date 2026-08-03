"""The backdated demo history generator.

`plan_history` is pure (seeded RNG in, plain dicts out), so the part that matters —
does the generated data actually give the trend chart a shape — is testable with no
database. The load-bearing test is `test_plan_produces_a_non_flat_trend_series`: it
feeds the plan straight into the real `compute_daily_series` the dashboard uses and
asserts every bucket is populated, which is precisely what was broken.
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import config
from app.services.metrics_service import compute_daily_series
from app.tools.seed_demo_history import (
    DEMO_PHONE_PREFIX,
    DEMO_TRACKING_PREFIX,
    plan_history,
)

TZ = ZoneInfo(config.BUSINESS_HOURS_TIMEZONE)
END = date(2026, 7, 28)  # a Tuesday, so a 14-day window spans two full weekends


@pytest.fixture
def plan():
    return plan_history(days=14, seed=42, end_date=END)


def test_plan_is_deterministic_for_a_seed():
    """Same seed, same data — so a demo can be rebuilt exactly, and this suite isn't
    asserting against a moving target."""
    first = plan_history(days=7, seed=99, end_date=END)
    second = plan_history(days=7, seed=99, end_date=END)
    assert first == second

    different = plan_history(days=7, seed=100, end_date=END)
    assert different != first


def test_history_ends_yesterday_and_covers_the_window(plan):
    """Today is left alone: it belongs to the live demo traffic, and synthetic rows
    would bury the run happening on screen."""
    assert len(plan["dates"]) == 14
    assert plan["dates"][-1] == END - timedelta(days=1)
    assert plan["dates"][0] == END - timedelta(days=14)
    assert END not in plan["dates"]


def test_plan_produces_a_non_flat_trend_series(plan):
    """The actual acceptance criterion for this tool: run the generated rows through
    the same compute_daily_series the dashboard renders, and every bucket in the
    window must carry real activity — no flat zeros beside one spike."""
    series = compute_daily_series(
        plan["interactions"], plan["intervention_outcomes"],
        days=14, end_date=END - timedelta(days=1),
    )

    assert len(series) == 14
    assert all(day["interactions"] > 0 for day in series), \
        "every day must have traffic — flat-zero buckets are the bug this tool fixes"
    assert all(day["deflection_rate_pct"] > 0 for day in series)
    assert sum(day["rto_prevented"] for day in series) > 0
    # Both savings levers must be represented, since the chart plots them as two series.
    assert sum(day["support_saving_pkr"] for day in series) > 0
    assert sum(day["rto_saving_pkr"] for day in series) > 0


def test_timestamps_bucket_into_their_intended_local_day(plan):
    """Generated timestamps are tz-aware in the business timezone, so they must land
    in the same local-date bucket compute_daily_series assigns them — a naive
    datetime here would silently shift a whole day's worth of rows."""
    planned_days = set(plan["dates"])
    for interaction in plan["interactions"]:
        created = interaction["created_at"]
        assert created.tzinfo is not None
        assert created.astimezone(TZ).date() in planned_days


def test_weekends_are_quieter_than_weekdays(plan):
    """Courier volume genuinely dips at the weekend; that shape is what makes the
    chart look like data rather than noise."""
    weekday, weekend = [], []
    for interaction in plan["interactions"]:
        day = interaction["created_at"].astimezone(TZ).date()
        (weekend if day.weekday() >= 5 else weekday).append(day)

    weekday_days = len({d for d in weekday})
    weekend_days = len({d for d in weekend})
    assert weekend_days > 0 and weekday_days > 0
    assert len(weekend) / weekend_days < len(weekday) / weekday_days


def test_after_hours_traffic_is_present_but_not_dominant(plan):
    """After-hours coverage is a headline KPI, so the data must exercise it — but a
    majority-after-hours dataset would be obviously fake."""
    after_hours = sum(
        1 for i in plan["interactions"]
        if not (config.BUSINESS_HOURS_START_HOUR
                <= i["created_at"].astimezone(TZ).hour
                < config.BUSINESS_HOURS_END_HOUR)
    )
    share = after_hours / len(plan["interactions"])
    assert 0.1 < share < 0.5


def test_escalation_varies_by_intent(plan):
    """A flat escalation rate across intents would make the deflection metric
    meaningless — a status check should almost never need a human, a delay complaint
    often does."""
    by_intent = {}
    for i in plan["interactions"]:
        bucket = by_intent.setdefault(i["intent"], [0, 0])
        bucket[0] += 1
        bucket[1] += 1 if i["escalated"] else 0

    track_total, track_escalated = by_intent["track_order"]
    delay_total, delay_escalated = by_intent["delay_complaint"]
    assert delay_escalated / delay_total > track_escalated / track_total


def test_intervention_chains_are_coherent(plan):
    """Each resolved outcome must trace back to a real intervention and to the second
    delivery attempt that resolved it — the cases feed, the RTO metric and the trend
    chart have to be describing the same events."""
    intervention_ids = {iv["intervention_id"] for iv in plan["interventions"]}
    attempt_keys = {(a["tracking_number"], a["attempt_no"]) for a in plan["delivery_attempts"]}

    assert plan["intervention_outcomes"], "expected at least one resolved intervention"
    for outcome in plan["intervention_outcomes"]:
        assert outcome["intervention_id"] in intervention_ids
        assert (outcome["tracking_number"], 1) in attempt_keys   # the original failure
        assert (outcome["tracking_number"], 2) in attempt_keys   # the redelivery
        assert outcome["outcome"] in {"delivered", "still_failed"}


def test_resolution_lags_the_intervention(plan):
    """A redelivery lands a day or two after the corrective action. That lag is real,
    and it's why the most recent buckets legitimately show fewer resolved outcomes
    than interventions."""
    intervention_times = {iv["intervention_id"]: iv["created_at"] for iv in plan["interventions"]}
    for outcome in plan["intervention_outcomes"]:
        assert outcome["created_at"] > intervention_times[outcome["intervention_id"]]


def test_every_row_is_marked_as_demo_data(plan):
    """Synthetic rows must be unmistakable and removable — `--wipe` identifies them by
    exactly these markers, and nothing here may ever be mistaken for real traffic."""
    for interaction in plan["interactions"]:
        assert interaction["customer_phone"].startswith(DEMO_PHONE_PREFIX)
        assert (interaction["tracking_number"] or DEMO_TRACKING_PREFIX).startswith(DEMO_TRACKING_PREFIX)
    for message in plan["messages"]:
        assert message["customer_phone"].startswith(DEMO_PHONE_PREFIX)
    for row in plan["interventions"] + plan["delivery_attempts"] + plan["intervention_outcomes"]:
        assert row["tracking_number"].startswith(DEMO_TRACKING_PREFIX)


def test_messages_pair_inbound_with_a_reply(plan):
    """Every generated interaction leaves a two-sided thread, so the conversation pane
    has history for the backdated days too."""
    inbound = [m for m in plan["messages"] if m["direction"] == "in"]
    outbound = [m for m in plan["messages"] if m["direction"] == "out"]
    assert len(inbound) == len(outbound) == len(plan["interactions"])
