from datetime import date

from app.core.database import SessionLocal
from app.models.parcel import Parcel
from app.services import delivery_state


def find_delayed_parcels() -> list[dict]:
    """Overdue parcels whose journey hasn't ended. Excluding *every* terminal status,
    not just `delivered`: a parcel returned to origin is overdue forever, so filtering
    on `!= "delivered"` alone would have the proactive scanner chasing dead parcels and
    messaging customers about a delivery that is never coming."""
    db = SessionLocal()
    try:
        parcels = (
            db.query(Parcel)
            .filter(
                Parcel.status.notin_(tuple(delivery_state.TERMINAL_STATUSES)),
                Parcel.expected_delivery_date < date.today(),
            )
            .all()
        )
        return [_to_dict(p) for p in parcels]
    finally:
        db.close()


def find_parcel(tracking_number: str) -> dict | None:
    db = SessionLocal()
    try:
        parcel = db.query(Parcel).filter_by(tracking_number=tracking_number.upper()).first()
        return _to_dict(parcel) if parcel else None
    finally:
        db.close()


def find_parcels_by_phone(phone_number: str) -> list[dict]:
    db = SessionLocal()
    try:
        parcels = db.query(Parcel).filter_by(customer_phone=phone_number).all()
        return [_to_dict(p) for p in parcels]
    finally:
        db.close()


def _to_dict(parcel: Parcel) -> dict:
    return {
        "tracking_number": parcel.tracking_number,
        "customer_phone": parcel.customer_phone,
        "status": parcel.status,
        "current_hub": parcel.current_hub,
        "destination_city": parcel.destination_city,
        "dispatch_date": parcel.dispatch_date,
        "expected_delivery_date": parcel.expected_delivery_date,
        "delay_reason": parcel.delay_reason,
        "address_line": parcel.address_line,
        "preferred_delivery_window": parcel.preferred_delivery_window,
        "attempt_count": parcel.attempt_count or 0,
    }