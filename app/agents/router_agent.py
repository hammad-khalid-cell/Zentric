import re
from app.core.groq_client import groq_chat

# Matches tracking numbers like TRK1234, PSX98765, etc.
TRACKING_PATTERN = re.compile(r"\b[A-Z]{2,5}\d{4,10}\b", re.IGNORECASE)

COMPLAINT_KEYWORDS = [
    "complaint", "complain", "angry", "worst", "refund", "manager",
    "shikayat", "bakwas", "ghalat", "pareshan", "gussa", "bura"
]

FAQ_KEYWORDS = [
    "policy", "hours", "timing", "kaise", "kitne baje", "office",
    "location", "working hours"
]


def rule_based_classify(message: str):
    text = message.lower()

    if TRACKING_PATTERN.search(message):
        return "tracking_query"

    if any(word in text for word in COMPLAINT_KEYWORDS):
        return "complaint"

    if any(word in text for word in FAQ_KEYWORDS):
        return "faq"

    return None  # unclear — needs LLM fallback


def llm_classify(message: str) -> str:
    system_prompt = (
        "You are an intent classifier for a logistics customer support system. "
        "Classify the user's message into exactly one of these categories: "
        "tracking_query, faq, complaint. "
        "Respond with ONLY the category name, nothing else."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    result = groq_chat(messages).strip().lower()

    if result not in {"tracking_query", "faq", "complaint"}:
        return "faq"  # safe default fallback

    return result


def classify_message(message: str) -> str:
    category = rule_based_classify(message)
    if category:
        return category
    return llm_classify(message)