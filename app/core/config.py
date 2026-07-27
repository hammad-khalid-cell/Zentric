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