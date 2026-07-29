# Zentric — Master Project Plan

> **Read this first, every session.** This is the single source of truth for
> *why* this project exists, *what* we are building, and the *rules* we do not
> break. `docs/ROADMAP.md` is the living task board; update it as work lands.
> `CLAUDE.md` holds the technical/codebase guide. When those conflict with a
> passing thought, **this file and ROADMAP win** — re-read before large changes.

Last meaningful update: 2026-07-29.

---

## 0. Starting a session (copy-paste these)

A fresh Claude Code session remembers nothing. Reconstruct context in one turn.

**At the start of any session** (orient before touching code):

```
Read docs/PROJECT_PLAN.md and docs/ROADMAP.md, then run `git log --oneline -10`.
Summarize the current state and the next P0 item. Don't write code yet.
```

**When resuming a specific task**, add what you want:

```
Read docs/PROJECT_PLAN.md and docs/ROADMAP.md and `git log --oneline -10`, then
summarize where we are. Today I want to work on <Phase / item>. Confirm the plan
against the roadmap before writing code.
```

**At the end of every session** (so the next one starts accurate — the roadmap is
this project's save file):

```
Update docs/ROADMAP.md to reflect what we did this session (check boxes, add any
discovered tasks), then commit it.
```

Notes: `CLAUDE.md` auto-loads each session and points here, but explicitly asking the
agent to *read these two docs* is what reliably reloads context. Durable facts (e.g.
the dual-account git workflow) live in Claude's persistent memory and carry over
automatically — no need to re-explain them.

---

## 1. What this project is (one paragraph)

Zentric is an **autonomous, Roman-Urdu-native WhatsApp support agent for Pakistani
courier/logistics companies**. It is not "a chatbot." Its job is to attack the two
biggest cost centres in Pakistani last-mile logistics: repetitive status queries
(WISMO) and **failed/returned Cash-on-Delivery (COD) deliveries (RTO)**. It does
this reactively (answering customers) *and* proactively (intervening before a
delivery fails), while keeping every business-affecting decision deterministic and
auditable.

---

## 2. The problem we solve (this is what the defense is about)

Pakistani logistics bleeds money in two places:

1. **WISMO overload — "Where Is My Order?"** 50–70% of courier support contacts are
   just status checks, each handled by a paid human, in business hours, often not in
   the customer's own language. High-volume, repetitive, pure cost.

2. **COD failed deliveries → RTO (the big one).** ~90% of Pakistani e-commerce is
   COD. First-attempt delivery failure runs **20–30%**, driven by exactly our
   `delay_reason` codes: *customer unavailable, wrong address, unreachable.* A failed
   attempt wastes a rider trip; a **Return-to-Origin** ships the parcel out **and**
   back with **zero cash collected** — the courier eats the full round trip and the
   merchant loses the sale.

Current industry handling is **reactive** (they learn of a failure *after* it
happens), **expensive** (call centres), **English/limited-hours**, and does not meet
customers on **WhatsApp**, which in Pakistan has near-universal reach and ~90%+ open
rates.

> **Defense framing:** lead with RTO reduction. WISMO deflection is the strong
> secondary benefit. Never pitch "a support bot."

---

## 3. The solution & the economic case (the value proposition)

An agent that:
- **Deflects WISMO instantly** at near-zero marginal cost, in the customer's language.
- **Proactively intervenes before a delivery fails** — confirms availability, fixes
  the address, reschedules — converting would-be RTOs into completed COD deliveries.
- **Escalates only genuine cases** to a human.

**Two savings levers (illustrative — must be validated against real courier data or a
cited report; always present as a parameterised model, never as fact):**

| Lever | Mechanism | Illustrative annual impact (mid-size courier) |
|---|---|---|
| Support deflection | Bot resolves WISMO at ~PKR 2/query vs ~PKR 30 human | ~PKR 36M + fewer agents |
| **RTO / failed-delivery reduction** | Proactive intervention converts preventable failures | **~PKR 65M (the headline)** |

The RTO lever is roughly **2× larger** than the support lever. That is the story.

**KPIs we must be able to *measure* (see §9):** deflection rate, cost-per-interaction
(human vs bot), **RTO reduction %**, first-response time, after-hours coverage %,
language reach %.

---

## 4. Definition of "done & defensible" (the finish line)

The project is defense-ready when we can demonstrate, live:
1. A customer sends a WhatsApp-style message and gets an instant, correct,
   same-language reply (WISMO deflection). ✅ *logic exists; needs channel + demo*
2. The system **proactively** messages a customer about a delay, the customer
   **replies**, and the system **takes a corrective action** (reschedule / fix
   address) that **changes the delivery outcome**. ⛔ *not yet closed — top priority*
3. An **ops dashboard** shows the conversations, tickets, and the **KPIs above with
   real numbers** produced by the system's own outcome tracking. ⛔
4. An **evaluation** backs the safety/quality claims (adversarial + decision
   correctness vs a naive LLM baseline). ⛔
5. A one-page **ROI model** the panel can poke at. ⛔

Anything not serving one of these five is polish.

---

## 5. Non-negotiable principles (do not break these)

1. **The LLM never chooses a business action.** Decisions (notify / reroute /
   escalate / reschedule / update_address) are deterministic (`decision_rules.py`
   style mappings). The LLM only *phrases* explanations and *interprets* free-text
   into a structured intent that a deterministic policy then acts on. This is our
   trust/safety thesis — protect it.
2. **Every business-affecting change is auditable** (a DB row: ticket, reroute,
   attempt, intervention outcome). No silent state changes.
3. **Ownership is always verified** — a parcel is only ever revealed/acted-on for the
   phone number that owns it. Never build an oracle that confirms ownership.
4. **WhatsApp real API is the LAST thing we integrate** — to preserve free quota for
   the live defense. Until then, everything talks to a **mock channel** through the
   `send_whatsapp_message()` seam (see §7). Code must be swappable with a one-line
   provider change, not a rewrite.
5. **Guardrails stay on**: input validation, rate limiting, prompt-injection framing,
   LLM fallbacks (`safe_chat_completion`). New LLM call sites inherit these.
6. **Determinism is testable** — new deterministic logic ships with tests
   (`tests/`, pytest). Mock external boundaries (Groq/Postgres/Redis/WhatsApp).

---

## 6. Architecture: current → target

**Current (reactive brain, working):**
`memory_load → intent_understanding → escalation_check → data_retrieval →
[decision_making → action_execution] → response_generation → memory_save`
plus a **one-way** `proactive_notifier.scan_and_notify()` and RAG FAQ.

**What's missing for the value prop (target):**

```
                 ┌─────────────────── WhatsApp Channel (PORT: send_whatsapp_message) ───────────────────┐
   inbound  ─────►  webhook adapter (mock now / Cloud API later)                                         │
                 └───────────────────────────────────────────────────────────────────────────────────────┘
                         │ inbound                                            ▲ outbound (captured to Message log)
                         ▼                                                    │
   Reactive graph (exists) ──────────────────────────────────────────────────┘
                         ▲
   Proactive loop (TO BUILD):
     scan_and_notify → send proactive msg → store PENDING ACTION (parcel-scoped) →
     customer reply routed into graph → interpret (reschedule/new address/window/cancel) →
     deterministic corrective ACTION → write-back to Parcel/DeliveryAttempt → OUTCOME recorded
                         │
                         ▼
   Outcome tracking (TO BUILD) → Metrics service → Ops Dashboard (TO BUILD)
```

Three target additions: **(a)** two-way mock WhatsApp channel, **(b)** a *closed*
proactive loop with corrective actions, **(c)** outcome tracking feeding a dashboard.

---

## 7. WhatsApp mock strategy (concrete — build this, not the real API)

Keep `app/core/whatsapp_client.py::send_whatsapp_message()` as the **outbound port**
(it already is — good). Formalise a channel abstraction:

- **`WhatsAppChannel` interface** with `send(phone, message)`.
- **`MockWhatsAppChannel`** (build now): persists every outbound message to a new
  `messages` table (direction=`out`) and, for the demo, exposes it so the dashboard
  renders a real-looking conversation thread. No network, no quota.
- **`CloudApiWhatsAppChannel`** (build LAST): real Meta WhatsApp Cloud API.
- **Selection via env** `WHATSAPP_PROVIDER=mock|cloud` — swap with one setting.

**Inbound:** add `POST /webhook/whatsapp` that accepts a payload shaped like Meta's
webhook and feeds the same graph (`compiled_graph`). Keep `POST /test/message` as a
dev shortcut. A tiny **customer simulator** (web page or dashboard panel) posts to the
webhook so we can demo the full two-way loop. When the real API arrives, point Meta at
the same webhook — the graph doesn't change.

**Payoff:** the entire proactive loop and dashboard are fully demoable with **zero**
WhatsApp quota consumed until the defense.

---

## 8. Phased roadmap (summary — detail & status in `docs/ROADMAP.md`)

| Phase | Goal | Priority |
|---|---|---|
| 0 | Guardrails + refactor + test suite | ✅ mostly done |
| 1 | **Mock WhatsApp channel** (two-way) + message log | **P0** |
| 2 | **Close the proactive loop**: pending-action memory, reply interpretation, `reschedule`/`update_address` actions + Parcel fields | **P0** |
| 3 | **Outcome tracking**: DeliveryAttempt + InterventionOutcome + metrics service | **P0 (ROI evidence)** |
| 4 | **Ops/KPI dashboard** (frontend) | P1 |
| 5 | Human-handoff notification/console | P1 |
| 6 | Robustness: scheduler/worker, conversation history, auth, realistic dataset, mock delivery system | P2 |
| 7 | **Swap mock → real WhatsApp Cloud API (LAST)** | P0-but-last |
| X | Evaluation harness (adversarial + decision correctness vs naive LLM) | P1, cross-cutting |

**The three P0s (phases 1–3) are the difference between "a chatbot" and "a system
that measurably reduces COD return cost." Do them first.**

---

## 9. Data model & metrics (what phases 2–3 need)

**New/changed persistence (Postgres, `app/models/`):**
- `Parcel`: add delivery address fields (currently only `destination_city`), a
  reschedule/preferred window, and an attempt counter. Address must be *updatable*.
- **`Message`** (new): `customer_phone`, `direction` (in/out), `body`,
  `tracking_number?`, `created_at` — the conversation log + mock channel store.
- **`DeliveryAttempt`** (new): `tracking_number`, `attempt_no`, `outcome`
  (success/failed), `failure_reason`, `created_at` — the raw material for RTO metrics.
- **`InterventionOutcome`** (new): links a proactive intervention → the subsequent
  delivery outcome, so we can compute "RTO prevented."
- **Pending action** (Redis or a `pending_actions` table): parcel-scoped, TTL longer
  than the current 30-min session, so a proactive message and its later reply connect.

**New action types** (deterministic): `reschedule`, `update_address` — added to the
decision layer and `action_execution`, each writing an auditable row.

**Metrics service** (feeds dashboard & defense):
- Deflection rate = auto-resolved ÷ total inbound.
- Cost-per-interaction = configurable human PKR vs bot token PKR.
- **RTO reduction** = interventions that led to a successful delivery ÷ preventable
  failures; plus PKR saved via a configurable per-parcel cost.
- Response time, after-hours %, language reach %.
- Keep cost assumptions in **config**, not hard-coded, so the ROI model is tunable
  live during the defense.

---

## 10. Working agreements for any agent (Claude Code or otherwise)

1. **Read `docs/PROJECT_PLAN.md` + `docs/ROADMAP.md` before starting.** Confirm the
   task maps to a roadmap item and a §4 finish-line goal; if it doesn't, question it.
2. **Respect §5 principles** — especially "LLM never decides actions" and "WhatsApp
   mock until the end."
3. **Keep the `send_whatsapp_message()` seam** — never call a WhatsApp API directly
   from a node/service.
4. **Ship deterministic logic with pytest tests**; mock external boundaries.
5. **Update `docs/ROADMAP.md`** (check boxes, add discovered tasks) as part of the
   same change — the board must reflect reality.
6. **Commit discipline:** one logical change per commit; conventional prefixes
   (`feat:`/`fix:`/`test:`/`docs:`/`refactor:`). Follow the dual-account git workflow
   (work on a feature/topic branch, PR into `main` on the upstream account — never
   push `main` directly). **Never add a `Co-Authored-By:` trailer or any other AI
   attribution to a commit message** — not in any commit, ever. Agents that append one
   by default must suppress it here; this is graded work defended as the author's own.
7. **State-shape changes start in `app/graph/state.py`** — it's the contract between
   nodes.
8. When unsure between "clever" and "auditable/deterministic," pick auditable.

---

## 11. Glossary

- **WISMO** — "Where Is My Order?" status queries; the bulk of support volume.
- **COD** — Cash on Delivery; ~90% of Pakistani e-commerce.
- **RTO** — Return to Origin; a failed delivery returned to sender, the core cost we
  attack.
- **Deflection** — a query fully resolved by the agent with no human involved.
- **Closed loop** — proactive message → customer reply → corrective action → changed
  delivery outcome, all captured.
