import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHROMA_API_KEY = os.getenv("CHROMA_API_KEY")
CHROMA_TENANT = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")
DATABASE_URL = os.getenv("DATABASE_URL")
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# Which WhatsApp channel send_whatsapp_message() dispatches to: "mock" (default,
# no network/quota) or "cloud" (real Meta API, wired up last). Optional — not a
# hard-required credential, so the app still boots without it.
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "mock").strip().lower()

# Phase 3 — metrics cost assumptions. Tunable business assumptions, not infra
# credentials, so these have defaults and never block startup. Keep them here (not
# hard-coded in metrics_service) so the ROI model is adjustable live during the
# defense. Human/bot PKR figures are the plan doc's own illustrative numbers
# (docs/PROJECT_PLAN.md §3); RTO_COST_PKR is a net-new placeholder for the
# round-trip-with-zero-cash-collected cost of a failed COD delivery — an illustrative
# assumption, not a sourced fact, until validated against real courier data.
HUMAN_COST_PER_QUERY_PKR = float(os.getenv("HUMAN_COST_PER_QUERY_PKR", "30"))
BOT_COST_PER_QUERY_PKR = float(os.getenv("BOT_COST_PER_QUERY_PKR", "2"))
RTO_COST_PKR = float(os.getenv("RTO_COST_PKR", "450"))

# Phase 4 — shared read-only token for the ops dashboard's API (app/core/auth.py).
# Deliberately has NO default: the dashboard reads every customer's conversations, so
# an unset token must fail closed (503) rather than leave those reads open. Real
# per-user auth is Phase 6.
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")

# Phase 5 — a SECOND, separate token for the write endpoints (claim/resolve a handoff).
# Phase 4's whole argument for a single shared token was that the ops surface could not
# write; "mark handled" ends that, so reads and writes are separately credentialled
# rather than quietly widening the read token's power. Also no default, and also fails
# closed: unset means the write endpoints 503 and the ops API is exactly as read-only
# as it was in Phase 4. Real per-user accounts remain Phase 6 — which is why the write
# endpoints require an explicit `actor` in the body instead of inferring identity.
DASHBOARD_WRITE_TOKEN = os.getenv("DASHBOARD_WRITE_TOKEN")

# Phase 5 — where staff handoff alerts go (app/core/staff_notifier.py). A separate port
# from the customer WhatsApp channel on purpose; see that module's docstring.
STAFF_NOTIFY_PROVIDER = os.getenv("STAFF_NOTIFY_PROVIDER", "log").strip().lower()

# How long a handoff stays live before it lapses back to the bot. The failure mode this
# guards is a human claiming a thread and walking away, which would otherwise silence
# the bot for that customer forever. One shift is long enough to be real and short
# enough that an abandoned claim self-heals overnight.
HANDOFF_TTL_HOURS = float(os.getenv("HANDOFF_TTL_HOURS", "8"))

# Phase 6 — the token Meta echoes back when it verifies the inbound webhook URL.
#
# Optional, and unset means the handshake behaves exactly as it did before: it echoes
# `hub.challenge` to anyone who asks. That is a deliberate default rather than an
# oversight — the endpoint is harmless while `WHATSAPP_PROVIDER=mock` (it returns a
# string the caller already supplied and touches nothing), and requiring a token by
# default would break the local simulator and every existing test for no gain today.
# Set it in Phase 7, when the URL is public and Meta is the only caller that should
# succeed. The *signature* check on inbound POSTs (X-Hub-Signature-256) is separate and
# genuinely Phase 7 work: it needs the App Secret, and implementing HMAC now with no
# real signature to verify against would only test the implementation against itself.
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

# Phase 6 — the proactive worker (app/tools/worker.py).
#
# Defaults to OFF, deliberately. Every other optional setting here defaults to the
# useful value; this one defaults to inert, because it is the only setting that makes
# the system *send messages on a timer with nobody watching*. Pulling this branch must
# not quietly start doing that — and in Phase 7 those sends become real Meta quota
# against the number reserved for the live defense.
PROACTIVE_SCAN_ENABLED = os.getenv("PROACTIVE_SCAN_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
PROACTIVE_SCAN_INTERVAL_SECONDS = int(os.getenv("PROACTIVE_SCAN_INTERVAL_SECONDS", "300"))

# Ceiling on sends per scan. Blast-radius control rather than throttling: if a bad
# migration or a clock problem suddenly makes a thousand parcels look overdue, this is
# what stops the worker messaging all thousand of them before anyone notices. 0 or unset
# means no cap, which is the reasonable default while the channel is the mock one.
_max_sends = int(os.getenv("PROACTIVE_MAX_SENDS_PER_RUN", "0"))
PROACTIVE_MAX_SENDS_PER_RUN = _max_sends if _max_sends > 0 else None

# Business-hours window (used for the after-hours-coverage % metric). Simplistic v1:
# a single hour-of-day window, no weekend/day-of-week distinction.
BUSINESS_HOURS_TIMEZONE = os.getenv("BUSINESS_HOURS_TIMEZONE", "Asia/Karachi")
BUSINESS_HOURS_START_HOUR = int(os.getenv("BUSINESS_HOURS_START_HOUR", "9"))
BUSINESS_HOURS_END_HOUR = int(os.getenv("BUSINESS_HOURS_END_HOUR", "18"))

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found in .env file")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not found in .env file")

if not CHROMA_API_KEY or not CHROMA_TENANT or not CHROMA_DATABASE:
    raise RuntimeError("Chroma Cloud credentials missing in .env file")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found in .env file")

if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
    raise RuntimeError("Upstash Redis credentials missing in .env file")