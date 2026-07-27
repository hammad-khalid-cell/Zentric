import re

# Phase 3 — language-reach metric needs to know whether an interaction was handled in
# Roman Urdu (the product's differentiator vs. English-only support). A keyword-list
# heuristic keeps this at zero extra cost/latency, consistent with the rules-first
# pattern already used for intent (DELAY_KEYWORDS/FAQ_KEYWORDS) and frustration
# detection (HUMAN_REQUEST_KEYWORDS/FRUSTRATION_KEYWORDS) in app/graph/nodes.py.
# Binary only (english vs roman_urdu) — a "mixed" bucket is deferred.
ROMAN_URDU_KEYWORDS = [
    "kab", "kab tak", "kahan", "kaise", "kyun", "kyu", "kitna", "kitne",
    "mera", "meri", "mujhe", "humein", "hamara", "aap", "ap", "tum",
    "hai", "hain", "tha", "thi", "nahi", "nhi", "haan", "han",
    "abhi", "abhi tak", "der", "bohot", "bahut", "acha", "theek", "thik",
    "shukriya", "bhejo", "bhejein", "bata", "batao", "chahiye", "karo",
    "kar do", "wapas", "parcel kahan", "kaha hai", "delivery kab",
    "gussa", "pareshan", "shikayat", "faltu", "bakwas", "ghatiya",
    "banda", "insaan", "yaar", "acha ji", "theek hai", "ok ji",
]

_PATTERNS = [re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE) for kw in ROMAN_URDU_KEYWORDS]


def detect_language(text: str) -> str:
    """Returns 'roman_urdu' if the message contains any Roman Urdu marker word,
    else 'english'. A cheap heuristic, not a real language classifier — good enough
    for a reach-percentage metric."""
    for pattern in _PATTERNS:
        if pattern.search(text):
            return "roman_urdu"
    return "english"
