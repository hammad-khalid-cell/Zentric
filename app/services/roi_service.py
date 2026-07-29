"""Phase 4 — the parameterised ROI model behind the dashboard's live calculator
(`docs/PROJECT_PLAN.md` §4.5: "a one-page ROI model the panel can poke at").

Why this lives on the server rather than in the dashboard's JavaScript: it is the
business claim the project is defended on, so it must be one deterministic
implementation with tests over known fixtures — not a second copy in the UI that can
silently drift from the first.

**Every number here is an assumption, not a fact.** §3 of the plan is explicit that the
savings model is presented as parameterised and validated against real courier data or
a cited report — never asserted. The defaults below are exactly the plan's own
illustrative mid-size-courier figures, and `compute_roi` re-derives the §3 table from
them, so the panel can change any input and watch the headline move.

The two levers are kept separate because they are defended separately, and because the
RTO lever being roughly 2x the support lever *is* the story.
"""
from app.core import config

MONTHS_PER_YEAR = 12

# The plan's illustrative mid-size Pakistani courier (docs/PROJECT_PLAN.md §2/§3):
# ~90% of e-commerce is COD; first-attempt delivery failure runs 20-30%.
ROI_DEFAULTS = {
    "monthly_parcels": 300_000,
    "cod_share_pct": 90.0,
    # Share of COD parcels that fail on the first delivery attempt.
    "first_attempt_failure_rate_pct": 25.0,
    # Of those failures, the share driven by the delay_reason codes an agent can
    # actually act on before the attempt fails (customer unavailable, wrong address,
    # unreachable) — as opposed to vehicle breakdown, damage, weather.
    "preventable_share_pct": 45.0,
    # Of preventable failures, the share where we reach the customer *and* the
    # corrective action converts the delivery. This is the one input the system
    # measures about itself; see get_roi_assumptions().
    "intervention_success_rate_pct": 40.0,
    # WISMO status queries generated per parcel shipped.
    "wismo_contacts_per_parcel": 0.5,
    "deflection_rate_pct": 70.0,
}

# Inputs that default to the tunable cost assumptions in config rather than to a
# figure baked in here, so the ROI model and the live metrics agree on unit costs.
_COST_INPUTS = {
    "human_cost_per_query_pkr": "HUMAN_COST_PER_QUERY_PKR",
    "bot_cost_per_query_pkr": "BOT_COST_PER_QUERY_PKR",
    "rto_cost_pkr": "RTO_COST_PKR",
}

INPUT_KEYS = tuple(ROI_DEFAULTS) + tuple(_COST_INPUTS)


def resolve_inputs(overrides: dict | None = None) -> dict:
    """Model defaults + config cost assumptions, with whatever the panel typed layered
    on top. Keys set to None are treated as "not supplied" so a partial payload from
    the calculator doesn't blank out the rest of the model."""
    resolved = dict(ROI_DEFAULTS)
    for key, config_attr in _COST_INPUTS.items():
        resolved[key] = getattr(config, config_attr)

    for key, value in (overrides or {}).items():
        if key in resolved and value is not None:
            resolved[key] = value
    return resolved


def _pct(value: float) -> float:
    return value / 100.0


def compute_roi(inputs: dict) -> dict:
    """Project annual PKR saved from a fully-resolved input set.

    Counts are rounded to whole parcels/queries *before* the money is derived from
    them, so a panelist multiplying the displayed figures on paper gets the displayed
    answer instead of a rounding discrepancy.

    Known simplification: the support lever credits only deflected queries with
    (human - bot), ignoring the bot cost spent on queries that escalated anyway. It
    matches `metrics_service.compute_daily_series` deliberately — the projection and
    the measured trend line must use the same definition of a saved query.
    """
    monthly_parcels = inputs["monthly_parcels"]
    human_cost = inputs["human_cost_per_query_pkr"]
    bot_cost = inputs["bot_cost_per_query_pkr"]
    rto_cost = inputs["rto_cost_pkr"]

    # Lever 1 — WISMO deflection.
    monthly_queries = round(monthly_parcels * inputs["wismo_contacts_per_parcel"])
    monthly_deflected = round(monthly_queries * _pct(inputs["deflection_rate_pct"]))
    support_monthly = monthly_deflected * (human_cost - bot_cost)

    # Lever 2 — RTO / failed-delivery reduction (the headline).
    monthly_cod = round(monthly_parcels * _pct(inputs["cod_share_pct"]))
    monthly_failures = round(monthly_cod * _pct(inputs["first_attempt_failure_rate_pct"]))
    monthly_preventable = round(monthly_failures * _pct(inputs["preventable_share_pct"]))
    monthly_prevented = round(monthly_preventable * _pct(inputs["intervention_success_rate_pct"]))
    rto_monthly = monthly_prevented * rto_cost

    total_monthly = support_monthly + rto_monthly
    rto_share = round(rto_monthly / total_monthly * 100, 1) if total_monthly else 0.0

    return {
        "inputs": dict(inputs),
        "support_lever": {
            "monthly_queries": monthly_queries,
            "monthly_deflected": monthly_deflected,
            "monthly_saving_pkr": round(support_monthly, 2),
            "annual_saving_pkr": round(support_monthly * MONTHS_PER_YEAR, 2),
        },
        "rto_lever": {
            "monthly_cod_parcels": monthly_cod,
            "monthly_failed_first_attempts": monthly_failures,
            "monthly_preventable_failures": monthly_preventable,
            "monthly_rto_prevented": monthly_prevented,
            "monthly_saving_pkr": round(rto_monthly, 2),
            "annual_saving_pkr": round(rto_monthly * MONTHS_PER_YEAR, 2),
        },
        "totals": {
            "monthly_saving_pkr": round(total_monthly, 2),
            "annual_saving_pkr": round(total_monthly * MONTHS_PER_YEAR, 2),
            # The plan's claim that RTO is the bigger lever, recomputed live rather
            # than asserted — if the panel's inputs flip it, the dashboard says so.
            "rto_share_of_total_pct": rto_share,
        },
    }


def simulate(overrides: dict | None = None) -> dict:
    """Resolve a partial input set and project from it. Pure computation — no writes,
    despite being reached over POST."""
    return compute_roi(resolve_inputs(overrides))


def get_roi_assumptions(measured: dict | None = None) -> dict:
    """What the calculator loads with: the model defaults, plus what the system has
    actually measured about itself, reported *separately*.

    They're kept apart on purpose. Measured rates are the honest input to use once
    there's real volume behind them, but early on a 100% deflection rate over three
    interactions would make the projection look authoritative and be meaningless — so
    the sample sizes travel with the numbers and the UI has to opt in to using them.
    """
    assumptions = {
        "defaults": resolve_inputs(),
        "measured": None,
        "note": (
            "Illustrative and tunable — not sourced from real courier data. "
            "Present as a parameterised model, never as fact."
        ),
    }
    if measured is not None:
        rto = measured.get("rto_reduction", {})
        assumptions["measured"] = {
            "deflection_rate_pct": measured.get("deflection_rate_pct", 0.0),
            "sample_interactions": measured.get("total_interactions", 0),
            "intervention_success_rate_pct": rto.get("rto_reduction_pct", 0.0),
            "sample_resolved_interventions": (
                rto.get("delivered_count", 0) + rto.get("still_failed_count", 0)
            ),
        }
    return assumptions
