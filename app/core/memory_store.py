from typing import Optional

# In-memory session store: { customer_id: { ...session data... } }
# NOTE: This resets when the server restarts — a real Redis instance
# would persist across restarts and support automatic expiry (TTL).
_SESSION_STORE: dict[str, dict] = {}


def get_session(customer_id: str) -> Optional[dict]:
    return _SESSION_STORE.get(customer_id)


def save_session(customer_id: str, data: dict) -> None:
    existing = _SESSION_STORE.get(customer_id, {})
    existing.update(data)
    _SESSION_STORE[customer_id] = existing


def clear_session(customer_id: str) -> None:
    _SESSION_STORE.pop(customer_id, None)