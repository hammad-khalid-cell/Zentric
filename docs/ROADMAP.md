# Zentric — Roadmap & Task Board (living document)

> Update this file **in the same change** that does the work. Check boxes, add
> discovered sub-tasks, move items between phases. Pair it with
> `docs/PROJECT_PLAN.md` (the why/rules). Status legend: `[ ]` todo · `[~]` in
> progress · `[x]` done. Priorities: **P0** (blocks the value prop) · P1 (makes it
> worth it / defensible) · P2 (robustness & polish).

Last updated: 2026-07-29 (Phase 5 — human handoff, bot suppression, ops write API).

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
- [ ] Wire an autouse dummy-env fixture so tests run in CI without real secrets (P2).
      *Narrowed:* the suite is now genuinely offline (see parking lot), but importing
      `app.core.config` still **raises** without real credentials in `.env`, so CI needs
      dummy values injected before collection. That's all that's left of this item.

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

## Phase 4 — Ops / KPI dashboard (frontend) — P1 — 🚧 in progress

Goal: the "worth it" artifact + demo centerpiece + defense metrics visualization.

**Decisions taken** (so a later session doesn't relitigate them):
- **Frontend:** no-build static SPA in `app/static/`, served by FastAPI at `/dashboard`;
  vanilla JS, hand-rolled inline SVG charts, no CDN (the defense machine may be offline).
  One process, one URL, nothing to `npm build` on demo morning.
- **Live updates:** polling with an id cursor (~4s conversations/cases, ~20s KPIs),
  *not* websockets — the demo runs `app.tools.sim` / `simulate_outcomes` / the proactive
  scanner as **separate processes**, so any in-process pub/sub would never see them.
  Postgres is the only shared state. Survives the Phase 7 swap unchanged; SSE is a
  later drop-in.
- **ROI model:** computed server-side and pytest-tested — it's a business claim we
  defend, so it must be one deterministic implementation, not duplicated in JS.
- **No new tables and no `state.py` change** this phase: it's read-only over what
  Phases 1–3 already record.

### Backend (read-only ops API)

- [x] Read-only auth for the dashboard — `app/core/auth.py`, shared `DASHBOARD_TOKEN`
      bearer token, **fails closed with 503 when unset**. Gates `/ops/*` *and*
      `/metrics/report`. Rationale: an unauthenticated per-phone read would be exactly
      the ownership oracle `PROJECT_PLAN.md` §5.3 forbids; the customer-facing
      ownership check in `tracking_agent.py` is untouched.
- [x] `GET /ops/conversations` — per-customer summary (counts, last message,
      `last_message_id` as the poll cursor)
- [x] `GET /ops/conversations/{phone}` — in/out thread, `since_id` for incremental polls
- [x] `GET /ops/cases` — tickets + reroutes + interventions as one normalised feed,
      filterable by `type`/`status`. An **intervention's status is derived** (`open` /
      `delivered` / `still_failed`) from whether an `InterventionOutcome` exists — no
      status column, no schema change.
- [x] Tests: auth gate (fail-closed, bad headers, constant-time compare), derived
      status, case merge/filter/limit, thread ordering + cursor, route validation
      (28 new, 163 total)
- [x] `GET /metrics/timeseries?days=N` — daily buckets for the trend chart, pure
      `compute_daily_series` beside the existing `compute_*` fns (N sliding calls to
      `/metrics/report` would be N full table scans). Zero-fills quiet days so the
      chart's x-axis stays continuous; buckets by *local* Asia/Karachi date so it
      agrees with the after-hours metric; splits the two savings levers
      (`support_saving_pkr` credits only deflected interactions — an escalated one
      still cost a human — and `rto_saving_pkr`)
- [x] `GET /ops/roi/assumptions` + `POST /ops/roi/simulate` — `app/services/roi_service.py`,
      pure + pytest-tested. Its defaults **re-derive the §3 savings table** (~PKR 36M
      support, ~PKR 65M RTO, RTO ≈ 1.9× support), and a test asserts that, so the plan
      doc and the calculator can't drift apart and quote different headlines.
      `/roi/assumptions` reports the system's *measured* deflection/success rates
      **separately from** the model defaults and **with sample sizes** — right now that's
      100% off n=3 and n=1, which must look as weak as it is rather than authoritative.
- [x] `proactive_notifier` didn't pass `tracking_number` to `send_whatsapp_message`
      (`proactive_notifier.py:79`), so proactive outbound rows landed in `messages` with a
      null tracking number and the dashboard thread couldn't say which parcel they were
      about — found while smoke-testing the ops API. Fixed, and pinned by the first
      tests this module has had (`tests/test_proactive_notifier.py`, 5 new): tracking-number
      propagation, pending action opened for `notify` reasons only, already-notified skip

### Frontend — `app/static/`, served at `/dashboard`

- [x] Conversation view (per customer, in/out thread from `messages`), polled with the
      `since_id` cursor so a live thread costs one small query per tick
- [x] Tickets / reroutes / interventions list with status + type filter
- [x] KPI panel wired to the metrics service (6 stat tiles + 2 charts). The range
      control scopes tiles *and* charts: the trend defines the window and hands its
      `start_at` to `/metrics/report`, so the cards and the chart can't disagree
- [x] Live ROI calculator (10 sliders → savings), labelled **illustrative & tunable**
      per `PROJECT_PLAN.md` §3 — never presented as fact
- [x] Browser customer simulator page at `/simulator` — posts to the existing
      `/webhook/whatsapp` (customer surface, no new backend, `send_whatsapp_message()`
      seam untouched) so the "live" demo has a traffic source on the same screen
- [x] **Spread the demo dataset over several days** — `python -m app.tools.seed_demo_history`
      writes backdated `Interaction` / `Message` / `Intervention` / `DeliveryAttempt` /
      `InterventionOutcome` chains with explicit `created_at` (the server default was why
      everything piled onto one day). Generation is split into a pure, seeded
      `plan_history()` and a `write_plan()` that only inserts, so the shape is testable
      without a DB (`tests/test_seed_demo_history.py`, 11 new). Weekday/weekend volume
      shape and a real after-hours tail; **no artificial growth ramp** — a fabricated
      adoption curve would claim something the system hasn't earned. Defaults to **18**
      days because history ends *yesterday*, so a 14-day generation is already missing
      its oldest bucket and sheds one more per day; the extra days are slack.
      All 14 chart buckets now populated (deflection 33–100%/day, RTO 76.5% off n=34).
      ⚠️ **This is MODELLED history, not observed** — rows are marked (`DEMO`-prefixed
      tracking numbers, reserved `92300900xxxx` phones), removable with `--wipe`, and must
      be presented as modelled, exactly like `RTO_COST_PKR` (`PROJECT_PLAN.md` §3).
- [~] Eyeball the rendered dashboard in a browser — **still open**, no browser tooling
      available in either session. A code-read pass (the half that doesn't need eyes)
      found and fixed three things screenshots wouldn't have shown anyway:
      the conversation list is a 420px scroll box that the 4s poll tore down and rebuilt
      unconditionally, resetting scroll position and dropping keyboard focus every tick
      (now change-detected by signature + scroll preserved) — much more visible since the
      demo history added ~40 threads; and `selectConversation` (click) and `runRoi`
      (debounce timer) were unguarded async, so a DNS blip left an unhandled rejection
      with the status pill still reading "Live" (both wrapped in `guarded` now).
      **Still needs a human to look at it**: layout, dark mode, long Roman-Urdu strings
      in bubbles, and the charts at real widths.

**Charts** follow the dataviz method: two categorical slots (blue = support lever,
orange = RTO lever, validated in both light and dark — worst adjacent CVD ΔE 24.7/26.8),
colour follows the entity so a filter never repaints it, no dual axis (savings and
deflection rate are two charts, not two scales), 2px surface gaps rather than strokes,
selective direct labels, a table-view twin behind every chart, crosshair/hover tooltips
with ≥24px hit targets, and status shown as icon + label rather than colour alone.

**Acceptance:** ✅ open the dashboard, watch a live conversation and the KPIs update.

**One-time setup after pulling:** no migration this phase, but set `DASHBOARD_TOKEN`
in `.env` — without it the ops API and `/metrics/report` return 503 by design. Then
`uvicorn app.main:app` and open `/dashboard` (ops) and `/simulator` (customer side).

---

## Phase 5 — Human handoff — P1 — ✅ done

**Decisions taken** (so a later session doesn't relitigate them):
- **Two tokens, not one.** Phase 4's argument for a single shared `DASHBOARD_TOKEN` was
  that the ops surface *could not write*. "Mark handled" is the first real write, so
  rather than quietly widening the read token, writes need a separate
  `DASHBOARD_WRITE_TOKEN`. The defensible claim changes shape but survives: it is no
  longer "the dashboard is read-only" but **"a holder of the read token alone still
  cannot write"** — and with the write token unset, the API *is* exactly Phase 4's.
- **Writes live in their own router** (`app/routes/ops_write_routes.py`), not bolted
  onto `ops_routes.py`, so that file's "every endpoint here is a GET behind the read
  token" invariant stays true and checkable by reading one file. A test asserts it
  structurally.
- **A separate notification port**, not the WhatsApp seam. `send_whatsapp_message()` is
  the *customer* channel — its mock persists to `messages` keyed by `customer_phone`,
  so staff alerts through it would inject internal notices into customer threads and
  would spend real Meta quota in Phase 7. Same shape, same one-env swap, different
  destination.
- **Conversation-scoped, in its own table.** Not a `Ticket` column (parcel-scoped,
  `tracking_number NOT NULL`, wrong grain — the most common trigger has no parcel at
  all) and not Redis (ephemeral; this is an auditable business-affecting state change
  per §5.2 that the dashboard reads from Postgres).
- **Only `claimed` suppresses the bot**, not `open`. An open handoff means staff were
  alerted but nobody has picked it up — silence then leaves the customer with nothing.

- [x] On escalation, notify staff (channel/queue) — not just a flag/ticket.
      **The gap was worse than the roadmap said:** `escalation_check_node` set
      `needs_human_handoff`, produced a soothing reply, and created *nothing* durable —
      no ticket (those need a parcel), no queue entry. Now `raise_handoff()` opens a
      `Handoff` row and alerts staff via the new `app/core/staff_notifier.py` seam
      (`STAFF_NOTIFY_PROVIDER=log|slack|email`, default `log`, no network/quota). The
      delay path's `decision == "escalate"` links its handoff to the `Ticket` it creates.
      Alerts follow the *row*, not the message, so three angry messages raise one alert.
      Notification delivery is itself audited (`notified_at` / `notify_failed`).
- [x] A place a human can view the thread and mark it handled — Handoffs pane on
      `/dashboard`, with **two** transitions rather than one: *Take* (`open → claimed`)
      is what silences the bot, *Resolve* (`claimed → resolved`) restores it. Collapsing
      them would conflate "I'm on it" with "it's done" and make the suppression window
      invisible. `actor` is required and never defaulted — there are no staff accounts
      yet (Phase 6), so an explicit name is the honest audit record. Repeat clicks get a
      409 rather than silently rewriting `claimed_at`/`resolved_at`.
- [x] Suppress bot auto-replies once a human has taken over — `human_handoff` /
      `handoff_suppressed` added to `state.py` (contract first), loaded in
      `memory_load_node`, and short-circuited by `route_after_memory_load` into a
      terminal `handoff_hold` node **before** intent classification, so a human-owned
      turn costs no LLM call. The customer's message is still logged inbound (that
      happens before the graph), so the human sees it; the bot simply sends nothing —
      a "someone will be with you shortly" on every message would interleave bot text
      into a conversation a person is handling. `needs_human_handoff` is set, so the
      existing `record_interaction` books the turn as `resolved_by='human'` and it
      never inflates the deflection rate. `HANDOFF_TTL_HOURS` (default 8, one shift)
      lazily expires an abandoned claim so a human who walks away can't silence the bot
      for that customer forever.
- [x] Tests: handoff routing + bot suppression — 62 new (287 total).
      `test_handoff.py` (store lifecycle, idempotency, expiry sweep, notifier seam +
      an AST check that it never imports the customer WhatsApp channel),
      `test_handoff_routing.py` (routing, suppression, escalation → handoff,
      end-to-end through `process_inbound_message`), `test_ops_write_routes.py`
      (**the read token cannot write** — the load-bearing auth test — plus attribution,
      409s, and a structural guard that no mutating route lands in the read router).

**Acceptance:** ✅ an escalation raises a handoff and alerts staff; taking it on the
dashboard silences the bot for that customer; resolving it hands the thread back.

**One-time setup after pulling:** `python -m app.core.create_tables` for the new
`handoffs` table, then set `DASHBOARD_WRITE_TOKEN` (a *different* value from
`DASHBOARD_TOKEN`) — without it the handoff buttons return 503 by design. See
**`docs/MIGRATIONS.md`**.

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

- [ ] Link `messages` to `interactions` (shared id) so the dashboard can show latency
      per reply instead of correlating by phone + timestamp — noted while building Phase 4
- [x] **The test suite depended on live network** despite `tests/conftest.py` claiming
      otherwise. Three causes, all fixed: `vector_store` built its Chroma Cloud client at
      *import* time (so a DNS blip failed collection of modules that never touch RAG — it's
      lazy now), `decision_making_node`'s `record_attempt_outcome` opened a real Postgres
      connection in `test_decision.py`, and LangSmith tracing phoned home on every graph
      invoke. `conftest.py` now **enforces** offline with an autouse socket block, so this
      class of regression fails loudly at the call site instead of intermittently. 209
      passed in 5.6s with no network. Opt out per-test with `@pytest.mark.allow_network`.
- [ ] Roman-Urdu code-switched labeled dataset + classification accuracy report (optional novelty artifact)
- [ ] Merchant-facing notifications (COD sale protected)
- [ ] Address geocoding/validation
