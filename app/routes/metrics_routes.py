"""Phase 3 — dev-only KPI report endpoint. No auth, matching the existing
/test/message precedent; Phase 4's dashboard is the intended consumer."""
from datetime import datetime

from fastapi import APIRouter, Query

from app.services.metrics_service import get_metrics_report

router = APIRouter()


@router.get("/metrics/report")
def metrics_report(
    since: datetime | None = Query(None, description="ISO 8601 — only interactions at/after this time"),
    until: datetime | None = Query(None, description="ISO 8601 — only interactions at/before this time"),
):
    return get_metrics_report(since=since, until=until)
