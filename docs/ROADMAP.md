# Zentric — Roadmap & Task Board (living document)

> Update this file **in the same change** that does the work. Check boxes, add
> discovered sub-tasks, move items between phases. Pair it with
> `docs/PROJECT_PLAN.md` (the why/rules). Status legend: `[ ]` todo · `[~]` in
> progress · `[x]` done. Priorities: **P0** (blocks the value prop) · P1 (makes it
> worth it / defensible) · P2 (robustness & polish).

Last updated: 2026-08-06 (Phase 6 — delivery state machine, proactive worker, webhook handshake).

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
- [x] Eyeball the rendered dashboard in a browser — **done 2026-08-06**, findings below.
      A code-read pass (the half that doesn't need eyes)
      found and fixed three things screenshots wouldn't have shown anyway:
      the conversation list is a 420px scroll box that the 4s poll tore down and rebuilt
      unconditionally, resetting scroll position and dropping keyboard focus every tick
      (now change-detected by signature + scroll preserved) — much more visible since the
      demo history added ~40 threads; and `selectConversation` (click) and `runRoi`
      (debounce timer) were unguarded async, so a DNS blip left an unhandled rejection
      with the status pill still reading "Live" (both wrapped in `guarded` now).
      Attempted 2026-07-31 with browser tooling available, but the Chrome extension
      wasn't connected. **Finally rendered and inspected 2026-08-06** (extension
      connected, live server, real Postgres). What the eyes confirmed: light *and* dark
      both read correctly, Roman-Urdu bubbles render clean at full width, the Handoffs
      pane and its Take/Resolve buttons work (first time seen), and the ROI hero
      re-derives the plan's own figures on screen (PKR 35.3M + 65.6M). `Resolve`
      showing on an `open` row looked wrong but is deliberate — `resolve_handoff`
      allows it for a case settled out of band. What the eyes caught that no code read
      would have:
      - **Duplicate axis label.** A 2000-max axis ticks at 0/500/1000/1500/2000 and the
        formatter rounded to whole thousands, printing `1.5K` and `2K` both as "2K".
        Now `kLabel()` keeps one decimal for a non-whole K.
      - **Dead space under Conversations.** `#cases` was the only unbounded pane in
        `.ops-grid`; at 40+ rows it ran ~3x the height of the card beside it. Capped to
        the shared `--pane-h` the conversation list and thread already used (now one
        token, not three literals) and scrolled in place, with a sticky `thead` — the
        header is drawn with an inset shadow because `border-collapse: collapse` drops
        a sticky cell's own border.
- [x] **Demo history went stale on its own** (found and fixed 2026-08-06).
      `seed_demo_history.py` writes a window ending *yesterday*, so it ages out at a day
      per day; six days after the original run the right third of both charts was empty
      and read as "the system stopped working last week." The reason nobody topped it up
      is that **re-running it wasn't safe**: it always planned the full window starting
      its counter at 1, so a second run doubled the overlapping days and reissued
      `DEMO....` tracking numbers that `delivery_attempts` already held under its unique
      `(tracking_number, attempt_no)` — an IntegrityError mid-write. A plain run now
      fills only the missing days (`skip_dates`) and continues the numbering past what
      exists (`seq_start`), so **re-running before a demo is the intended habit** and a
      second run in the same day is a no-op. Both new arguments are plain values, so
      `plan_history` stays pure and DB-free; `existing_demo_state()` is the impure half,
      and it buckets by *local* date exactly as the trend chart does. `--fresh` keeps
      the old whole-window behaviour for a wiped slate. Verified live: filled 29 Jul–5
      Aug (8 days, 79 interactions), re-ran clean, both charts full end to end, and the
      RTO figure now rests on n=37 rather than n=16. 3 new tests (305 total).

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
- [x] Tests: handoff routing + bot suppression — 62 new (295 total, incl.
      `test_offline_guard.py` covering the conftest network block itself).
      `test_handoff.py` (store lifecycle, idempotency, expiry sweep, notifier seam +
      an AST check that it never imports the customer WhatsApp channel),
      `test_handoff_routing.py` (routing, suppression, escalation → handoff,
      end-to-end through `process_inbound_message`), `test_ops_write_routes.py`
      (**the read token cannot write** — the load-bearing auth test — plus attribution,
      409s, and a structural guard that no mutating route lands in the read router).

**Acceptance:** ✅ **verified end-to-end against the live stack** (2026-07-31), not just
in unit tests. Escalation raises a handoff and notifies staff → an *open* handoff does
**not** suppress the bot → the read token is refused on claim (401) → claiming with the
write token suppresses → a second claim is a 409, not a silent rewrite → an inbound
message during suppression is **logged but not answered** → a blank actor is a 422 →
resolving hands the thread back and the bot replies again. All 19 checks passed.

**One-time setup after pulling:** `python -m app.core.create_tables` for the new
`handoffs` table, then set `DASHBOARD_WRITE_TOKEN` (a *different* value from
`DASHBOARD_TOKEN`) — without it the handoff buttons return 503 by design. See
**`docs/MIGRATIONS.md`**. *(Both already done on the dev machine.)*

---

## Phase 6 — Robustness & polish — P2 — ✅ the three items worth building are done

**Triaged 2026-08-06** — seven items ranked by what a panel actually sees, because
shipping three that change the demo beats seven that don't. **Build:** the mock delivery
system, the scheduler, and the cheap half of webhook auth. **Cut:** conversation history,
observability, a larger seed dataset. **Moved to Phase 7:** delivery receipts and the
webhook signature check. Reasons recorded on each item.

- [~] **Mock "delivery management system" so reroute/reschedule visibly changes state.**
      Ranked first: it closes the last ⛔ on `PROJECT_PLAN.md` §4.2. A corrective action
      already moved the parcel (`apply_reschedule` sets `out_for_delivery` and bumps
      `attempt_count`) but nothing ever *ended* the journey, so every parcel sat at
      `out_for_delivery` forever and "did it actually get delivered?" had only an audit
      row for an answer.
      - [x] `app/services/delivery_state.py` — the deterministic rule table.
            `next_status(status, outcome, attempts_made)` is a pure lookup: no model, no
            probability, one answer (§5.1). Adds `attempt_failed` (the window the
            proactive loop exists to act in, previously unrepresentable) and
            `returned_to_origin` (the cost centre). **`MAX_DELIVERY_ATTEMPTS = 3` is an
            assumption, not sourced courier data** — hold it to the `RTO_COST_PKR`
            standard. No migration: two new *values* in the existing free-text
            `parcels.status`.
      - [x] `record_attempt_outcome` advances the parcel, clears `delay_reason` on
            delivery, and **refuses an attempt on a terminal parcel outright** rather
            than recording one it then declines to act on.
      - [x] **Provenance, because this moves the headline number.** The ops button
            creates a delivery outcome, and "RTO prevented" is computed from those — so
            `delivery_attempts` gains `source` + `recorded_by`, and `source` is a
            **required keyword-only** argument: an untagged write is impossible, not
            merely discouraged. `MODELLED_SOURCES` (`ops_console`, `simulator`) is the
            set that must never be presented as observed, exactly as §3 demands of
            `RTO_COST_PKR`. Pre-existing rows stay NULL — back-filling a source would be
            claiming something about data nobody tagged at the time.
      - [x] `POST /ops/parcels/{tracking_number}/attempt` in the **write** router behind
            `DASHBOARD_WRITE_TOKEN` — the same seam a real courier webhook calls in
            Phase 7, differing only in who pulls the trigger. `actor` moved to a shared
            `ActorRequest` base so the attribution rule holds for *future* write
            endpoints, not just the ones that remembered.
      - [x] ⚠️ `find_delayed_parcels` now excludes **every** terminal status, not just
            `delivered`. A returned parcel is overdue forever, so the old filter would
            have had the scanner messaging customers about a delivery never coming.
      - [x] Tests: 28 new (340 total) — the rule table, the wiring, terminal refusal,
            rollback leaving the parcel untouched, provenance, and **the read token
            still cannot record a delivery**.
      - [x] Verified end-to-end against live Postgres: reschedule → `out_for_delivery`,
            success → `delivered`, finished parcel → 409, provenance written.
      - [x] `GET /ops/deliveries` (read router, still GET-only) + Deliveries pane:
            parcel, state, `n of 3` attempts with a **"last attempt"** warning, the
            attempt history as chips, and the Delivered/Failed buttons. Each chip carries
            its provenance — `M` for manually triggered (with who, on hover), `?` for
            rows written before provenance existed. A refusal is **explained in the row**
            rather than surfacing a bare 409, because "an attempt needs a corrective
            action to schedule it" is the non-obvious rule an operator hits first.
            `next_attempt` is advisory; the write endpoint re-checks and stays the
            authority.
      - [x] **The ops console cannot mark a parcel that never left the origin
            "delivered."** `can_attempt_delivery` excludes `booked`/`picked_up`, and it
            is **opt-in** (`require_dispatched=True`) rather than global: the scanner
            legitimately records failures on parcels still `in_transit`, and applying
            this everywhere would starve the RTO metric of its organic first failures.
            Two different real events, two different rules.
      - [x] Three UI defects found by using it, all introduced by this phase's scroll
            boxes and writes: the 10s poll **reset the pane's scroll mid-read** (now
            signature-change-detected and scroll-preserving, same as the conversation
            list — and `#cases` needed it too once it gained a scroll box); a **write's
            refresh raced the in-flight poll**, so a slow response could repaint the
            pre-write state (now generation-stamped, newest render wins); and a ~2s
            round trip left the row looking dead after a click (now shows *Recording…*
            — deliberately not an optimistic paint of an outcome that hasn't been
            recorded, since this pane's whole claim is that it reflects real state).
      - [x] Verified in-browser end to end, both themes: **Failed → bot reschedules →
            Delivered**, with the parcel reaching `delivered` and both attempts tagged
            `ops_console`.
- [x] **Scheduler/worker for `scan_and_notify` + retries + dead-letter.** This is the
      word "autonomous" in the one-paragraph pitch — until now the proactive loop only
      ran when a human ran it.
      - [x] `app/tools/worker.py`, a **standalone process**. Not in-process in uvicorn:
            `--reload` runs two processes and `--workers N` runs N, so an in-process
            scheduler would fire the scan two-or-N times over, and coupling "the scans
            happen" to "the API is up" is wrong anyway. Consistent with the Phase 4
            decision that traffic sources are separate processes and Postgres is the
            only shared state.
      - [x] **A plain loop, not APScheduler** — a change from the earlier note. One job,
            one interval, one consumer; a scheduler library buys cron expressions, job
            stores and misfire policies nothing here needs, in exchange for a dependency
            to install on demo morning. Sleeping *after* the work also means a slow scan
            can never stack up behind itself. Rejected an Upstash queue for the reason
            already recorded: no blocking pop over REST, so it would be polled anyway.
      - [x] **Retries are inherent, not scheduled.** A parcel that fails stays overdue
            and un-notified, so the next scan retries it for free. `notification_jobs.py`
            only *counts* the attempts, gives up after `MAX_NOTIFY_ATTEMPTS` (3) so one
            poisoned parcel stops costing an LLM call per run, and keeps the give-ups
            readable. A dead-lettered parcel is skipped **before** the message is
            generated — otherwise giving up saves nothing.
      - [x] **A dead-lettered notification is visible, not lost.** It surfaces in the
            dashboard's case feed as a `notification_failure` ("Failed alerts" filter),
            with `retrying` distinguished from `dead` — the first is a transient blip
            that fixes itself, the second means a customer will never be told about
            their delay unless someone acts. That was the actual defect:
            `proactive_notifier.py`'s `except Exception: logger.exception(); continue`
            put failures only in a log nobody reads. Recovery clears the record and logs
            it, so nobody chases a dead-letter that already fixed itself.
      - [x] ⚠️ **Phase 7 quota guard.** `PROACTIVE_SCAN_ENABLED` defaults to **false** —
            the only optional setting in this project that defaults to inert rather than
            useful, because it is the only one that sends on a timer unattended.
            `PROACTIVE_MAX_SENDS_PER_RUN` bounds the blast radius if a clock or migration
            problem suddenly makes a thousand parcels look overdue. The worker logs which
            provider it booted against and warns when that isn't `mock`.
      - [x] `scan_and_notify` returns counts rather than a bare int: a run that sent
            nothing because everything dead-lettered is a very different event from one
            that sent nothing because there was nothing to do.
      - [x] Tests: 22 new. Store lifecycle, dead-letter skip-before-LLM, recovery,
            the send cap, one bad parcel not stopping the scan, and the case-feed shape.
      - [x] Verified live: three failures → `dead` → skipped by the scan → visible in
            the case feed → cleared on recovery. Worker smoke-tested in both modes
            (inert by default; `--force` sent 2 with the cap respected).
- [ ] ~~Conversation history / richer memory beyond flat 30-min blob~~ — **cut.** Not a
      storage problem (`messages` already has full history), so it is only "feed the
      last N turns into the prompt". Costs ~400–600 tokens on the latency path, and the
      real objection is §5.1: more conversational context gives the model material to
      reason about what *should* happen and phrase something inconsistent with
      `REASON_TO_DECISION`. The multi-turn cases that matter are already carried
      deterministically by `pending_clarification` and `pending_actions`. Cutting it is
      the stronger defense answer, not the weaker one.
- [x] **Auth on inbound webhook — the half that doesn't need Meta.** The GET handshake
      echoed `hub.challenge` to anyone; it now checks `hub.verify_token` (constant-time,
      same as the dashboard tokens) and `hub.mode`, returning 403 without echoing the
      challenge on a mismatch. **Opt-in via `WHATSAPP_VERIFY_TOKEN`**: unset, the
      endpoint behaves exactly as before, which is a deliberate default — it returns a
      string the caller already supplied and touches no state, so while the provider is
      `mock` requiring a token would break the local simulator for no gain. Set it in
      Phase 7 when the URL is public. A test asserts the verify token does **not** gate
      inbound POSTs: Meta never sends it on a message, so gating on it would silently
      drop real traffic.
      The `X-Hub-Signature-256` check on inbound POSTs **stays Phase 7** — it needs the
      App Secret, and HMAC written with no genuine signature to verify against only
      tests the implementation against itself. The "admin endpoints" half of the original
      line was already done by Phases 4/5. 8 new tests (374 total).
- [ ] Realistic, larger seed dataset. *Partly answered already:* `seed_demo_history.py`
      is now idempotent and tops up, so it is the single owner of the demo window and a
      third generator would be the "two tools fighting over the same tables" risk rather
      than a fix. Volume comes from `--days` / `--max-per-day` on that tool. What is
      genuinely still open is honesty about sample size, not size itself.
- [ ] ~~Observability: structured logging, basic metrics/tracing~~ — **cut.** LangSmith
      is already wired. Structured logging and tracing are hygiene no panelist sees, and
      the one observability gap that *does* matter — a silently lost proactive
      notification — is fixed by the scheduler item surfacing it as **dashboard rows**,
      which is better evidence than a trace.
- [ ] ~~Delivery-receipt handling~~ — **moved to Phase 7**, confirmed. It is the
      `statuses` array (sent/delivered/read) in Meta's webhook; the mock channel's
      "delivery" is a DB insert that always succeeds, so there is nothing meaningful to
      mock and nothing to learn from mocking it.

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
- [x] **The offline guard had a Postgres-shaped hole** (found 2026-08-06 while adding
      the worker). `conftest.py` patches Python's `socket`, but psycopg2 is a C extension
      and libpq opens its own socket and resolves its own DNS below that layer — so an
      unmocked `SessionLocal()` reached Supabase for real and merely looked like a slow
      test. A probe test that queried the live database passed. That defeated the guard
      twice over: the network was back on the critical path (on the machine with
      intermittent DNS), and an unmocked DB boundary passed silently instead of failing
      at the call site. Now blocked at SQLAlchemy's `Engine.connect`, which sits above
      the driver and is therefore driver-agnostic. **The whole suite went from ~18s to
      ~6s** once the hidden DB calls surfaced and were mocked — they had been there all
      along. Pinned by two tests in `test_offline_guard.py`.
- [x] **The test suite depended on live network** despite `tests/conftest.py` claiming
      otherwise. Three causes, all fixed: `vector_store` built its Chroma Cloud client at
      *import* time (so a DNS blip failed collection of modules that never touch RAG — it's
      lazy now), `decision_making_node`'s `record_attempt_outcome` opened a real Postgres
      connection in `test_decision.py`, and LangSmith tracing phoned home on every graph
      invoke. `conftest.py` now **enforces** offline with an autouse socket block, so this
      class of regression fails loudly at the call site instead of intermittently. 209
      passed in 5.6s with no network. Opt out per-test with `@pytest.mark.allow_network`.
- [x] **The bot claimed to be human** (found 2026-08-06 while smoke-testing the Handoffs
      pane). "I want to talk to a real person" got back *"I'm here to help and I'm a real
      person"* — which contradicts §5's trust thesis and undercuts the whole Phase 5
      handoff feature at the exact moment it matters. Two causes, both fixed:
      `response_generation_node`'s system prompt described the bot only as "a support
      assistant" and never said it was automated or forbade posing as staff; and the
      frustration/handoff guidance was assigned to a `context_parts_prefix` local that
      was **never read**, so this case reached the model with no instruction at all and
      it improvised. The note is now joined into the prompt (after the untrusted
      customer message, matching the other situation notes) and states what the bot
      *is*. Verified live in both registers — English *"I'm the automated assistant"*,
      Roman Urdu *"Main automated assistant hoon"* — with language mirroring intact.
      Pinned by `tests/test_response_honesty.py` (7 new, 302 total), which asserts on
      the prompts rather than on generated text, including a regression guard that the
      note actually reaches the user prompt.
- [ ] Roman-Urdu code-switched labeled dataset + classification accuracy report (optional novelty artifact)
- [ ] Merchant-facing notifications (COD sale protected)
- [ ] Address geocoding/validation
