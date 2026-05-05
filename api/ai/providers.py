import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"


def _client():
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )


def call_llm(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
    """
    Call OpenRouter (OpenAI-compatible).

    Returns:
        (response_message, usage_dict)
        usage_dict: {"model": str, "input_tokens": int, "output_tokens": int}
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
        kwargs["tool_choice"] = "auto"

    response = _client().chat.completions.create(**kwargs)
    msg = response.choices[0].message
    usage = response.usage

    return msg, {
        "model": model,
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
    }
