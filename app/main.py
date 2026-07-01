from fastapi import FastAPI

app = FastAPI(title="Zentric - Agentic Logistics Support")


@app.get("/")
def health_check():
    return {"status": "ok", "service": "zentric-backend"}