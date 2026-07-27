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
