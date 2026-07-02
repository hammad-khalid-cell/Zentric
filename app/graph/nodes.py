import re
import json
from app.core.groq_client import client
from app.graph.state import AgentState
from app.services.parcel_data import find_parcel, find_parcels_by_phone
from datetime import date

# --- Intent Understanding Agent ---

TRACKING_PATTERN = re.compile(r"\b[A-Z]{2,5}\d{4,10}\b", re.IGNORECASE)

DELAY_KEYWORDS = [
    "delay", "delayed", "late", "der", "abhi tak nahi", "kab tak",
    "overdue", "complaint", "complain", "shikayat", "gussa", "pareshan"
]

FAQ_KEYWORDS = [
    "policy", "hours", "timing", "office", "location", "working hours",
    "kaise", "kitne baje"
]


def extract_tracking_number(message: str) -> str | None:
    match = TRACKING_PATTERN.search(message)
    return match.group(0).upper() if match else None


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    for kw in keywords:
        # \b = word boundary, so "der" won't match inside "order"
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def rule_based_intent(message: str) -> str | None:
    text = message.lower()
    has_tracking_number = extract_tracking_number(message) is not None

    if _contains_keyword(text, DELAY_KEYWORDS):
        return "delay_complaint"

    if has_tracking_number:
        return "track_order"

    if _contains_keyword(text, FAQ_KEYWORDS):
        return "faq"

    return None

def llm_intent(message: str) -> str:
    system_prompt = (
        "You are an intent classifier for a logistics customer support system. "
        "Classify the message into exactly one of: track_order, delay_complaint, faq, unclear. "
        "Respond with ONLY the category name."
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        temperature=0,
    )
    result = response.choices[0].message.content.strip().lower()
    valid = {"track_order", "delay_complaint", "faq", "unclear"}
    return result if result in valid else "unclear"


def intent_understanding_node(state: AgentState) -> AgentState:
    message = state["user_message"]

    intent = rule_based_intent(message)
    if not intent:
        intent = llm_intent(message)

    tracking_number = extract_tracking_number(message)

    state["intent"] = intent
    state["tracking_number"] = tracking_number
    return state

from app.services.parcel_data import find_parcel, find_parcels_by_phone



# --- Data Retrieval Agent ---

def data_retrieval_node(state: AgentState) -> AgentState:
    intent = state.get("intent")

    # Only track_order and delay_complaint need parcel data right now
    if intent not in {"track_order", "delay_complaint"}:
        return state

    tracking_number = state.get("tracking_number")

    # Case 1: customer gave a tracking number directly — always trust it
    if tracking_number:
        parcel = find_parcel(tracking_number)
        if parcel:
            state["retrieved_data"] = parcel
            state["clarification_needed"] = None
        else:
            state["retrieved_data"] = None
            state["clarification_needed"] = (
                f"I couldn't find a parcel with tracking number {tracking_number}. "
                "Could you double-check and share it again?"
            )
        return state

    # Case 2: no tracking number — look up by their WhatsApp number
    phone = state["customer_id"]
    matches = find_parcels_by_phone(phone)

    if len(matches) == 0:
        state["retrieved_data"] = None
        state["clarification_needed"] = (
            "I couldn't find any parcels linked to this number. "
            "Could you share your tracking ID so I can look it up?"
        )

    elif len(matches) == 1:
        state["retrieved_data"] = matches[0]
        state["clarification_needed"] = None

    else:
        # Multiple parcels — Option B: ask which one
        tracking_list = ", ".join(p["tracking_number"] for p in matches)
        state["retrieved_data"] = None
        state["clarification_needed"] = (
            f"You have {len(matches)} active parcels: {tracking_list}. "
            "Which one would you like to check?"
        )

    return state

# --- Decision Making Agent ---
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


def decision_making_node(state: AgentState) -> AgentState:
    if state.get("intent") != "delay_complaint":
        return state

    parcel = state.get("retrieved_data")
    if not parcel:
        return state

    is_delayed = parcel["expected_delivery_date"] < date.today() and parcel["status"] != "delivered"

    if not is_delayed:
        state["decision"] = "no_action"
        state["decision_reason"] = "Parcel is on track, not actually delayed."
        return state

    reason_code = parcel.get("delay_reason")
    decision = REASON_TO_DECISION.get(reason_code, "escalate")  # unknown reason -> safe default

    # LLM only writes the human-facing explanation, doesn't decide the action
    system_prompt = (
        "You are a logistics assistant. Given a parcel's delay reason and the "
        "action already decided, write ONE short, natural sentence explaining "
        "the situation to a support agent. Do not repeat raw codes verbatim."
    )
    user_prompt = (
        f"Delay reason: {reason_code or 'unknown'}\n"
        f"Decision made: {decision}\n"
        f"Days overdue: {(date.today() - parcel['expected_delivery_date']).days}"
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )

    state["decision"] = decision
    state["decision_reason"] = response.choices[0].message.content.strip()

    if decision == "escalate":
        state["needs_human_handoff"] = True

    return state