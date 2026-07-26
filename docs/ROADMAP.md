# Zentric — Roadmap & Task Board (living document)

> Update this file **in the same change** that does the work. Check boxes, add
> discovered sub-tasks, move items between phases. Pair it with
> `docs/PROJECT_PLAN.md` (the why/rules). Status legend: `[ ]` todo · `[~]` in
> progress · `[x]` done. Priorities: **P0** (blocks the value prop) · P1 (makes it
> worth it / defensible) · P2 (robustness & polish).

Last updated: 2026-07-26.

---

## Phase 0 — Foundation (guardrails, refactor, tests) — ✅ mostly done

- [x] LangGraph pipeline, intent (rules+LLM), decision rules, actions, FAQ RAG
- [x] Postgres models (Parcel/Ticket/Reroute/Notification), Redis session + rate limit
- [x] Guardrails: input validation, rate limiting, prompt-injection framing, LLM fallbacks
- [x] Ownership verification on parcel lookups
- [x] Duplicate-action prevention + race-free ID generation
- [x] Refactor data retrieval into `app/agents/tracking_agent.py`
- [x] Fix `memory_save_node` returning `None`; remove duplicate graph edge
- [x] pytest suite (70 tests) + `docs`-style TEST_REPORT
- [ ] Wire an autouse dummy-env fixture so tests run in CI without real secrets (P2)

---

## Phase 1 — Mock WhatsApp channel (two-way) — **P0**

Goal: demo the full product with **zero** WhatsApp quota used. Swappable with the
real API by one env setting later (Phase 7).

- [ ] Define `WhatsAppChannel` interface (`send(phone, message)`)
- [ ] `MockWhatsAppChannel`: persist outbound to a new `messages` table (direction=out)
- [ ] Route `send_whatsapp_message()` through the selected channel (env `WHATSAPP_PROVIDER=mock|cloud`)
- [ ] `Message` model + migration (in/out, body, tracking_number?, timestamp)
- [ ] `POST /webhook/whatsapp` — accepts a Meta-shaped payload, feeds `compiled_graph`, logs inbound
- [ ] Persist inbound messages to `messages` too (full conversation thread)
- [ ] Minimal **customer simulator** (web page or CLI) that posts to the webhook
- [ ] Tests: channel selection, outbound capture, inbound webhook → graph

**Acceptance:** a simulated customer message flows in via the webhook, the reply is
sent via the mock channel, and both are persisted as a conversation.

---

## Phase 2 — Close the proactive loop — **P0 (the RTO money lever)**

Goal: proactive message → customer reply → **corrective action** → changed outcome.

- [ ] `state.py`: add fields for corrective intents/actions (contract first)
- [ ] Pending-action store (parcel-scoped, TTL > session) linking a proactive contact to its expected reply
- [ ] Reply interpretation: free-text → structured intent (`reschedule` / `update_address` / `available_window` / `cancel`) — LLM *interprets*, deterministic policy *acts*
- [ ] New deterministic actions in decision/action layer: `reschedule`, `update_address`
- [ ] `Parcel`: updatable address fields + preferred delivery window + attempt counter
- [ ] `action_execution`: apply corrective action, write auditable row
- [ ] Route a reply that matches a pending action into the corrective path (not fresh classification)
- [ ] Tests: each corrective path, pending-action expiry, wrong/ambiguous replies

**Acceptance:** proactive notify for an "incorrect_address" parcel → customer replies
with a new address → parcel address updated + attempt rescheduled + audit row written.

---

## Phase 3 — Outcome tracking & metrics — **P0 (ROI evidence for defense)**

Goal: produce the numbers §3/§9 of the plan promise.

- [ ] `DeliveryAttempt` model (attempt_no, outcome, failure_reason, timestamp)
- [ ] `InterventionOutcome` model linking intervention → later delivery outcome
- [ ] Instrument the graph + proactive loop to record attempts/interventions/deflections
- [ ] Metrics service: deflection rate, cost-per-interaction, **RTO reduction %**, response time, after-hours %, language reach %
- [ ] Cost assumptions in **config** (human PKR/query, bot PKR/query, PKR/RTO) — tunable live
- [ ] Seed/simulate a believable dataset so metrics are non-trivial in the demo
- [ ] Tests: metric calculations against known fixtures

**Acceptance:** a metrics endpoint/report returns real KPI values derived from the
system's own recorded outcomes.

---

## Phase 4 — Ops / KPI dashboard (frontend) — P1

Goal: the "worth it" artifact + demo centerpiece + defense metrics visualization.

- [ ] Conversation view (per customer, in/out thread from `messages`)
- [ ] Tickets / reroutes / interventions list with status
- [ ] KPI panel wired to the metrics service (cards + trend)
- [ ] Live ROI calculator (plug in volume/COD%/failure rate/agent cost → savings)
- [ ] Read-only auth for the dashboard

**Acceptance:** open the dashboard, watch a live conversation and the KPIs update.

---

## Phase 5 — Human handoff — P1

- [ ] On escalation, notify staff (channel/queue) — not just a flag/ticket
- [ ] A place a human can view the thread and mark it handled (dashboard tie-in)
- [ ] Suppress bot auto-replies once a human has taken over
- [ ] Tests: handoff routing + bot suppression

---

## Phase 6 — Robustness & polish — P2

- [ ] Scheduler/worker for `scan_and_notify` (cron/queue) + retries + dead-letter
- [ ] Conversation history / richer memory beyond flat 30-min blob
- [ ] Auth on inbound webhook + admin endpoints
- [ ] Mock "delivery management system" so reroute/reschedule visibly changes state
- [ ] Realistic, larger seed dataset
- [ ] Observability: structured logging, basic metrics/tracing
- [ ] Delivery-receipt handling (once real API exists)

---

## Phase 7 — Real WhatsApp Cloud API — **P0 but LAST**

> Deliberately last to preserve free quota for the live defense.

- [ ] `CloudApiWhatsAppChannel` implementing `WhatsAppChannel`
- [ ] Real Meta webhook verification + signature check
- [ ] Flip `WHATSAPP_PROVIDER=cloud`; smoke-test end-to-end on the free test number
- [ ] Fallback to mock if quota/credentials unavailable during defense

---

## Cross-cutting — Evaluation harness — P1

Backs the safety/quality claims with evidence.

- [ ] Adversarial set: prompt injection, other-customer tracking numbers, ambiguous reasons
- [ ] Decision-correctness benchmark over `REASON_TO_DECISION` + edge cases
- [ ] Naive pure-LLM-agent baseline for comparison
- [ ] Report generator: comparison tables (naive vs constrained), robustness %

---

## Discovered / parking lot

- [ ] Roman-Urdu code-switched labeled dataset + classification accuracy report (optional novelty artifact)
- [ ] Merchant-facing notifications (COD sale protected)
- [ ] Address geocoding/validation
