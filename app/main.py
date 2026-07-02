from fastapi import FastAPI
from app.routes import test_routes

app = FastAPI(title="Zentric - Agentic Logistics Support")

app.include_router(test_routes.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "zentric-backend"}