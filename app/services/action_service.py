from app.core.db import get_connection


def create_ticket(tracking_number: str, reason: str, decision: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM tickets")
    count = cursor.fetchone()[0]
    ticket_id = f"TCK-{count + 1:04d}"

    cursor.execute(
        "INSERT INTO tickets (ticket_id, tracking_number, reason, decision) VALUES (?, ?, ?, ?)",
        (ticket_id, tracking_number, reason, decision),
    )
    conn.commit()
    conn.close()

    return {"ticket_id": ticket_id, "status": "created"}


def create_reroute_request(tracking_number: str, reason: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM reroutes")
    count = cursor.fetchone()[0]
    reroute_id = f"RRT-{count + 1:04d}"

    cursor.execute(
        "INSERT INTO reroutes (reroute_id, tracking_number, reason, status) VALUES (?, ?, ?, ?)",
        (reroute_id, tracking_number, reason, "requested"),
    )
    conn.commit()
    conn.close()

    return {"reroute_id": reroute_id, "status": "requested"}