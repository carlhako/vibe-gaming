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

As of 2026-07, DeepSeek's own API (api.deepseek.com) exposes exactly two
model families — `deepseek-v4-flash` and `deepseek-v4-pro` (confirmed via
`GET /models`) — each with a "thinking" (chain-of-thought reasoning) mode
that's toggled per-request rather than selected via a separate model name.
The old `deepseek-chat` / `deepseek-reasoner` names (which used to be the
way to pick fast-vs-reasoning) are legacy aliases for deepseek-v4-flash's
non-thinking/thinking modes and are retired 2026-07-24 — do not use them.

So unlike the old two-model scheme, `effort` no longer selects the model —
`model` does (defaulting to deepseek-v4-flash). `effort` now toggles
thinking mode instead: "high" or "max" enables it (at that reasoning depth,
via the `thinking`/`reasoning_effort` request fields), anything else runs
the fast non-thinking path with temperature pinned to 0.0 (DeepSeek's own
recommended setting for code/math output — see
https://api-docs.deepseek.com/quick_start/parameter_settings), unless the
caller passes an explicit `temperature`. Pass `model` explicitly to pin
deepseek-v4-pro or (until retirement) a legacy name.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langsmith.wrappers import wrap_openai
from openai import APIError, APITimeoutError, OpenAI

load_dotenv()

BASE_URL = "https://api.deepseek.com"
MODEL_DEFAULT = "deepseek-v4-flash"  # 284B total / 13B active MoE, 1M ctx — fast + cheap
MODEL_PRO = "deepseek-v4-pro"        # 1.6T total / 49B active MoE, 1M ctx — best quality

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class AIError(Exception):
    """Raised when a DeepSeek prompt fails. str(exc) is a displayable message."""


@dataclass
class AskResult:
    text: str
    output_tokens: int
    model: str
    effort: str
    raw_response: dict


def _client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise AIError("Error: DEEPSEEK_API_KEY is not set (see .env.example)")
    # wrap_openai adds LangSmith tracing per chat.completions.create call
    # (prompts, response, tokens, latency). No-op unless LANGSMITH_TRACING
    # is truthy in the environment; upload failures never raise into us.
    return wrap_openai(OpenAI(api_key=api_key, base_url=BASE_URL))


def _resolve_model(model: str | None) -> str:
    return model or MODEL_DEFAULT


def _resolve_thinking(effort: str | None, temperature: float | None) -> tuple[dict, str, float | None]:
    """Map `effort` onto DeepSeek V4's per-request thinking-mode toggle.

    "high"/"max" enable thinking mode at that reasoning_effort (temperature
    is documented as a no-op there, so it's only forwarded if the caller set
    one explicitly). Anything else disables thinking and defaults
    temperature to 0.0 — DeepSeek's own recommendation for code/math — unless
    the caller overrode it.
    """
    if effort in ("high", "max"):
        extra_body = {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
        return extra_body, effort, temperature
    extra_body = {"thinking": {"type": "disabled"}}
    return extra_body, "non-thinking", (0.0 if temperature is None else temperature)


def ask(
    prompt: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    temperature: float | None = None,
    timeout: int | None = 120,
    **_ignored,
) -> AskResult:
    """Run a one-shot DeepSeek prompt and return the result. Raises AIError.

    `**_ignored` absorbs kwargs from the home-net `ai.ask()` interface that
    have no DeepSeek equivalent (e.g. web_search) so callers ported over
    unchanged don't need per-call edits.
    """
    resolved_model = _resolve_model(model)
    extra_body, resolved_effort, resolved_temperature = _resolve_thinking(effort, temperature)
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    client = _client()
    create_kwargs = dict(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        timeout=timeout,
        extra_body=extra_body,
    )
    if resolved_temperature is not None:
        create_kwargs["temperature"] = resolved_temperature

    try:
        response = client.chat.completions.create(**create_kwargs)
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
        effort=resolved_effort,
        raw_response=response.model_dump(),
    )
