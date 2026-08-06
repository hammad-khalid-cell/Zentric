# Database Migrations (manual)

> This project has **no migration framework** (no Alembic). Schema is created with
> SQLAlchemy `Base.metadata.create_all` via `python -m app.core.create_tables`.
>
> **Important:** `create_all` only creates *missing tables*. It **never** alters an
> existing table to add/drop/change a column. So whenever a change adds a column to an
> already-deployed table, you must run the `ALTER TABLE` by hand (Supabase SQL editor,
> `psql`, or any client) **in addition to** `create_tables`. Record every such change
> here, newest first, so a fresh clone or a teammate's DB can be brought up to date.

Order of operations when pulling schema changes:

1. `python -m app.core.create_tables` — creates any brand-new tables.
2. Run the pending `ALTER TABLE` blocks below that your DB hasn't had yet.
3. `python -m app.core.seed_data` — idempotent; picks up new demo rows (skips existing
   tracking numbers, so pre-existing rows keep NULL/default values for new columns).

---

## Phase 6 — delivery state machine, provenance, proactive worker (2026-08-06)

**New table** (created automatically by `create_tables`, no `ALTER TABLE` needed):
`notification_failures` — retry accounting and the dead-letter queue for proactive
notifications. Unique on `(tracking_number, delay_reason)`; `delay_reason` is **NOT
NULL** with an `"unknown"` fallback, because Postgres treats NULLs as distinct in a
unique index and a nullable column there would quietly permit duplicate rows for exactly
the recurring case.

**New environment variables** (all optional, all defaulted safely):

```bash
PROACTIVE_SCAN_ENABLED=false          # default false — see below
PROACTIVE_SCAN_INTERVAL_SECONDS=300   # default 300
PROACTIVE_MAX_SENDS_PER_RUN=0         # 0 / unset = no cap
```

`PROACTIVE_SCAN_ENABLED` is the one optional setting in this project that defaults to
**inert rather than useful**, deliberately: it is the only one that makes the system send
messages on a timer with nobody watching, and in Phase 7 those sends are real Meta quota
against the number reserved for the live defense. Pulling this branch must not quietly
start doing that. Run the worker with:

```bash
python -m app.tools.worker                 # loop, honouring the env config
python -m app.tools.worker --once          # one scan then exit (cron-friendly)
python -m app.tools.worker --once --force  # ignore the enabled flag, for a smoke test
```

It logs the WhatsApp provider before it sends anything, and warns if that provider is
not `mock`.

---

Two nullable columns are also added to an existing table, so this **does** need an
`ALTER TABLE`:

```sql
ALTER TABLE delivery_attempts ADD COLUMN IF NOT EXISTS source      TEXT;
ALTER TABLE delivery_attempts ADD COLUMN IF NOT EXISTS recorded_by TEXT;
CREATE INDEX IF NOT EXISTS ix_delivery_attempts_source ON delivery_attempts (source);
```

Both are nullable on purpose: rows written before this change are genuinely of unknown
provenance, and back-filling them with a source would be *claiming* something about data
we didn't tag at the time. They read as untagged, which is the honest state.

**Why provenance is now a column.** "RTO prevented" is computed from delivery outcomes,
and Phase 6 adds an ops button that creates one. A headline number that a human can move
by clicking has to be able to say which of its inputs were clicked —
`delivery_service.MODELLED_SOURCES` is the set that must never be presented as observed
courier data, exactly as `docs/PROJECT_PLAN.md` §3 requires of `RTO_COST_PKR` and the
demo history.

**No `parcels` change.** The delivery state machine adds two new *values* to the
existing free-text `parcels.status` column — `attempt_failed` and `returned_to_origin`
— joining the six `seed_data.STATUSES` already uses. Nothing to alter.

⚠️ **`find_delayed_parcels` behaviour change.** It previously excluded only
`delivered`; it now excludes every terminal status. Without that, a parcel returned to
origin stays overdue forever and the proactive scanner would keep messaging the customer
about a delivery that is never coming.

---

## Phase 5 — human handoff (2026-07-29)

**New table** (created automatically by `create_tables`, no `ALTER TABLE` needed — no
existing table changed this phase): `handoffs`.

- `handoffs` — one row per conversation handed to a human. Keyed by
  **`customer_phone`, not tracking number**: human ownership applies to a customer's
  whole thread, across every parcel and intent, which is why this isn't a column on
  `tickets` (those are parcel-scoped and `tracking_number` is `NOT NULL`). Lifecycle
  `open → claimed → resolved`, with `expired` as a lazily-swept safety valve; every
  transition stamps who and when.

**New environment variables** (both optional, both defaulted safely — nothing blocks
startup):

```bash
DASHBOARD_WRITE_TOKEN=<a second, different secret>   # required to claim/resolve
STAFF_NOTIFY_PROVIDER=log                            # log | slack | email (default log)
HANDOFF_TTL_HOURS=8                                  # default 8
```

`DASHBOARD_WRITE_TOKEN` is deliberately **separate from `DASHBOARD_TOKEN`** and has no
default. Phase 4's case for one shared token rested on the ops surface being unable to
write; "mark handled" ends that, so reads and writes are separately credentialled.
Leave it unset and the write endpoints return 503 — the ops API is then exactly as
read-only as it was in Phase 4. Set it to a *different* value from `DASHBOARD_TOKEN`,
or the split buys you nothing.

**No demo-data step**, but note that a handoff only appears once something escalates —
send "let me talk to a human" through `/simulator` to put one in the queue.

---

## Phase 3 — outcome tracking & metrics (2026-07-27)

**New tables** (created automatically by `create_tables`, no `ALTER TABLE` needed —
no existing table changed this phase): `delivery_attempts`, `intervention_outcomes`,
`interactions`.

- `delivery_attempts` — one row per delivery attempt outcome; unique on
  `(tracking_number, attempt_no)`.
- `intervention_outcomes` — links an `interventions` row to the delivery attempt that
  resolved it (`'delivered'` = RTO prevented, `'still_failed'`).
- `interactions` — one row per agent-graph run (deflection/cost/response-time/
  after-hours/language metrics all derive from this; distinct from `messages`, which
  is the conversation transcript).

**New dependency:** `tzdata` (added to `requirements.txt`) — Python's `zoneinfo` has
no bundled timezone database, and Windows doesn't ship a system one either, so
resolving `Asia/Karachi` (used for the after-hours metric) needs the `tzdata` package
installed. `pip install -r requirements.txt` picks it up automatically.

**Demo data:** after applying at least one corrective action (Phase 2's proactive
loop), run `python -m app.tools.simulate_outcomes` once to resolve open interventions
to a delivered/still-failed outcome (there's no real courier feedback signal yet —
Phase 6) so `GET /metrics/report`'s RTO-reduction % is non-trivial for the demo.

---

## Phase 2 — proactive loop (2026-07-26)

**New tables** (created automatically by `create_tables`): `pending_actions`, `interventions`.

**New `parcels` columns** — require a manual migration on any DB where `parcels`
already existed (`create_all` left the existing table untouched):

```sql
ALTER TABLE parcels ADD COLUMN IF NOT EXISTS address_line VARCHAR;
ALTER TABLE parcels ADD COLUMN IF NOT EXISTS preferred_delivery_window VARCHAR;
ALTER TABLE parcels ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
```

- `address_line` — updatable delivery address; what `update_address` writes back to.
- `preferred_delivery_window` — free-text window (e.g. "tomorrow evening") from a reschedule.
- `attempt_count` — bumped each time a corrective action reschedules an attempt.

After migrating, re-run `seed_data` to load the demo `TRK20250` incorrect-address parcel
used by the acceptance case.
