"""
Shared OpenAI client for Private workspace projects.
Models are resolved from environment variables with automatic fallback.

Usage:
    from openai_client import chat, client, PRIMARY_MODEL

    response = chat([{"role": "user", "content": "Hello"}])
    print(response.choices[0].message.content)
"""

import os
import logging
from openai import OpenAI, NotFoundError, APIStatusError

logger = logging.getLogger(__name__)

PRIMARY_MODEL  = os.getenv("OPENAI_MODEL_PRIMARY",  "gpt-5.5")
FALLBACK_MODEL = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-5.4")
MINI_MODEL     = os.getenv("OPENAI_MODEL_MINI",     "gpt-5.4-mini")

_api_key = os.environ.get("OPENAI_API_KEY")
if not _api_key:
    raise EnvironmentError(
        "OPENAI_API_KEY nie jest ustawiony. "
        "Uruchom: echo 'export OPENAI_API_KEY=\"sk-...\"' >> ~/.zshenv && source ~/.zshenv"
    )

client = OpenAI(api_key=_api_key)

MODEL_CHAIN = [PRIMARY_MODEL, FALLBACK_MODEL, MINI_MODEL]


def chat(
    messages: list[dict],
    model: str | None = None,
    **kwargs,
) -> object:
    """
    Sends a chat completion request with automatic model fallback.

    Args:
        messages: List of message dicts (role + content).
        model:    Override model (skips fallback chain).
        **kwargs: Extra params passed to client.chat.completions.create().

    Returns:
        ChatCompletion response object.

    Raises:
        RuntimeError: If none of the models in the chain succeed.
    """
    chain = [model] if model else MODEL_CHAIN

    # gpt-5.x requires max_completion_tokens instead of max_tokens
    if "max_tokens" in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

    for m in chain:
        try:
            logger.debug("Trying model: %s", m)
            return client.chat.completions.create(
                model=m,
                messages=messages,
                **kwargs,
            )
        except (NotFoundError, APIStatusError) as exc:
            if isinstance(exc, APIStatusError) and exc.status_code not in (404, 400):
                raise
            logger.warning("Model %s unavailable (%s), trying next...", m, exc)
            continue

    raise RuntimeError(
        f"Żaden model z łańcucha {chain} nie jest dostępny. "
        "Sprawdź dostęp do modeli na platform.openai.com."
    )
