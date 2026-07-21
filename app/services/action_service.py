from app.core.database import SessionLocal
from app.models.ticket import Ticket
from app.models.reroute import Reroute


def find_open_ticket(tracking_number: str) -> dict | None:
    db = SessionLocal()
    try:
        ticket = (
            db.query(Ticket)
            .filter(Ticket.tracking_number == tracking_number, Ticket.status == "open")
            .first()
        )
        return {"ticket_id": ticket.ticket_id, "status": ticket.status} if ticket else None
    finally:
        db.close()


def find_active_reroute(tracking_number: str) -> dict | None:
    db = SessionLocal()
    try:
        reroute = (
            db.query(Reroute)
            .filter(Reroute.tracking_number == tracking_number, Reroute.status == "requested")
            .first()
        )
        return {"reroute_id": reroute.reroute_id, "status": reroute.status} if reroute else None
    finally:
        db.close()


def create_ticket(tracking_number: str, reason: str, decision: str) -> dict:
    # Guard against the same delay complaint (retry, duplicate message, repeat graph
    # run) spawning a second open ticket for a parcel that already has one.
    existing = find_open_ticket(tracking_number)
    if existing:
        return {**existing, "already_existed": True}

    db = SessionLocal()
    try:
        ticket = Ticket(
            ticket_id="PENDING",
            tracking_number=tracking_number,
            reason=reason,
            decision=decision,
        )
        db.add(ticket)
        db.flush()  # assigns the DB-generated autoincrement id — atomic, no count()-based race
        ticket.ticket_id = f"TCK-{ticket.id:04d}"
        db.commit()

        return {"ticket_id": ticket.ticket_id, "status": "created", "already_existed": False}
    finally:
        db.close()


def create_reroute_request(tracking_number: str, reason: str) -> dict:
    existing = find_active_reroute(tracking_number)
    if existing:
        return {**existing, "already_existed": True}

    db = SessionLocal()
    try:
        reroute = Reroute(
            reroute_id="PENDING",
            tracking_number=tracking_number,
            reason=reason,
            status="requested",
        )
        db.add(reroute)
        db.flush()
        reroute.reroute_id = f"RRT-{reroute.id:04d}"
        db.commit()

        return {"reroute_id": reroute.reroute_id, "status": "requested", "already_existed": False}
    finally:
        db.close()
