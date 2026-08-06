"""The bot must never present itself as a human.

This is not cosmetic: the whole human-handoff feature (Phase 5) is premised on the
bot standing aside for a person, and a reply that claims to *be* a person contradicts
the trust thesis in docs/PROJECT_PLAN.md §5.

Found live on 2026-08-06 — asking "I want to talk to a real person" got back
"I'm here to help and I'm a real person". Two causes, both covered below:
the system prompt never stated the bot was automated, and the handoff guidance was
assigned to a `context_parts_prefix` local that was never read, so the model got no
instruction for this case at all and improvised.

These assert on the prompts rather than on generated text — the LLM is stubbed, so
what is pinned is the contract we hand it, which is the deterministic part.
"""
import pytest

from app.graph import nodes


@pytest.fixture
def captured(monkeypatch):
    """Run response_generation_node with the LLM stubbed, returning the prompts."""
    seen = {}

    def fake_completion(**kwargs):
        seen["messages"] = kwargs["messages"]
        return "stubbed reply"

    monkeypatch.setattr(nodes, "safe_chat_completion", fake_completion)

    def run(state):
        nodes.response_generation_node(state)
        return {
            "system": seen["messages"][0]["content"],
            "user": seen["messages"][1]["content"],
        }

    return run


def _handoff_state(reason="explicit_human_request"):
    return {
        "user_message": "I want to talk to a real person",
        "customer_id": "923001112222",
        "intent": "unclear",
        "needs_human_handoff": True,
        "escalation_reason": reason,
    }


def test_system_prompt_states_the_bot_is_automated(captured):
    system = captured(_handoff_state())["system"].lower()
    assert "automated assistant" in system
    assert "not a human agent" in system


def test_system_prompt_forbids_claiming_to_be_a_person(captured):
    system = captured(_handoff_state())["system"].lower()
    # The exact failure observed live, spelled out so the model has no room to read
    # "be warm and reassuring" as licence to pose as staff.
    assert "never claim or imply that you are a person" in system
    assert "i am a real person" in system


@pytest.mark.parametrize(
    "reason", ["explicit_human_request", "repeated_query", "tone_detected"]
)
def test_handoff_note_actually_reaches_the_prompt(captured, reason):
    """The regression guard. The note existed before but was dead code — asserting it
    is present in the *user* prompt is what proves it is wired in, not just written."""
    user = captured(_handoff_state(reason))["user"]
    assert "you are the automated assistant" in user.lower()
    assert "do not claim to be a human agent" in user.lower()


def test_handoff_note_is_absent_when_no_handoff(captured):
    """It should not be bolted onto every reply — only the escalation reasons above."""
    state = {
        "user_message": "where is my parcel",
        "customer_id": "923001112222",
        "intent": "unclear",
        "needs_human_handoff": False,
        "escalation_reason": None,
    }
    assert "do not claim to be a human agent" not in captured(state)["user"].lower()


def test_handoff_note_follows_the_customer_message(captured):
    """Ordering matters for injection resistance: our instruction sits after the
    untrusted customer text, same as the other situation notes."""
    prompts = captured(_handoff_state())
    user = prompts["user"]
    assert user.index("Customer's original message") < user.index(
        "Note: this customer is frustrated"
    )
