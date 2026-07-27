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
