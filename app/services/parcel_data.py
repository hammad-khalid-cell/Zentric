from datetime import date, timedelta

# Dummy in-memory parcel database.
# tracking_number -> parcel details
PARCELS = {
    "TRK12345": {
        "tracking_number": "TRK12345",
        "status": "in_transit",
        "current_hub": "Lahore Sorting Facility",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=2),
        "expected_delivery_date": date.today() + timedelta(days=1),
    },
    "TRK67890": {
        "tracking_number": "TRK67890",
        "status": "out_for_delivery",
        "current_hub": "Karachi Local Hub",
        "destination_city": "Karachi",
        "dispatch_date": date.today() - timedelta(days=3),
        "expected_delivery_date": date.today(),
    },
    "TRK99999": {
        "tracking_number": "TRK99999",
        "status": "in_transit",
        "current_hub": "Multan Sorting Facility",
        "destination_city": "Islamabad",
        "dispatch_date": date.today() - timedelta(days=6),
        "expected_delivery_date": date.today() - timedelta(days=1),  # already overdue -> delayed
    },
}


def find_parcel(tracking_number: str):
    return PARCELS.get(tracking_number.upper())

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