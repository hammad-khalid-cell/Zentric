from app.core.database import SessionLocal
from app.models.parcel import Parcel


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
    }