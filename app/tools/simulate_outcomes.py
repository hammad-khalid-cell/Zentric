"""Demo-data outcome simulator (Phase 3).

There is no real courier/delivery-management feedback in this system yet (that's
Phase 6). So "did a corrective action actually prevent an RTO?" has no organic
signal — this script stands in for that missing feedback, resolving every
Intervention that doesn't yet have an InterventionOutcome to success ('delivered')
or failure ('still_failed'), weighted so interventions mostly succeed (the story
the proactive loop is supposed to tell) while still leaving some failures so the
RTO-reduction % isn't a suspicious 100%.

This is a real service call (delivery_service.record_attempt_outcome), not a fake —
a future real delivery-system webhook would call the exact same function. Run this
once as demo-data prep, any time after corrective actions have been applied:

    python -m app.tools.simulate_outcomes
    python -m app.tools.simulate_outcomes --success-rate 0.6 --seed 42
"""
import argparse
import random

from app.core.database import SessionLocal
from app.models.intervention import Intervention
from app.models.intervention_outcome import InterventionOutcome
from app.services.delivery_service import record_attempt_outcome

DEFAULT_SUCCESS_RATE = 0.75


def find_unresolved_interventions() -> list[str]:
    """Tracking numbers with an Intervention that has no InterventionOutcome yet."""
    db = SessionLocal()
    try:
        resolved_ids = {row.intervention_id for row in db.query(InterventionOutcome.intervention_id).all()}
        interventions = db.query(Intervention).all()
        return [iv.tracking_number for iv in interventions if iv.intervention_id not in resolved_ids]
    finally:
        db.close()


def simulate(success_rate: float = DEFAULT_SUCCESS_RATE) -> dict:
    tracking_numbers = find_unresolved_interventions()
    resolved, skipped = 0, 0

    for tracking_number in tracking_numbers:
        outcome = "success" if random.random() < success_rate else "failed"
        result = record_attempt_outcome(tracking_number, outcome)
        if result.get("recorded"):
            resolved += 1
        else:
            skipped += 1

    return {"candidates": len(tracking_numbers), "resolved": resolved, "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--success-rate", type=float, default=DEFAULT_SUCCESS_RATE,
                        help=f"Probability a resolved intervention succeeds (default {DEFAULT_SUCCESS_RATE})")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible demo data")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    summary = simulate(success_rate=args.success_rate)
    print(
        f"Resolved {summary['resolved']}/{summary['candidates']} open interventions "
        f"({summary['skipped']} skipped) at success_rate={args.success_rate}."
    )
