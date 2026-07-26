"""Request validation on the /test/message payload (MessageRequest): phone
number must be digits-only, message must be non-empty after stripping."""
import pytest
from pydantic import ValidationError

from app.routes.test_routes import MessageRequest


def test_valid_payload():
    req = MessageRequest(from_number="923001234567", message="TRK12345 status?")
    assert req.from_number == "923001234567"
    assert req.message == "TRK12345 status?"


def test_from_number_strips_whitespace():
    req = MessageRequest(from_number="  923001234567  ", message="hi")
    assert req.from_number == "923001234567"


def test_message_is_stripped():
    req = MessageRequest(from_number="923001234567", message="  hello  ")
    assert req.message == "hello"


@pytest.mark.parametrize("bad_number", [
    "92300abc4567",     # contains letters
    "+923001234567",    # leading '+' is not a digit
    "0300 123 4567",    # spaces inside
    "12345",            # too short (min_length 7)
])
def test_invalid_from_number_rejected(bad_number):
    with pytest.raises(ValidationError):
        MessageRequest(from_number=bad_number, message="hi")


@pytest.mark.parametrize("bad_message", [
    "",         # empty
    "   ",      # whitespace-only
])
def test_invalid_message_rejected(bad_message):
    with pytest.raises(ValidationError):
        MessageRequest(from_number="923001234567", message=bad_message)


def test_overlong_message_rejected():
    with pytest.raises(ValidationError):
        MessageRequest(from_number="923001234567", message="x" * 1001)
