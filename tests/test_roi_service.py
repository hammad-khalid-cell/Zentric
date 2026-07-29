"""Phase 4 — the parameterised ROI model behind the dashboard's live calculator.

The load-bearing test here is the first one: the defaults must re-derive the savings
table in docs/PROJECT_PLAN.md §3 (~PKR 36M support, ~PKR 65M RTO, RTO roughly 2x the
support lever). If someone edits an assumption without meaning to move the headline,
that test is what catches it — the plan doc and the calculator would otherwise drift
apart silently and the defense would quote two different numbers.
"""
import pytest

from app.core import config
from app.routes import ops_routes
from app.services import roi_service
from app.services.roi_service import ROI_DEFAULTS, compute_roi, resolve_inputs, simulate

MILLION = 1_000_000


# --- the plan's headline table ------------------------------------------

def test_defaults_reproduce_the_plan_savings_table():
    result = simulate()

    support_annual = result["support_lever"]["annual_saving_pkr"]
    rto_annual = result["rto_lever"]["annual_saving_pkr"]

    assert support_annual == pytest.approx(36 * MILLION, rel=0.05)   # plan §3: ~PKR 36M
    assert rto_annual == pytest.approx(65 * MILLION, rel=0.05)       # plan §3: ~PKR 65M


def test_rto_is_the_bigger_lever_by_roughly_two_x():
    """The plan's actual claim (§3) — RTO is ~2x support. That's the story, so it's
    asserted rather than left to inspection."""
    result = simulate()

    ratio = result["rto_lever"]["annual_saving_pkr"] / result["support_lever"]["annual_saving_pkr"]

    assert 1.5 <= ratio <= 2.5
    assert result["totals"]["rto_share_of_total_pct"] > 50


# --- the funnel ----------------------------------------------------------

def test_rto_funnel_narrows_at_every_stage():
    result = simulate()["rto_lever"]

    assert (result["monthly_cod_parcels"]
            > result["monthly_failed_first_attempts"]
            > result["monthly_preventable_failures"]
            > result["monthly_rto_prevented"])


def test_rto_funnel_math_on_round_numbers():
    result = compute_roi(resolve_inputs({
        "monthly_parcels": 100_000,
        "cod_share_pct": 90,
        "first_attempt_failure_rate_pct": 20,
        "preventable_share_pct": 50,
        "intervention_success_rate_pct": 40,
        "rto_cost_pkr": 500,
    }))["rto_lever"]

    assert result["monthly_cod_parcels"] == 90_000
    assert result["monthly_failed_first_attempts"] == 18_000
    assert result["monthly_preventable_failures"] == 9_000
    assert result["monthly_rto_prevented"] == 3_600
    assert result["monthly_saving_pkr"] == 1_800_000
    assert result["annual_saving_pkr"] == 21_600_000


def test_support_lever_math_on_round_numbers():
    result = compute_roi(resolve_inputs({
        "monthly_parcels": 100_000,
        "wismo_contacts_per_parcel": 0.5,
        "deflection_rate_pct": 70,
        "human_cost_per_query_pkr": 30,
        "bot_cost_per_query_pkr": 2,
    }))["support_lever"]

    assert result["monthly_queries"] == 50_000
    assert result["monthly_deflected"] == 35_000
    assert result["monthly_saving_pkr"] == 980_000     # 35,000 x (30 - 2)
    assert result["annual_saving_pkr"] == 11_760_000


def test_displayed_counts_multiply_out_to_displayed_money():
    """A panelist checking the arithmetic on paper must get the same answer, so counts
    are rounded before the money is derived from them."""
    result = compute_roi(resolve_inputs({"monthly_parcels": 123_457, "rto_cost_pkr": 450}))

    rto = result["rto_lever"]
    support = result["support_lever"]
    inputs = result["inputs"]

    assert rto["monthly_saving_pkr"] == rto["monthly_rto_prevented"] * 450
    assert support["monthly_saving_pkr"] == support["monthly_deflected"] * (
        inputs["human_cost_per_query_pkr"] - inputs["bot_cost_per_query_pkr"]
    )


# --- input resolution ----------------------------------------------------

def test_costs_default_to_config(monkeypatch):
    monkeypatch.setattr(config, "HUMAN_COST_PER_QUERY_PKR", 44.0)
    monkeypatch.setattr(config, "BOT_COST_PER_QUERY_PKR", 4.0)
    monkeypatch.setattr(config, "RTO_COST_PKR", 700.0)

    resolved = resolve_inputs()

    assert resolved["human_cost_per_query_pkr"] == 44.0
    assert resolved["bot_cost_per_query_pkr"] == 4.0
    assert resolved["rto_cost_pkr"] == 700.0


def test_partial_overrides_leave_the_rest_of_the_model_intact():
    resolved = resolve_inputs({"monthly_parcels": 5})

    assert resolved["monthly_parcels"] == 5
    assert resolved["cod_share_pct"] == ROI_DEFAULTS["cod_share_pct"]


def test_none_values_are_treated_as_not_supplied():
    """The calculator posts only touched sliders; a null must not blank the model."""
    resolved = resolve_inputs({"monthly_parcels": None, "cod_share_pct": 50})

    assert resolved["monthly_parcels"] == ROI_DEFAULTS["monthly_parcels"]
    assert resolved["cod_share_pct"] == 50


def test_unknown_keys_are_ignored():
    resolved = resolve_inputs({"monthly_parcels": 10, "profit_margin_pct": 99})

    assert "profit_margin_pct" not in resolved


def test_result_echoes_the_inputs_it_used():
    """So a screenshot of the panel is self-documenting about its assumptions."""
    result = simulate({"monthly_parcels": 42})

    assert result["inputs"]["monthly_parcels"] == 42
    assert set(result["inputs"]) == set(roi_service.INPUT_KEYS)


# --- degenerate inputs ---------------------------------------------------

def test_zero_volume_produces_zeros_not_an_error():
    result = simulate({"monthly_parcels": 0})

    assert result["totals"]["annual_saving_pkr"] == 0.0
    assert result["totals"]["rto_share_of_total_pct"] == 0.0


def test_zero_rates_zero_the_relevant_lever():
    result = simulate({"deflection_rate_pct": 0, "intervention_success_rate_pct": 0})

    assert result["support_lever"]["monthly_saving_pkr"] == 0.0
    assert result["rto_lever"]["monthly_saving_pkr"] == 0.0


def test_bot_costing_more_than_a_human_yields_a_negative_support_lever():
    """The model shouldn't quietly floor at zero — if the assumptions say the bot is
    the expensive option, the panel should see that."""
    result = simulate({"human_cost_per_query_pkr": 2, "bot_cost_per_query_pkr": 30})

    assert result["support_lever"]["monthly_saving_pkr"] < 0


# --- assumptions payload -------------------------------------------------

def test_assumptions_without_measurements_reports_none():
    assumptions = roi_service.get_roi_assumptions()

    assert assumptions["measured"] is None
    assert assumptions["defaults"]["monthly_parcels"] == ROI_DEFAULTS["monthly_parcels"]
    assert "tunable" in assumptions["note"]


def test_assumptions_carry_sample_sizes_alongside_measured_rates():
    """A 100% rate over 3 interactions must not look like a 100% rate over 3,000."""
    assumptions = roi_service.get_roi_assumptions(measured={
        "total_interactions": 3,
        "deflection_rate_pct": 100.0,
        "rto_reduction": {"rto_reduction_pct": 100.0, "delivered_count": 1,
                          "still_failed_count": 0},
    })

    assert assumptions["measured"] == {
        "deflection_rate_pct": 100.0,
        "sample_interactions": 3,
        "intervention_success_rate_pct": 100.0,
        "sample_resolved_interventions": 1,
    }


def test_measured_values_do_not_overwrite_the_model_defaults():
    assumptions = roi_service.get_roi_assumptions(measured={
        "total_interactions": 3,
        "deflection_rate_pct": 100.0,
        "rto_reduction": {"rto_reduction_pct": 100.0, "delivered_count": 1,
                          "still_failed_count": 0},
    })

    assert assumptions["defaults"]["deflection_rate_pct"] == ROI_DEFAULTS["deflection_rate_pct"]
    assert assumptions["defaults"]["intervention_success_rate_pct"] == (
        ROI_DEFAULTS["intervention_success_rate_pct"]
    )


def test_assumptions_tolerate_an_empty_metrics_report():
    """A fresh database returns a report with no rto_reduction detail worth reading."""
    assumptions = roi_service.get_roi_assumptions(measured={})

    assert assumptions["measured"]["sample_interactions"] == 0
    assert assumptions["measured"]["sample_resolved_interventions"] == 0


# --- route wiring --------------------------------------------------------

def test_simulate_route_passes_only_the_fields_that_were_sent():
    payload = ops_routes.RoiRequest(monthly_parcels=1_000)

    result = ops_routes.roi_simulate(payload)

    assert result["inputs"]["monthly_parcels"] == 1_000
    assert result["inputs"]["cod_share_pct"] == ROI_DEFAULTS["cod_share_pct"]


@pytest.mark.parametrize("field,value", [
    ("cod_share_pct", 101),
    ("cod_share_pct", -1),
    ("deflection_rate_pct", 100.1),
    ("monthly_parcels", -5),
    ("rto_cost_pkr", -1),
    ("wismo_contacts_per_parcel", 21),
])
def test_simulate_route_rejects_out_of_range_inputs(field, value):
    with pytest.raises(Exception):
        ops_routes.RoiRequest(**{field: value})
