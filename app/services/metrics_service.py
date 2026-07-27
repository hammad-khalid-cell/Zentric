"""Phase 3 metrics service — the ROI evidence for the defense (docs/PROJECT_PLAN.md
§3/§9). Each `compute_*` function is a pure function over plain dicts (not ORM rows or
a live DB session) so it's trivially unit-testable against known fixtures; only
`get_metrics_report` touches the database, translating rows to dicts before handing
off to the compute functions.
"""
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from app.core import config
from app.core.database import SessionLocal
from app.models.interaction import Interaction
from app.models.intervention_outcome import InterventionOutcome


def compute_deflection_rate(interactions: list[dict]) -> float:
    """Fraction (0-1) of interactions resolved without escalating to a human."""
    if not interactions:
        return 0.0
    deflected = sum(1 for i in interactions if not i["escalated"])
    return deflected / len(interactions)


def compute_cost_per_interaction(interactions: list[dict],
                                  human_cost: float | None = None,
                                  bot_cost: float | None = None) -> dict:
    human_cost = config.HUMAN_COST_PER_QUERY_PKR if human_cost is None else human_cost
    bot_cost = config.BOT_COST_PER_QUERY_PKR if bot_cost is None else bot_cost
    total = len(interactions)
    if total == 0:
        return {"blended_pkr": 0.0, "bot_only_pkr": bot_cost, "human_only_pkr": human_cost, "total_pkr": 0.0}

    bot_count = sum(1 for i in interactions if i["resolved_by"] == "bot")
    human_count = total - bot_count
    total_pkr = bot_count * bot_cost + human_count * human_cost
    return {
        "blended_pkr": round(total_pkr / total, 2),
        "bot_only_pkr": bot_cost,
        "human_only_pkr": human_cost,
        "total_pkr": round(total_pkr, 2),
    }


def compute_rto_reduction(intervention_outcomes: list[dict], rto_cost: float | None = None) -> dict:
    """Of the interventions that reached a resolved delivery outcome, what fraction
    ended in 'delivered' (RTO prevented) vs 'still_failed'."""
    rto_cost = config.RTO_COST_PKR if rto_cost is None else rto_cost
    total = len(intervention_outcomes)
    if total == 0:
        return {"rto_reduction_pct": 0.0, "delivered_count": 0, "still_failed_count": 0, "pkr_saved": 0.0}

    delivered = sum(1 for io in intervention_outcomes if io["outcome"] == "delivered")
    return {
        "rto_reduction_pct": round(delivered / total * 100, 1),
        "delivered_count": delivered,
        "still_failed_count": total - delivered,
        "pkr_saved": round(delivered * rto_cost, 2),
    }


def compute_response_time(interactions: list[dict]) -> dict:
    if not interactions:
        return {"avg_ms": 0.0, "median_ms": 0.0}
    times = [i["response_time_ms"] for i in interactions]
    return {"avg_ms": round(sum(times) / len(times), 1), "median_ms": round(median(times), 1)}


def compute_after_hours_pct(interactions: list[dict],
                             start_hour: int | None = None,
                             end_hour: int | None = None,
                             tz_name: str | None = None) -> float:
    """% of interactions whose timestamp falls outside the configured business-hours
    window (simplistic v1: hour-of-day only, no weekend/day-of-week distinction)."""
    if not interactions:
        return 0.0
    start_hour = config.BUSINESS_HOURS_START_HOUR if start_hour is None else start_hour
    end_hour = config.BUSINESS_HOURS_END_HOUR if end_hour is None else end_hour
    tz = ZoneInfo(config.BUSINESS_HOURS_TIMEZONE if tz_name is None else tz_name)

    after_hours = sum(
        1 for i in interactions
        if not (start_hour <= i["created_at"].astimezone(tz).hour < end_hour)
    )
    return round(after_hours / len(interactions) * 100, 1)


def compute_language_reach_pct(interactions: list[dict]) -> float:
    if not interactions:
        return 0.0
    roman_urdu = sum(1 for i in interactions if i["language"] == "roman_urdu")
    return round(roman_urdu / len(interactions) * 100, 1)


def _local_date(dt: datetime, tz: ZoneInfo) -> date:
    """Bucket key for the daily series. Rows read back from Postgres `timestamptz` are
    tz-aware; the naive guard is here because `astimezone()` on a naive datetime would
    silently assume the *server's* local time and quietly shift a whole day's bucket."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def compute_daily_series(interactions: list[dict],
                          intervention_outcomes: list[dict],
                          days: int = 14,
                          end_date: date | None = None,
                          tz_name: str | None = None,
                          human_cost: float | None = None,
                          bot_cost: float | None = None,
                          rto_cost: float | None = None) -> list[dict]:
    """Per-day KPI buckets for the dashboard's trend chart, oldest day first.

    Days with no activity are emitted as zero rows rather than skipped, so the chart
    gets a continuous x-axis instead of a line that silently closes gaps.

    Two savings lines, kept separate because they're the plan's two distinct levers
    (docs/PROJECT_PLAN.md §3) and the RTO one is the headline:
      - support: only *deflected* interactions save a human touch, so an escalated
        interaction contributes nothing — a human still handled it.
      - RTO: each intervention that resolved to 'delivered' avoided one return trip.

    Bucketing is by local date in the configured business timezone, matching
    compute_after_hours_pct, so every time-based metric agrees on where a day ends.
    """
    human_cost = config.HUMAN_COST_PER_QUERY_PKR if human_cost is None else human_cost
    bot_cost = config.BOT_COST_PER_QUERY_PKR if bot_cost is None else bot_cost
    rto_cost = config.RTO_COST_PKR if rto_cost is None else rto_cost
    tz = ZoneInfo(config.BUSINESS_HOURS_TIMEZONE if tz_name is None else tz_name)

    if end_date is None:
        end_date = datetime.now(tz).date()
    bucket_dates = [end_date - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    window = set(bucket_dates)

    buckets = {
        d: {"interactions": 0, "deflected": 0, "escalated": 0,
            "interventions_resolved": 0, "rto_prevented": 0}
        for d in bucket_dates
    }

    for interaction in interactions:
        day = _local_date(interaction["created_at"], tz)
        if day not in window:
            continue
        bucket = buckets[day]
        bucket["interactions"] += 1
        if interaction["escalated"]:
            bucket["escalated"] += 1
        else:
            bucket["deflected"] += 1

    for outcome in intervention_outcomes:
        day = _local_date(outcome["created_at"], tz)
        if day not in window:
            continue
        bucket = buckets[day]
        bucket["interventions_resolved"] += 1
        if outcome["outcome"] == "delivered":
            bucket["rto_prevented"] += 1

    series = []
    for day in bucket_dates:
        bucket = buckets[day]
        support_saving = bucket["deflected"] * (human_cost - bot_cost)
        rto_saving = bucket["rto_prevented"] * rto_cost
        deflection_pct = (
            round(bucket["deflected"] / bucket["interactions"] * 100, 1)
            if bucket["interactions"] else 0.0
        )
        series.append({
            "date": day.isoformat(),
            **bucket,
            "deflection_rate_pct": deflection_pct,
            "support_saving_pkr": round(support_saving, 2),
            "rto_saving_pkr": round(rto_saving, 2),
            "total_saving_pkr": round(support_saving + rto_saving, 2),
        })
    return series


def _interaction_to_dict(i: Interaction) -> dict:
    return {
        "escalated": i.escalated,
        "resolved_by": i.resolved_by,
        "response_time_ms": i.response_time_ms,
        "language": i.language,
        "created_at": i.created_at,
    }


def _intervention_outcome_to_dict(io: InterventionOutcome) -> dict:
    return {"outcome": io.outcome, "created_at": io.created_at}


def get_metrics_report(since: datetime | None = None, until: datetime | None = None) -> dict:
    """The full KPI report (docs/PROJECT_PLAN.md §3/§9), derived entirely from the
    system's own recorded Interaction/InterventionOutcome rows. Optional since/until
    bound both by created_at."""
    db = SessionLocal()
    try:
        interaction_q = db.query(Interaction)
        outcome_q = db.query(InterventionOutcome)
        if since:
            interaction_q = interaction_q.filter(Interaction.created_at >= since)
            outcome_q = outcome_q.filter(InterventionOutcome.created_at >= since)
        if until:
            interaction_q = interaction_q.filter(Interaction.created_at <= until)
            outcome_q = outcome_q.filter(InterventionOutcome.created_at <= until)

        interactions = [_interaction_to_dict(i) for i in interaction_q.all()]
        intervention_outcomes = [_intervention_outcome_to_dict(io) for io in outcome_q.all()]
    finally:
        db.close()

    return {
        "total_interactions": len(interactions),
        "deflection_rate_pct": round(compute_deflection_rate(interactions) * 100, 1),
        "cost_per_interaction": compute_cost_per_interaction(interactions),
        "rto_reduction": compute_rto_reduction(intervention_outcomes),
        "response_time_ms": compute_response_time(interactions),
        "after_hours_pct": compute_after_hours_pct(interactions),
        "language_reach_pct": compute_language_reach_pct(interactions),
    }


def get_metrics_timeseries(days: int = 14) -> dict:
    """Daily KPI buckets for the dashboard's trend chart. One query pass over the
    window — the alternative (N sliding calls to get_metrics_report) would be N full
    scans of the same two tables.

    The window is bounded by *local* midnight in the business timezone, so the first
    bucket is a whole day rather than a partial one starting at whatever time the
    request happened to arrive.
    """
    tz = ZoneInfo(config.BUSINESS_HOURS_TIMEZONE)
    end_date = datetime.now(tz).date()
    start_date = end_date - timedelta(days=days - 1)
    start_at = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)

    db = SessionLocal()
    try:
        interactions = [
            _interaction_to_dict(i)
            for i in db.query(Interaction).filter(Interaction.created_at >= start_at).all()
        ]
        intervention_outcomes = [
            _intervention_outcome_to_dict(io)
            for io in db.query(InterventionOutcome)
            .filter(InterventionOutcome.created_at >= start_at).all()
        ]
    finally:
        db.close()

    return {
        "days": days,
        "timezone": config.BUSINESS_HOURS_TIMEZONE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        # Echoed so the dashboard can label the savings lines as the tunable
        # assumptions they are (docs/PROJECT_PLAN.md §3) rather than as fact.
        "assumptions": {
            "human_cost_per_query_pkr": config.HUMAN_COST_PER_QUERY_PKR,
            "bot_cost_per_query_pkr": config.BOT_COST_PER_QUERY_PKR,
            "rto_cost_pkr": config.RTO_COST_PKR,
        },
        "series": compute_daily_series(interactions, intervention_outcomes,
                                       days=days, end_date=end_date),
    }
