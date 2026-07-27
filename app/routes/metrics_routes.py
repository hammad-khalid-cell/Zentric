"""KPI report endpoint. Phase 4 put it behind the same read-only dashboard token as
the rest of the ops API (`app/core/auth.py`) — the dashboard is its consumer, and one
gate for every ops read is easier to reason about than a per-endpoint judgement call
about which aggregates are safe to expose."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.auth import require_dashboard_token
from app.services.metrics_service import get_metrics_report, get_metrics_timeseries

router = APIRouter(dependencies=[Depends(require_dashboard_token)])


@router.get("/metrics/report")
def metrics_report(
    since: datetime | None = Query(None, description="ISO 8601 — only interactions at/after this time"),
    until: datetime | None = Query(None, description="ISO 8601 — only interactions at/before this time"),
):
    return get_metrics_report(since=since, until=until)


@router.get("/metrics/timeseries")
def metrics_timeseries(
    days: int = Query(14, ge=1, le=90, description="Number of days to bucket, ending today"),
):
    """Daily KPI buckets for the dashboard's trend chart. Zero-activity days are
    included as zero rows so the chart's x-axis stays continuous."""
    return get_metrics_timeseries(days=days)
