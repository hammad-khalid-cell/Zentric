from datetime import date, timedelta

DELAY_REASONS = [
    "customer_unavailable",
    "incorrect_address",
    "consignee_requested_reschedule",
    "weather_delay",
    "vehicle_breakdown",
    "shipment_damaged",
    "security_restrictions",
    "payment_issue_cod",
    "operational_delay",
    "linehaul_delay",
]

PARCELS = {
    "TRK12345": {
        "tracking_number": "TRK12345",
        "customer_phone": "923001234567",
        "status": "in_transit",
        "current_hub": "Lahore Sorting Facility",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=2),
        "expected_delivery_date": date.today() + timedelta(days=1),
        "delay_reason": None,
    },
    "TRK67890": {
        "tracking_number": "TRK67890",
        "customer_phone": "923001234567",
        "status": "out_for_delivery",
        "current_hub": "Karachi Local Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=3),
        "expected_delivery_date": date.today(),
        "delay_reason": None,
    },
    "TRK99999": {
        "tracking_number": "TRK99999",
        "customer_phone": "923009999999",
        "status": "in_transit",
        "current_hub": "Multan Sorting Facility",
        "destination_city": "Islamabad",
        "dispatch_date": date.today() - timedelta(days=6),
        "expected_delivery_date": date.today() - timedelta(days=1),
        "delay_reason": "vehicle_breakdown",
    },
    "TRK55555": {
    "tracking_number": "TRK55555",
    "customer_phone": "923004444444",
    "status": "in_transit",
    "current_hub": "Faisalabad Sorting Facility",
    "destination_city": "Lahore",
    "dispatch_date": date.today() - timedelta(days=5),
    "expected_delivery_date": date.today() - timedelta(days=2),
    "delay_reason": "shipment_damaged",
},
}
def find_parcel(tracking_number: str):
    return PARCELS.get(tracking_number.upper())


def find_parcels_by_phone(phone_number: str) -> list[dict]:
    """Returns all parcels linked to a given WhatsApp number."""
    return [p for p in PARCELS.values() if p["customer_phone"] == phone_number]

def get_shipment_status(tracking_number: str) -> dict:
    parcel = find_parcel(tracking_number)
    if not parcel:
        return {"error": f"No parcel found with tracking number {tracking_number}"}
    return {
        "tracking_number": parcel["tracking_number"],
        "status": parcel["status"],
        "current_hub": parcel["current_hub"],
    }


def get_expected_delivery(tracking_number: str) -> dict:
    parcel = find_parcel(tracking_number)
    if not parcel:
        return {"error": f"No parcel found with tracking number {tracking_number}"}
    return {
        "tracking_number": parcel["tracking_number"],
        "expected_delivery_date": str(parcel["expected_delivery_date"]),
    }


def check_delay(tracking_number: str) -> dict:
    parcel = find_parcel(tracking_number)
    if not parcel:
        return {"error": f"No parcel found with tracking number {tracking_number}"}

    is_delayed = parcel["expected_delivery_date"] < date.today() and parcel["status"] != "delivered"

    return {
        "tracking_number": parcel["tracking_number"],
        "is_delayed": is_delayed,
        "expected_delivery_date": str(parcel["expected_delivery_date"]),
        "status": parcel["status"],
    }