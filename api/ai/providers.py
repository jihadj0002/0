import asyncio
import logging
import os
import threading
import time

from openai import AsyncOpenAI, AuthenticationError, BadRequestError, InternalServerError, OpenAI, RateLimitError

logger = logging.getLogger(__name__)


class OpenRouterAPIKeyError(Exception):
    """OpenRouter rejected our API key (401 User not found)."""

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
FALLBACK_MODEL = "openai/gpt-4o-mini"
LLM_TIMEOUT = 180
MAX_RETRIES_ON_429 = 3
INITIAL_BACKOFF = 1.0

# ---- API key pool (thread-safe round-robin) --------------------------------

def _load_key_pool():
    raw = os.environ.get("OPENROUTER_API_KEYS", "") or os.environ.get("OPENROUTER_API_KEY", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        logger.warning("No OPENROUTER_API_KEYS or OPENROUTER_API_KEY set")
    return keys

_key_pool = _load_key_pool()
_key_index = 0
_key_lock = threading.Lock()


def _next_api_key():
    with _key_lock:
        global _key_index
        if not _key_pool:
            return ""
        key = _key_pool[_key_index % len(_key_pool)]
        _key_index += 1
        return key


def next_api_key():
    """Return the next key from the configured pool (for shared consumers)."""
    return _next_api_key()


def _client():
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=_next_api_key(),
    )


# ---- LLM call with retries ------------------------------------------------

def call_llm(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
    model = model or DEFAULT_MODEL

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": LLM_TIMEOUT,
    }
    if tools:
        kwargs["tools"] = tools

    last_error = None
    for attempt in range(MAX_RETRIES_ON_429 + 1):
        try:
            client = _client()
            response = client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            usage = response.usage

            return msg, {
                "model": model,
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
        except RateLimitError as e:
            last_error = e
            if attempt < MAX_RETRIES_ON_429:
                wait = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "Rate limited (attempt %d/%d), waiting %.1fs before retry",
                    attempt + 1, MAX_RETRIES_ON_429 + 1, wait,
                )
                time.sleep(wait)
                continue
            logger.error("Rate limited after %d attempts, giving up", MAX_RETRIES_ON_429 + 1)
            raise
        except InternalServerError:
            raise
        except AuthenticationError as e:
            logger.error(
                "OpenRouter API key rejected (401 User not found): %s", e
            )
            raise OpenRouterAPIKeyError(
                "OpenRouter API key invalid (401). Check OPENROUTER_API_KEY / OPENROUTER_API_KEYS."
            ) from e

    raise last_error


def call_llm_with_fallback(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
    model = model or DEFAULT_MODEL
    try:
        return call_llm(messages, tools, model, temperature, max_tokens)
    except InternalServerError:
        if model == FALLBACK_MODEL:
            raise
        logger.warning(
            "Primary model %s returned 5xx, retrying with fallback %s", model, FALLBACK_MODEL
        )
        return call_llm(messages, tools, FALLBACK_MODEL, temperature, max_tokens)
    except BadRequestError as e:
        if model == FALLBACK_MODEL or e.status_code != 400:
            raise
        logger.warning(
            "Primary model %s returned provider 400, retrying with fallback %s",
            model, FALLBACK_MODEL,
        )
        return call_llm(messages, tools, FALLBACK_MODEL, temperature, max_tokens)


# ---- Async LLM (event-loop path, for the AI pipeline) ---------------------
# One AsyncOpenAI client per process, created lazily on the event-loop thread.
# The SDK multiplexes thousands of concurrent in-flight requests over a single
# connection pool, which is what gives the pipeline its massive concurrency.

_async_client: AsyncOpenAI | None = None
_async_client_lock = threading.Lock()


def _async_client_instance():
    global _async_client
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                _async_client = AsyncOpenAI(
                    base_url=OPENROUTER_BASE_URL,
                    api_key=_next_api_key(),
                )
    return _async_client


async def async_call_llm(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
    """Async equivalent of :func:`call_llm` — one LLM request, awaited.

    Must be called from the running event loop. Retries on 429 use
    ``asyncio.sleep`` so the loop keeps servicing other conversations.
    """
    model = model or DEFAULT_MODEL

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools

    last_error = None
    for attempt in range(MAX_RETRIES_ON_429 + 1):
        try:
            client = _async_client_instance()
            response = await client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            usage = response.usage
            return msg, {
                "model": model,
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
        except RateLimitError as e:
            last_error = e
            if attempt < MAX_RETRIES_ON_429:
                wait = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "Rate limited (attempt %d/%d), waiting %.1fs before retry",
                    attempt + 1, MAX_RETRIES_ON_429 + 1, wait,
                )
                await asyncio.sleep(wait)
                continue
            logger.error("Rate limited after %d attempts, giving up", MAX_RETRIES_ON_429 + 1)
            raise
        except InternalServerError:
            raise
        except AuthenticationError as e:
            logger.error(
                "OpenRouter API key rejected (401 User not found): %s", e
            )
            raise OpenRouterAPIKeyError(
                "OpenRouter API key invalid (401). Check OPENROUTER_API_KEY / OPENROUTER_API_KEYS."
            ) from e

    raise last_error


async def async_call_llm_with_fallback(
    messages, tools=None, model=None, temperature=0.7, max_tokens=1024
):
    model = model or DEFAULT_MODEL
    try:
        return await async_call_llm(messages, tools, model, temperature, max_tokens)
    except InternalServerError:
        if model == FALLBACK_MODEL:
            raise
        logger.warning(
            "Primary model %s returned 5xx, retrying with fallback %s", model, FALLBACK_MODEL
        )
        return await async_call_llm(messages, tools, FALLBACK_MODEL, temperature, max_tokens)
    except BadRequestError as e:
        if model == FALLBACK_MODEL or e.status_code != 400:
            raise
        logger.warning(
            "Primary model %s returned provider 400, retrying with fallback %s",
            model, FALLBACK_MODEL,
        )
        return await async_call_llm(messages, tools, FALLBACK_MODEL, temperature, max_tokens)
