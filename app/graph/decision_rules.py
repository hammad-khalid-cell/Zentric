REASON_TO_DECISION = {
    "customer_unavailable": "notify",
    "incorrect_address": "notify",
    "consignee_requested_reschedule": "notify",
    "weather_delay": "notify",
    "vehicle_breakdown": "reroute",
    "operational_delay": "reroute",
    "linehaul_delay": "reroute",
    "shipment_damaged": "escalate",
    "security_restrictions": "escalate",
    "payment_issue_cod": "escalate",
}

# Phase 2 — the corrective side of the proactive loop. The LLM interprets a
# customer's free-text reply into one of these structured intents; this fixed map
# then deterministically chooses the business action. The LLM never picks the action.
#   update_address / reschedule -> apply a corrective write-back to the parcel
#   available_window            -> a reschedule with a stated window
#   cancel                      -> hand to a human (escalate), never auto-cancel a COD order
#   unclear                     -> ask the customer to clarify; take NO action, keep the
#                                  pending action open
CORRECTIVE_INTENT_TO_ACTION = {
    "update_address": "update_address",
    "reschedule": "reschedule",
    "available_window": "reschedule",
    "cancel": "escalate",
    "unclear": "clarify",
}
