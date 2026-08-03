from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import (
    test_routes, whatsapp_routes, metrics_routes, ops_routes, ops_write_routes,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Zentric - Agentic Logistics Support")

app.include_router(test_routes.router)
app.include_router(whatsapp_routes.router)
app.include_router(metrics_routes.router)
app.include_router(ops_routes.router)
# Writes live in their own router behind their own token (Phase 5) so ops_routes stays
# verifiably read-only — see app/routes/ops_write_routes.py.
app.include_router(ops_write_routes.router)

# The ops dashboard is a no-build static app served by this same process — one
# command and one URL at demo time, and no CDN to fail on an offline machine. The
# pages hold no secrets: everything they show comes from the token-gated /ops API,
# which the browser calls with a token the operator types in.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def health_check():
    return {"status": "ok", "service": "zentric-backend"}


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/simulator", include_in_schema=False)
def simulator():
    """Customer-side simulator: posts to /webhook/whatsapp exactly as the real Cloud
    API will, so the demo has a traffic source on the same screen as the dashboard.
    Deliberately not part of the ops surface and needs no ops token."""
    return FileResponse(STATIC_DIR / "simulator.html")
