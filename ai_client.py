"""
ai_client — prompt DeepSeek from anywhere in this project.

    import ai_client as ai
    answer = ai.ask("what is the capital of France?").text

Drop-in replacement for home-net's `irc_bot.libs.ai` (same `ask()` shape:
`AskResult(text, output_tokens, model, effort)`, `AIError`) so
game_generator.py / game_enhancer.py needed near-zero changes when ported
out of that project.

Talks to the DeepSeek API (OpenAI-compatible Chat Completions) via the
`openai` SDK pointed at DeepSeek's base URL. Requires `DEEPSEEK_API_KEY` in
the environment (see .env.example) — loaded via python-dotenv if a .env file
is present.

`effort` has no DeepSeek equivalent, so it's mapped onto DeepSeek's two
models instead of being passed through: "high" selects the reasoning model
(deepseek-reasoner), anything else selects the fast chat model
(deepseek-chat). Pass `model` explicitly to bypass this mapping.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import APIError, APITimeoutError, OpenAI

load_dotenv()

BASE_URL = "https://api.deepseek.com"
MODEL_FAST = "deepseek-chat"
MODEL_REASONING = "deepseek-reasoner"

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class AIError(Exception):
    """Raised when a DeepSeek prompt fails. str(exc) is a displayable message."""


@dataclass
class AskResult:
    text: str
    output_tokens: int
    model: str
    effort: str


def _client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise AIError("Error: DEEPSEEK_API_KEY is not set (see .env.example)")
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def _resolve_model(model: str | None, effort: str | None) -> str:
    if model:
        return model
    return MODEL_REASONING if effort == "high" else MODEL_FAST


def ask(
    prompt: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    timeout: int | None = 120,
    **_ignored,
) -> AskResult:
    """Run a one-shot DeepSeek prompt and return the result. Raises AIError.

    `**_ignored` absorbs kwargs from the home-net `ai.ask()` interface that
    have no DeepSeek equivalent (e.g. web_search) so callers ported over
    unchanged don't need per-call edits.
    """
    resolved_model = _resolve_model(model, effort)
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    client = _client()
    try:
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
        )
    except APITimeoutError:
        raise AIError(f"Error: timed out after {timeout}s")
    except APIError as exc:
        raise AIError(f"Error: {exc}")
    except Exception as exc:
        raise AIError(f"Error: {exc}")

    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "").strip() if choice else ""
    output_tokens = response.usage.completion_tokens if response.usage else 0

    return AskResult(
        text=text,
        output_tokens=output_tokens,
        model=resolved_model,
        effort=effort or "medium",
    )
