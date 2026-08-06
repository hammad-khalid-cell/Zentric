"""Retry accounting and the dead-letter queue for proactive notifications (Phase 6).

What this protects: the proactive scan is the only part of the system that acts with
nobody waiting on a reply, so its failures are uniquely invisible. Before this, a failed
notification was a `logger.exception` and nothing else — a customer who was never warned
about their delay looked exactly like one who was.

DB-free: `SessionLocal` is monkeypatched, following the FakeDB pattern the rest of the
suite uses.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.core import notification_jobs
from app.models.notification_failure import NotificationFailure


class _FakeQuery:
    def __init__(self, store, rows=None):
        self._store = store
        self._rows = rows if rows is not None else list(store.rows)

    def filter_by(self, **kw):
        matched = [
            r for r in self._rows
            if all(getattr(r, k) == v for k, v in kw.items())
        ]
        return _FakeQuery(self._store, matched)

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def delete(self, synchronize_session=False):
        for row in self._rows:
            self._store.rows.remove(row)
        return len(self._rows)


class FakeDB:
    def __init__(self, rows=None, flush_raises=None):
        self.rows = list(rows or [])
        self.committed = False
        self._flush_raises = flush_raises

    def query(self, *targets):
        return _FakeQuery(self)

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        if self._flush_raises is not None:
            exc, self._flush_raises = self._flush_raises, None
            self.rows.pop()          # the insert didn't land
            raise exc

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: fake)
    return fake


def _failure(tracking_number="TRK10001", reason="customer_unavailable", attempts=1,
             status=notification_jobs.STATUS_RETRYING):
    return NotificationFailure(
        id=1, tracking_number=tracking_number, delay_reason=reason,
        attempts=attempts, status=status, last_error="boom",
    )


def test_a_first_failure_starts_retrying_not_dead(db):
    """One blip is not a give-up — most of these are a DNS wobble that fixes itself."""
    result = notification_jobs.record_failure("TRK10001", "customer_unavailable", "boom")

    assert result["attempts"] == 1
    assert result["status"] == notification_jobs.STATUS_RETRYING
    assert result["dead"] is False
    assert db.committed is True


def test_repeated_failures_accumulate(db):
    db.rows.append(_failure(attempts=1))
    result = notification_jobs.record_failure("TRK10001", "customer_unavailable", "again")

    assert result["attempts"] == 2
    assert result["dead"] is False


def test_the_last_permitted_failure_dead_letters_it(db):
    """Giving up is the point: a poisoned parcel must stop costing an LLM call per run."""
    db.rows.append(_failure(attempts=notification_jobs.MAX_NOTIFY_ATTEMPTS - 1))
    result = notification_jobs.record_failure("TRK10001", "customer_unavailable", "final")

    assert result["attempts"] == notification_jobs.MAX_NOTIFY_ATTEMPTS
    assert result["status"] == notification_jobs.STATUS_DEAD
    assert result["dead"] is True


def test_a_missing_delay_reason_gets_a_stable_key(db):
    """Postgres treats NULLs as distinct in a unique index, so a null reason would
    silently allow duplicate rows for exactly the recurring case."""
    notification_jobs.record_failure("TRK10001", None, "boom")

    assert db.rows[0].delay_reason == "unknown"


def test_a_long_error_is_truncated(db):
    notification_jobs.record_failure("TRK10001", "customer_unavailable", "x" * 5000)

    assert len(db.rows[0].last_error) == 1000


def test_recording_a_failure_never_raises(monkeypatch):
    """This runs inside the scan's own exception handler. A failure to record a failure
    must not take the scan down with it."""
    class Exploding(FakeDB):
        def query(self, *a, **k):
            raise RuntimeError("database is unreachable")

    monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: Exploding())
    result = notification_jobs.record_failure("TRK10001", "customer_unavailable", "boom")

    assert result["recorded"] is False


def test_a_concurrent_insert_increments_instead_of_exploding(monkeypatch):
    """Two scans can overlap; the unique constraint is the real guard and losing that
    race must count the attempt, not lose it."""
    existing = _failure(attempts=1)
    fake = FakeDB(flush_raises=IntegrityError("dup", None, None))

    # The row appears only after our insert loses the race, as it would in Postgres.
    original_query = fake.query
    calls = {"n": 0}

    def query(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1 and existing not in fake.rows:
            fake.rows.append(existing)
        return original_query(*a, **k)

    fake.query = query
    monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: fake)

    result = notification_jobs.record_failure("TRK10001", "customer_unavailable", "boom")
    assert result["recorded"] is True
    assert result["attempts"] == 2


def test_clearing_a_failure_reports_whether_there_was_one(db):
    db.rows.append(_failure())
    assert notification_jobs.clear_failure("TRK10001", "customer_unavailable") is True
    assert db.rows == []
    assert notification_jobs.clear_failure("TRK10001", "customer_unavailable") is False


def test_only_dead_rows_are_reported_as_give_ups(monkeypatch):
    """`retrying` rows must not be skipped by the scan — they are still being retried,
    which is the entire mechanism."""
    class RowsDB(FakeDB):
        def query(self, *targets):
            return _FakeQuery(self, [
                (r.tracking_number, r.delay_reason) for r in self.rows
                if r.status == notification_jobs.STATUS_DEAD
            ])

    fake = RowsDB(rows=[
        _failure("TRK_DEAD", status=notification_jobs.STATUS_DEAD),
        _failure("TRK_RETRY", status=notification_jobs.STATUS_RETRYING),
    ])
    monkeypatch.setattr(notification_jobs, "SessionLocal", lambda: fake)

    keys = notification_jobs.dead_lettered_keys()
    assert ("TRK_DEAD", "customer_unavailable") in keys
    assert ("TRK_RETRY", "customer_unavailable") not in keys
