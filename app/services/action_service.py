from app.core.database import SessionLocal
from app.models.ticket import Ticket
from app.models.reroute import Reroute


def create_ticket(tracking_number: str, reason: str, decision: str) -> dict:
    db = SessionLocal()
    try:
        count = db.query(Ticket).count()
        ticket_id = f"TCK-{count + 1:04d}"

        ticket = Ticket(
            ticket_id=ticket_id,
            tracking_number=tracking_number,
            reason=reason,
            decision=decision,
        )
        db.add(ticket)
        db.commit()

        return {"ticket_id": ticket_id, "status": "created"}
    finally:
        db.close()


def create_reroute_request(tracking_number: str, reason: str) -> dict:
    db = SessionLocal()
    try:
        count = db.query(Reroute).count()
        reroute_id = f"RRT-{count + 1:04d}"

        reroute = Reroute(
            reroute_id=reroute_id,
            tracking_number=tracking_number,
            reason=reason,
            status="requested",
        )
        db.add(reroute)
        db.commit()

        return {"reroute_id": reroute_id, "status": "requested"}
    finally:
        db.close()