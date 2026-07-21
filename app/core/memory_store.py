import json
from upstash_redis import Redis
from app.core.config import UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN

redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)

SESSION_TTL_SECONDS = 30 * 60  # 30 minutes of inactivity expires the session

RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 60


def get_session(customer_id: str) -> dict | None:
    raw = redis.get(customer_id)
    if raw is None:
        return None
    return json.loads(raw)


def save_session(customer_id: str, data: dict) -> None:
    existing = get_session(customer_id) or {}
    existing.update(data)
    redis.set(customer_id, json.dumps(existing, default=str), ex=SESSION_TTL_SECONDS)


def clear_session(customer_id: str) -> None:
    redis.delete(customer_id)


def check_rate_limit(customer_id: str) -> bool:
    """Fixed-window limiter: allows RATE_LIMIT_MAX_REQUESTS per customer per window.

    Returns True if this request is allowed, False if the customer should be rejected.
    """
    key = f"ratelimit:{customer_id}"
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return count <= RATE_LIMIT_MAX_REQUESTS