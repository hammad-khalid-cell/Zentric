# Zentric — Roadmap & Task Board (living document)

> Update this file **in the same change** that does the work. Check boxes, add
> discovered sub-tasks, move items between phases. Pair it with
> `docs/PROJECT_PLAN.md` (the why/rules). Status legend: `[ ]` todo · `[~]` in
> progress · `[x]` done. Priorities: **P0** (blocks the value prop) · P1 (makes it
> worth it / defensible) · P2 (robustness & polish).

Last updated: 2026-07-26 (Phase 2 — proactive loop closed).

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

## Phase 1 — Mock WhatsApp channel (two-way) — **P0** — ✅ done

Goal: demo the full product with **zero** WhatsApp quota used. Swappable with the
real API by one env setting later (Phase 7).

- [x] Define `WhatsAppChannel` interface (`send(phone, message)`) — `app/core/whatsapp_client.py`
- [x] `MockWhatsAppChannel`: persist outbound to a new `messages` table (direction=out)
- [x] Route `send_whatsapp_message()` through the selected channel (env `WHATSAPP_PROVIDER=mock|cloud`)
- [x] `Message` model + `create_tables` registration (in/out, body, tracking_number?, timestamp)
- [x] `POST /webhook/whatsapp` — accepts a Meta-shaped payload, feeds `compiled_graph`, logs inbound
- [x] Persist inbound messages to `messages` too — `app/services/message_log.py`
- [x] Minimal **customer simulator** CLI (`python -m app.tools.sim`) that posts to the webhook
- [x] Tests: channel selection, outbound capture, inbound webhook → graph (10 new, 80 total)
- [x] GET `/webhook/whatsapp` verification handshake (echoes hub.challenge) — verified via TestClient

**Acceptance:** ✅ a simulated customer message flows in via the webhook, the reply is
sent via the mock channel, and both are persisted as a conversation.

**One-time setup after pulling:** run `python -m app.core.create_tables` to create the
new `messages` table.

---

## Phase 2 — Close the proactive loop — **P0 (the RTO money lever)** — ✅ done

Goal: proactive message → customer reply → **corrective action** → changed outcome.

- [x] `state.py`: add fields for corrective intents/actions (contract first) — `pending_action`, `corrective_intent`, `corrective_payload`
- [x] Pending-action store (parcel-scoped, TTL > session) linking a proactive contact to its expected reply — `pending_actions` table + `app/core/pending_actions.py` (48h TTL, lazy expiry)
- [x] Reply interpretation: free-text → structured intent (`reschedule` / `update_address` / `available_window` / `cancel`) — LLM *interprets* (`interpret_reply_node`), deterministic policy *acts* (`CORRECTIVE_INTENT_TO_ACTION`)
- [x] New deterministic actions in decision/action layer: `reschedule`, `update_address`
- [x] `Parcel`: updatable address fields + preferred delivery window + attempt counter (`address_line`, `preferred_delivery_window`, `attempt_count`)
- [x] `action_execution`: apply corrective action, write auditable row — `apply_address_update` / `apply_reschedule` + `interventions` audit table
- [x] Route a reply that matches a pending action into the corrective path (not fresh classification) — `route_after_intent` gates on `pending_action`
- [x] Tests: each corrective path, pending-action expiry, wrong/ambiguous replies (32 new, 112 total)
- [x] `proactive_notifier` opens a pending action for `notify`-reason parcels so the reply connects
- [x] Ownership verified on the corrective write-back (parcel only mutated for the owning number)

**Acceptance:** ✅ proactive notify for an "incorrect_address" parcel → customer replies
with a new address → parcel address updated + attempt rescheduled + audit row written.

**One-time setup after pulling:** run `python -m app.core.create_tables` to create the
new `pending_actions` and `interventions` tables, then apply the `parcels` `ALTER TABLE`
migration and re-seed. `create_tables` only *creates* new tables — the new `Parcel`
columns (`address_line`, `preferred_delivery_window`, `attempt_count`) need a manual
`ALTER TABLE` on an existing DB. Full steps + SQL in **`docs/MIGRATIONS.md`**.

---

## Phase 3 — Outcome tracking & metrics — **P0 (ROI evidence for defense)** — ✅ done

Goal: produce the numbers §3/§9 of the plan promise.

- [x] `DeliveryAttempt` model (attempt_no, outcome, failure_reason, timestamp) — `app/models/delivery_attempt.py`, unique per `(tracking_number, attempt_no)`
- [x] `InterventionOutcome` model linking intervention → later delivery outcome — `app/models/intervention_outcome.py`
- [x] `Interaction` model (new, not originally named) — one row per graph run; the raw material for deflection rate, cost, response time, after-hours %, language reach % — `app/models/interaction.py`
- [x] Instrument the graph + proactive loop to record attempts/interventions/deflections — `decision_making_node` + `scan_and_notify` record the organic first-failure `DeliveryAttempt`; `record_interaction()` is called from both `compiled_graph.invoke()` call sites (`test_routes.py`, `whatsapp_inbound.py`)
- [x] `app/services/delivery_service.py::record_attempt_outcome()` — writes the `DeliveryAttempt` and links an unresolved `Intervention` to an `InterventionOutcome` when one resolves
- [x] Metrics service: deflection rate, cost-per-interaction, **RTO reduction %**, response time, after-hours %, language reach % — `app/services/metrics_service.py`, pure `compute_*` functions over plain dicts + `get_metrics_report()`
- [x] Cost assumptions in **config** (human PKR/query, bot PKR/query, PKR/RTO) — tunable live — `app/core/config.py` (`HUMAN_COST_PER_QUERY_PKR`, `BOT_COST_PER_QUERY_PKR`, `RTO_COST_PKR`, business-hours window)
- [x] Seed/simulate a believable dataset so metrics are non-trivial in the demo — `python -m app.tools.simulate_outcomes` (weighted resolver for open interventions; a real delivery-system webhook would call the same `record_attempt_outcome` seam later, Phase 6)
- [x] `GET /metrics/report` endpoint (`app/routes/metrics_routes.py`), optional `since`/`until`
- [x] Tests: metric calculations against known fixtures (30 new — `test_delivery_service.py`, `test_metrics_service.py`, `test_interaction_log.py`, `test_language_detect.py`; 135 total)

**Acceptance:** ✅ `GET /metrics/report` returns real KPI values derived from the
system's own recorded `Interaction`/`InterventionOutcome` rows.

**One-time setup after pulling:** run `python -m app.core.create_tables` to create the
new `delivery_attempts`, `intervention_outcomes`, and `interactions` tables (all
brand-new — no `ALTER TABLE` needed this phase). Requires the `tzdata` package
(added to `requirements.txt`) for `zoneinfo` to resolve `Asia/Karachi` on Windows,
where no system tz database is installed by default.

**Known simplification:** `RTO_COST_PKR` (default 450) is an illustrative placeholder,
not sourced from real courier data — present it as tunable/parameterised in the
defense, never as fact, per `docs/PROJECT_PLAN.md` §3.

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
