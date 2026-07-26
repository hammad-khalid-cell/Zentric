# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read the plan first (project direction)

Before any non-trivial work, read **`docs/PROJECT_PLAN.md`** (the why, the value
proposition, and the non-negotiable rules) and **`docs/ROADMAP.md`** (the living task
board — update it in the same change). `CLAUDE.md` is the *codebase* guide; the plan
docs are the *direction*. If they ever conflict, the plan docs win. Core rules that
override convenience:

- **The LLM never chooses a business action.** Decisions are deterministic and
  auditable; the LLM only phrases text and interprets free-text into a structured
  intent that a deterministic policy then acts on.
- **WhatsApp real API is integrated LAST** (to preserve free quota for the live
  defense). Until then everything goes through a **mock channel** behind the
  `send_whatsapp_message()` seam — swappable by one env setting, never a rewrite.
- **Ownership is always verified**; **guardrails stay on**; **deterministic logic
  ships with pytest tests** (mock external boundaries).
- The headline problem is **reducing COD failed-delivery/RTO cost**, not "a chatbot."

## What this is

Zentric is the backend for an agentic WhatsApp customer-support bot for a Pakistani courier/logistics
company. It classifies incoming customer messages, looks up parcel status, decides on delay actions
(notify / reroute / escalate), answers FAQs via RAG, and replies in whichever language/style
(English or Roman Urdu) the customer used. There is currently a single test-only HTTP endpoint
(`POST /test/message`) that drives the whole pipeline — there is no WhatsApp integration wired up yet.

## Commands

```bash
# install deps
pip install -r requirements.txt

# run the API locally
uvicorn app.main:app --reload

# one-time / as-needed setup against Postgres and Chroma Cloud
python -m app.core.create_tables      # creates parcels/tickets/reroutes tables
python -m app.core.seed_data          # seeds ~50 test parcels (idempotent, skips existing tracking numbers)
python -m app.services.ingest_faqs    # embeds data/logistics_customer_support_faqs.json into Chroma
```

There is no automated test suite in the repo (no pytest config, no `tests/` directory). Manual testing
is done by POSTing to `/test/message`:

```bash
curl -X POST http://localhost:8000/test/message \
  -H "Content-Type: application/json" \
  -d '{"from_number": "923001234567", "message": "TRK12345 status?"}'
```

`.gitignore` references a local `test_manual.py` script used for manual testing — it's intentionally
not committed.

Required environment variables (validated eagerly in `app/core/config.py`; the app fails to import
without all of them): `GROQ_API_KEY`, `GEMINI_API_KEY`, `CHROMA_API_KEY`, `CHROMA_TENANT`,
`CHROMA_DATABASE`, `DATABASE_URL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`.

**Quirk:** `requirements.txt` is UTF-16LE encoded (not UTF-8). Tools that assume UTF-8 will show it as
garbled/space-separated text. Preserve the encoding if editing it directly, or regenerate it with
`pip freeze`.

## Architecture

The core of the app is a LangGraph state machine, not a typical request/response controller layout.

**Request flow:** `app/routes/test_routes.py` builds an initial `AgentState` (`user_message`,
`customer_id` = the customer's phone number) and invokes `compiled_graph` from
`app/graph/build_graph.py`. All business logic lives in graph nodes, not the route handler.

**Graph topology** (`app/graph/build_graph.py`, nodes implemented in `app/graph/nodes.py`):

```
memory_load -> intent_understanding -> escalation_check --route_after_intent-->
    - "track_order"/"delay_complaint" -> data_retrieval --route_after_retrieval-->
        - clarification_needed -> response_generation
        - intent == "delay_complaint" -> decision_making -> action_execution -> response_generation
        - else -> response_generation
    - "faq" -> faq_node -> memory_save -> END
    - else -> response_generation
response_generation -> memory_save -> END
```

Every node reads/writes the single shared `AgentState` TypedDict (`app/graph/state.py`), which is the
contract between nodes — when adding a new node or field, update `state.py` first.

**Intent classification** (`intent_understanding_node`) is a two-tier system: fast keyword/regex rules
(`rule_based_intent`, `DELAY_KEYWORDS`/`FAQ_KEYWORDS`, tracking-number regex) run first; only if no rule
matches does it fall back to an LLM call (`llm_intent`) to keep cost/latency down. The same
rules-then-LLM-fallback pattern is used again in `escalation_check_node` for frustration/human-handoff
detection (`rule_based_frustration_check` first, `llm_frustration_check` as fallback).

**Decision making is deterministic, not LLM-driven.** `decision_making_node` maps a parcel's
`delay_reason` code to an action (`notify` / `reroute` / `escalate`) via the fixed `REASON_TO_DECISION`
dict; unknown reason codes default to `escalate` (safe default). The LLM is only used afterwards to
phrase a human-readable explanation of a decision that's already been made — it never chooses the
action itself.

**Clarification / multi-turn handling:** if a customer's phone number matches zero, one, or multiple
parcels, `data_retrieval_node` either asks for a tracking number, resolves the single match, or asks
which of several parcels they mean. `memory_save_node` persists a `pending_clarification` marker to
Redis (via `app/core/memory_store.py`) so the next inbound message from that customer is treated by
`intent_understanding_node` as a continuation (it looks for a bare tracking number in the reply) rather
than reclassified from scratch.

**Session memory** (`app/core/memory_store.py`) is Upstash Redis accessed over its REST API, keyed by
customer phone number, with a 30-minute TTL. It stores `pending_clarification` state and the
last-message/repeat-count pair used for repeated-query escalation detection. It's a flat
key -> JSON-blob store, not a chat transcript.

**Persistent data** is Postgres via SQLAlchemy (`app/core/database.py`, models in `app/models/`):
`Parcel` (tracking_number PK, status, hub, delay_reason, ...), `Ticket` (escalations), `Reroute`
(reroute requests). Service functions (`app/services/parcel_data.py`, `app/services/action_service.py`)
each open and close their own `SessionLocal()` rather than using FastAPI's `Depends(get_db)` — `get_db`
is defined but not currently wired into any route.

**FAQ answering is RAG-based**, separate from the main delay/tracking logic:
`app/services/ingest_faqs.py` embeds `data/logistics_customer_support_faqs.json` (question+answer
combined) via Gemini (`app/core/embeddings.py`, model `gemini-embedding-2`) into a Chroma Cloud
collection named `faqs` (`app/services/vector_store.py`). `app/agents/faq_agent.py` embeds the
customer's question, retrieves top-k similar FAQs, and asks the LLM to answer using only that
retrieved context (told explicitly not to invent policy not present in the FAQs).

**LLM calls** go directly to Groq (`app/core/groq_client.py`) using `llama-3.3-70b-versatile` for
classification/decision/response generation, called ad hoc from within node functions rather than
through a shared prompt/response abstraction — when changing prompt style or model, check all call
sites in `app/graph/nodes.py` and `app/agents/faq_agent.py` rather than a single shared place.

**Language handling:** there's no translation layer — every LLM prompt that produces customer-facing
text (response generation, FAQ answers) is explicitly instructed to mirror the customer's own
language/register (plain English vs. Roman Urdu vs. a mix), professionally, in 1-3 sentences, no
markdown, WhatsApp-appropriate.
