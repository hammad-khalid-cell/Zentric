"""Phase 4 — compute_daily_series: the per-day buckets behind the dashboard's trend chart.

Pure function over plain dicts, same as the other compute_* fns in metrics_service, so
these assert on known fixtures with no database. The interesting behaviours are the
ones a chart depends on: zero-filled gaps, bucketing by *local* (Asia/Karachi) date
rather than UTC, and savings attributed only where a saving actually occurred.
"""
from datetime import date, datetime, timezone

import pytest

from app.services.metrics_service import compute_daily_series

END = date(2026, 7, 27)
KARACHI = "Asia/Karachi"   # UTC+5, no DST


def interaction(created_at: datetime, escalated: bool = False) -> dict:
    return {
        "escalated": escalated,
        "resolved_by": "human" if escalated else "bot",
        "response_time_ms": 500,
        "language": "english",
        "created_at": created_at,
    }


def outcome(created_at: datetime, result: str = "delivered") -> dict:
    return {"outcome": result, "created_at": created_at}


def utc(year, month, day, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def by_date(series: list[dict]) -> dict[str, dict]:
    return {row["date"]: row for row in series}


# --- window shape --------------------------------------------------------

def test_series_covers_every_day_oldest_first():
    series = compute_daily_series([], [], days=7, end_date=END, tz_name=KARACHI)

    assert len(series) == 7
    assert series[0]["date"] == "2026-07-21"
    assert series[-1]["date"] == "2026-07-27"
    assert [row["date"] for row in series] == sorted(row["date"] for row in series)


def test_days_with_no_activity_are_zero_filled_not_skipped():
    """A chart must not silently close gaps — a quiet day is a real data point."""
    series = compute_daily_series(
        [interaction(utc(2026, 7, 27))], [], days=3, end_date=END, tz_name=KARACHI
    )

    quiet = by_date(series)["2026-07-25"]
    assert quiet["interactions"] == 0
    assert quiet["deflection_rate_pct"] == 0.0
    assert quiet["total_saving_pkr"] == 0.0


def test_single_day_window():
    series = compute_daily_series([], [], days=1, end_date=END, tz_name=KARACHI)

    assert [row["date"] for row in series] == ["2026-07-27"]


def test_data_outside_the_window_is_ignored():
    series = compute_daily_series(
        [interaction(utc(2026, 7, 1)), interaction(utc(2026, 7, 27))],
        [outcome(utc(2026, 7, 1))],
        days=3, end_date=END, tz_name=KARACHI,
    )

    assert sum(row["interactions"] for row in series) == 1
    assert sum(row["interventions_resolved"] for row in series) == 0


# --- bucketing by local date --------------------------------------------

def test_buckets_by_local_business_date_not_utc():
    """22:00 UTC on the 26th is 03:00 on the 27th in Karachi (UTC+5). It belongs to
    the 27th, matching how compute_after_hours_pct reads the clock."""
    series = by_date(compute_daily_series(
        [interaction(utc(2026, 7, 26, 22, 0))], [], days=3, end_date=END, tz_name=KARACHI
    ))

    assert series["2026-07-27"]["interactions"] == 1
    assert series["2026-07-26"]["interactions"] == 0


def test_naive_timestamps_are_treated_as_utc():
    """Guard against astimezone() silently reading a naive value as server-local time."""
    naive = datetime(2026, 7, 26, 22, 0)   # no tzinfo

    series = by_date(compute_daily_series(
        [interaction(naive)], [], days=3, end_date=END, tz_name=KARACHI
    ))

    assert series["2026-07-27"]["interactions"] == 1


# --- per-day metrics -----------------------------------------------------

def test_counts_split_deflected_and_escalated():
    series = by_date(compute_daily_series(
        [
            interaction(utc(2026, 7, 27)),
            interaction(utc(2026, 7, 27)),
            interaction(utc(2026, 7, 27), escalated=True),
        ],
        [], days=2, end_date=END, tz_name=KARACHI,
    ))

    day = series["2026-07-27"]
    assert (day["interactions"], day["deflected"], day["escalated"]) == (3, 2, 1)
    assert day["deflection_rate_pct"] == 66.7


def test_rto_prevented_counts_only_delivered_outcomes():
    series = by_date(compute_daily_series(
        [],
        [
            outcome(utc(2026, 7, 27), "delivered"),
            outcome(utc(2026, 7, 27), "delivered"),
            outcome(utc(2026, 7, 27), "still_failed"),
        ],
        days=2, end_date=END, tz_name=KARACHI,
    ))

    day = series["2026-07-27"]
    assert day["interventions_resolved"] == 3
    assert day["rto_prevented"] == 2


# --- savings lines -------------------------------------------------------

def test_support_saving_credits_only_deflected_interactions():
    """An escalated query still cost a human, so it saves nothing."""
    series = by_date(compute_daily_series(
        [interaction(utc(2026, 7, 27)), interaction(utc(2026, 7, 27), escalated=True)],
        [], days=2, end_date=END, tz_name=KARACHI,
        human_cost=30, bot_cost=2, rto_cost=450,
    ))

    assert series["2026-07-27"]["support_saving_pkr"] == 28.0   # 1 deflected x (30 - 2)


def test_rto_and_total_savings_use_the_supplied_assumptions():
    series = by_date(compute_daily_series(
        [interaction(utc(2026, 7, 27))],
        [outcome(utc(2026, 7, 27)), outcome(utc(2026, 7, 27))],
        days=2, end_date=END, tz_name=KARACHI,
        human_cost=30, bot_cost=2, rto_cost=500,
    ))

    day = series["2026-07-27"]
    assert day["rto_saving_pkr"] == 1000.0
    assert day["total_saving_pkr"] == 1028.0


def test_cost_assumptions_default_to_config(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "HUMAN_COST_PER_QUERY_PKR", 40.0)
    monkeypatch.setattr(config, "BOT_COST_PER_QUERY_PKR", 5.0)
    monkeypatch.setattr(config, "RTO_COST_PKR", 600.0)

    series = by_date(compute_daily_series(
        [interaction(utc(2026, 7, 27))], [outcome(utc(2026, 7, 27))],
        days=1, end_date=END, tz_name=KARACHI,
    ))

    day = series["2026-07-27"]
    assert day["support_saving_pkr"] == 35.0
    assert day["rto_saving_pkr"] == 600.0


@pytest.mark.parametrize("field", [
    "date", "interactions", "deflected", "escalated", "interventions_resolved",
    "rto_prevented", "deflection_rate_pct", "support_saving_pkr", "rto_saving_pkr",
    "total_saving_pkr",
])
def test_every_bucket_exposes_the_full_chart_contract(field):
    (row,) = compute_daily_series([], [], days=1, end_date=END, tz_name=KARACHI)

    assert field in row
