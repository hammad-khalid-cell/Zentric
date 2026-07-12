from fastapi import FastAPI
from app.routes import test_routes
from app.core.db import init_db

app = FastAPI(title="Zentric - Agentic Logistics Support")

init_db()

app.include_router(test_routes.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "zentric-backend"}