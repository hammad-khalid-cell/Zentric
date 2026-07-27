"""Single place every inbound/outbound message is persisted, so both the mock
and (later) the real WhatsApp channel record history identically, and the
dashboard can render a conversation thread per customer."""
from app.core.database import SessionLocal
from app.models.message import Message

DIRECTION_IN = "in"
DIRECTION_OUT = "out"


def log_message(customer_phone: str, direction: str, body: str,
                tracking_number: str | None = None) -> dict:
    db = SessionLocal()
    try:
        msg = Message(
            customer_phone=customer_phone,
            direction=direction,
            body=body,
            tracking_number=tracking_number,
        )
        db.add(msg)
        db.commit()
        return {"id": msg.id, "direction": direction}
    finally:
        db.close()


def get_conversation(customer_phone: str, limit: int = 50) -> list[dict]:
    """Oldest-first message history for one customer — for the dashboard/simulator."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(Message.customer_phone == customer_phone)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": m.id,
                "direction": m.direction,
                "body": m.body,
                "tracking_number": m.tracking_number,
                "created_at": m.created_at,
            }
            for m in rows
        ]
    finally:
        db.close()
