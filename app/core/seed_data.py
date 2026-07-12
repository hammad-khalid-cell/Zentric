import random
from datetime import date, timedelta
from app.core.database import SessionLocal
from app.models.parcel import Parcel

CITIES = ["Lahore", "Karachi", "Islamabad", "Multan", "Faisalabad", "Peshawar", "Quetta", "Rawalpindi", "Sialkot", "Gujranwala"]

HUBS = [f"{city} Sorting Facility" for city in CITIES] + [f"{city} Local Hub" for city in CITIES]

STATUSES = ["booked", "picked_up", "in_transit", "arrived_at_facility", "out_for_delivery", "delivered"]

DELAY_REASONS = [
    "customer_unavailable", "incorrect_address", "consignee_requested_reschedule",
    "weather_delay", "vehicle_breakdown", "shipment_damaged",
    "security_restrictions", "payment_issue_cod", "operational_delay", "linehaul_delay",
]

PHONE_NUMBERS = [
    "923001234567", "923009999999", "923004444444", "923007777777",
    "923331112222", "923214445566", "923455556677", "923099887766",
    "923112233445", "923556677889",
]


def generate_random_parcels(count: int) -> list[dict]:
    parcels = []
    used_tracking_numbers = set()

    for _ in range(count):
        tracking_number = f"TRK{random.randint(10000, 99999)}"
        while tracking_number in used_tracking_numbers:
            tracking_number = f"TRK{random.randint(10000, 99999)}"
        used_tracking_numbers.add(tracking_number)

        status = random.choice(STATUSES)
        dispatch_offset = random.randint(1, 10)
        dispatch_date = date.today() - timedelta(days=dispatch_offset)

        # ~35% chance of being delayed (expected date in the past, not delivered)
        is_delayed = status != "delivered" and random.random() < 0.35

        if is_delayed:
            expected_delivery_date = date.today() - timedelta(days=random.randint(1, 4))
            delay_reason = random.choice(DELAY_REASONS)
        else:
            expected_delivery_date = date.today() + timedelta(days=random.randint(0, 5))
            delay_reason = None

        parcels.append({
            "tracking_number": tracking_number,
            "customer_phone": random.choice(PHONE_NUMBERS),
            "status": status,
            "current_hub": random.choice(HUBS),
            "destination_city": random.choice(CITIES),
            "dispatch_date": dispatch_date,
            "expected_delivery_date": expected_delivery_date,
            "delay_reason": delay_reason,
        })

    return parcels


# Fixed, known test cases — kept identical to what we've been testing against
# throughout the project, so all existing test_manual.py cases still work.
FIXED_PARCELS = [
    {
        "tracking_number": "TRK12345",
        "customer_phone": "923001234567",
        "status": "in_transit",
        "current_hub": "Lahore Sorting Facility",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=2),
        "expected_delivery_date": date.today() + timedelta(days=1),
        "delay_reason": None,
    },
    {
        "tracking_number": "TRK67890",
        "customer_phone": "923001234567",
        "status": "out_for_delivery",
        "current_hub": "Karachi Local Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=3),
        "expected_delivery_date": date.today(),
        "delay_reason": None,
    },
    {
        "tracking_number": "TRK99999",
        "customer_phone": "923009999999",
        "status": "in_transit",
        "current_hub": "Multan Sorting Facility",
        "destination_city": "Islamabad",
        "dispatch_date": date.today() - timedelta(days=6),
        "expected_delivery_date": date.today() - timedelta(days=1),
        "delay_reason": "vehicle_breakdown",
    },
    {
        "tracking_number": "TRK55555",
        "customer_phone": "923004444444",
        "status": "in_transit",
        "current_hub": "Faisalabad Sorting Facility",
        "destination_city": "Lahore",
        "dispatch_date": date.today() - timedelta(days=5),
        "expected_delivery_date": date.today() - timedelta(days=2),
        "delay_reason": "shipment_damaged",
    },
]


def seed():
    db = SessionLocal()
    try:
        all_parcels = FIXED_PARCELS + generate_random_parcels(46)  # 50 total

        count = 0
        for p in all_parcels:
            existing = db.query(Parcel).filter_by(tracking_number=p["tracking_number"]).first()
            if existing:
                continue  # don't duplicate on re-run
            db.add(Parcel(**p))
            count += 1

        db.commit()
        print(f"Seeded {count} new parcels (skipped duplicates). Total requested: {len(all_parcels)}.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()