from fastapi import FastAPI
from app.routes import test_routes, whatsapp_routes, metrics_routes, ops_routes

app = FastAPI(title="Zentric - Agentic Logistics Support")

app.include_router(test_routes.router)
app.include_router(whatsapp_routes.router)
app.include_router(metrics_routes.router)
app.include_router(ops_routes.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "zentric-backend"}