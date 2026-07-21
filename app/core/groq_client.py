import logging

from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq

from app.core.config import GROQ_API_KEY

logger = logging.getLogger(__name__)

# timeout + max_retries bound how long a single node can hang on a slow/unavailable
# Groq API before we fall back, so one flaky call can't stall the whole graph run.
client = Groq(api_key=GROQ_API_KEY, timeout=10.0, max_retries=1)

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def safe_chat_completion(messages, *, model: str = DEFAULT_MODEL, temperature: float = 0, fallback: str = "") -> str:
    """Chat completion that returns `fallback` instead of raising when Groq is down/slow/erroring."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except (APIConnectionError, APITimeoutError, APIStatusError) as e:
        logger.error("Groq chat completion failed, using fallback: %s", e)
        return fallback
