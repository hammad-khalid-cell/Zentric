"""Phase 3 metrics service — the ROI evidence for the defense (docs/PROJECT_PLAN.md
§3/§9). Each `compute_*` function is a pure function over plain dicts (not ORM rows or
a live DB session) so it's trivially unit-testable against known fixtures; only
`get_metrics_report` touches the database, translating rows to dicts before handing
off to the compute functions.
"""
from datetime import datetime
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


def _interaction_to_dict(i: Interaction) -> dict:
    return {
        "escalated": i.escalated,
        "resolved_by": i.resolved_by,
        "response_time_ms": i.response_time_ms,
        "language": i.language,
        "created_at": i.created_at,
    }


def _intervention_outcome_to_dict(io: InterventionOutcome) -> dict:
    return {"outcome": io.outcome}


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
