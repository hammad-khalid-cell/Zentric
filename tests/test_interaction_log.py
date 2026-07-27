from app.services import interaction_log
from app.models.interaction import Interaction


class FakeDB:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_record_interaction_bot_resolved(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(interaction_log, "SessionLocal", lambda: fake)

    state = {
        "user_message": "TRK12345 status?",
        "customer_id": "923001234567",
        "intent": "track_order",
        "tracking_number": "TRK12345",
        "decision": None,
        "needs_human_handoff": False,
    }
    interaction_log.record_interaction(state, elapsed_ms=250)

    assert len(fake.added) == 1
    row = fake.added[0]
    assert isinstance(row, Interaction)
    assert row.resolved_by == "bot"
    assert row.escalated is False
    assert row.language == "english"
    assert row.response_time_ms == 250
    assert row.tracking_number == "TRK12345"
    assert fake.committed is True


def test_record_interaction_escalated_is_human_resolved(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(interaction_log, "SessionLocal", lambda: fake)

    state = {
        "user_message": "mujhe insaan se baat karni hai",
        "customer_id": "923001234567",
        "intent": "unclear",
        "needs_human_handoff": True,
    }
    interaction_log.record_interaction(state, elapsed_ms=120)

    row = fake.added[0]
    assert row.resolved_by == "human"
    assert row.escalated is True
    assert row.language == "roman_urdu"
