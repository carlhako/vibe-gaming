"""
ai_client — prompt DeepSeek from anywhere in this project.

    import ai_client as ai
    answer = ai.ask("what is the capital of France?").text

`ask()` is the one-shot text API; `ask_with_tools()` is the multi-turn
function-calling API used by the game-generation loop (caller owns the
message list and appends tool results between calls).

Drop-in replacement for home-net's `irc_bot.libs.ai` (same `ask()` shape:
`AskResult(text, input_tokens, output_tokens, model, effort)`, `AIError`) so
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

import copy
import json
import logging
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

_logger = logging.getLogger(__name__)


class AIError(Exception):
    """Raised when a DeepSeek prompt fails. str(exc) is a displayable message."""


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    effort: str
    raw_response: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as the model produced it


@dataclass
class ToolAskResult:
    message: dict         # assistant message dict, ready to append to the conversation
    tool_calls: list[ToolCall]
    text: str             # any plain-text content alongside/instead of tool calls
    input_tokens: int
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


def _resolve_thinking(
    effort: str | None, temperature: float | None
) -> tuple[dict, str | None, str, float | None]:
    """Map `effort` onto DeepSeek V4's per-request thinking-mode toggle.

    "high"/"max" enable thinking mode: `thinking` goes in `extra_body` (no
    native SDK field for it), while `reasoning_effort` is returned separately
    to be passed as its own top-level kwarg — matching DeepSeek's documented
    example (https://api-docs.deepseek.com/guides/thinking_mode) rather than
    relying on extra_body's top-level merge to carry it. temperature is
    documented as a no-op in thinking mode, so it's only forwarded if the
    caller set one explicitly. Anything else disables thinking and defaults
    temperature to 0.0 — DeepSeek's own recommendation for code/math — unless
    the caller overrode it.

    Returns (extra_body, reasoning_effort_kwarg, resolved_effort_label, resolved_temperature).
    """
    if effort in ("high", "max"):
        extra_body = {"thinking": {"type": "enabled"}}
        return extra_body, effort, effort, temperature
    extra_body = {"thinking": {"type": "disabled"}}
    return extra_body, None, "non-thinking", (0.0 if temperature is None else temperature)


def redact_tool_call_arguments(raw_response: dict) -> dict:
    """Deep-copy `raw_response` with each tool call's `arguments` blanked
    out. For this project that's submit_game's full generated
    index.html/js source — large, already persisted to disk/DB elsewhere,
    and not useful (or safe to duplicate indefinitely) in a debug log.
    Everything else (ids, timestamps, finish_reason, usage,
    reasoning_content when thinking mode is on) is kept as-is."""
    redacted = copy.deepcopy(raw_response)
    for choice in redacted.get("choices") or []:
        message = choice.get("message") or {}
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = f"<stripped {len(arguments)} chars of tool-call arguments>"
    return redacted


def _log_response(raw_response: dict) -> None:
    """Log the full DeepSeek response payload at DEBUG for future
    debugging (e.g. verifying thinking-mode fields), minus tool-call
    arguments — see redact_tool_call_arguments."""
    if _logger.isEnabledFor(logging.DEBUG):
        _logger.debug(
            "DeepSeek response payload: %s",
            json.dumps(redact_tool_call_arguments(raw_response), default=str),
        )


def _log_api_error(exc: APIError) -> None:
    """Log whatever payload the API sent back with an error response, for
    the same future-debugging purpose as _log_response."""
    if _logger.isEnabledFor(logging.DEBUG):
        _logger.debug("DeepSeek error response payload: %s", json.dumps(exc.body, default=str))


def ask(
    prompt: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    temperature: float | None = None,
    timeout: int | None = 120,
    response_format: dict | None = None,
    **_ignored,
) -> AskResult:
    """Run a one-shot DeepSeek prompt and return the result. Raises AIError.

    `response_format` (e.g. {"type": "json_object"}) is passed straight
    through to the API when given. DeepSeek's JSON mode is "designed to,"
    not guaranteed to, return valid JSON, so callers requesting it should
    still parse defensively. Unverified whether thinking mode rejects a
    forced response_format the way it rejects a forced tool_choice (see
    _resolve_tool_choice) — nothing here downgrades it automatically, so
    check empirically before combining response_format with effort
    "high"/"max".

    `**_ignored` absorbs kwargs from the home-net `ai.ask()` interface that
    have no DeepSeek equivalent (e.g. web_search) so callers ported over
    unchanged don't need per-call edits.
    """
    resolved_model = _resolve_model(model)
    extra_body, reasoning_effort, resolved_effort, resolved_temperature = _resolve_thinking(effort, temperature)
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
    if reasoning_effort is not None:
        create_kwargs["reasoning_effort"] = reasoning_effort
    if resolved_temperature is not None:
        create_kwargs["temperature"] = resolved_temperature
    if response_format is not None:
        create_kwargs["response_format"] = response_format

    try:
        response = client.chat.completions.create(**create_kwargs)
    except APITimeoutError:
        raise AIError(f"Error: timed out after {timeout}s")
    except APIError as exc:
        _log_api_error(exc)
        raise AIError(f"Error: {exc}")
    except Exception as exc:
        raise AIError(f"Error: {exc}")

    response_dict = response.model_dump()
    _log_response(response_dict)

    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "").strip() if choice else ""
    input_tokens = response.usage.prompt_tokens if response.usage else 0
    output_tokens = response.usage.completion_tokens if response.usage else 0

    return AskResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=resolved_model,
        effort=resolved_effort,
        raw_response=response_dict,
    )


def _resolve_tool_choice(tool_choice, extra_body: dict):
    """DeepSeek's thinking mode (verified live, 2026-07-20) accepts `tools`
    but 400s on any forcing tool_choice — both "required" and a named
    {"type": "function", ...} — while accepting "auto"/"none"/omitted.
    Downgrade a forcing choice to "auto" when thinking is enabled so
    callers can always request the forced behavior and still run at
    effort "high"/"max"."""
    thinking_on = (extra_body.get("thinking") or {}).get("type") == "enabled"
    if thinking_on and tool_choice not in (None, "auto", "none"):
        return "auto"
    return tool_choice


def ask_with_tools(
    messages: list[dict],
    *,
    tools: list[dict],
    tool_choice: dict | str | None = "auto",
    model: str | None = None,
    effort: str | None = None,
    temperature: float | None = None,
    timeout: int | None = 120,
) -> ToolAskResult:
    """One turn of a multi-turn, function-calling conversation. The caller
    owns the message list: append the returned `.message`, then one
    {"role": "tool", "tool_call_id": ..., "content": ...} reply per tool
    call, and call again. Raises AIError.

    `reasoning_content` (present when thinking mode is on) is stripped from
    the returned `.message` — DeepSeek rejects requests that echo it back.
    A forcing `tool_choice` (named function or "required") is silently
    downgraded to "auto" when `effort` enables thinking mode — see
    _resolve_tool_choice; callers must tolerate an occasional reply with
    no tool call on that path.
    """
    resolved_model = _resolve_model(model)
    extra_body, reasoning_effort, resolved_effort, resolved_temperature = _resolve_thinking(effort, temperature)
    tool_choice = _resolve_tool_choice(tool_choice, extra_body)

    client = _client()
    create_kwargs = dict(
        model=resolved_model,
        messages=messages,
        tools=tools,
        timeout=timeout,
        extra_body=extra_body,
    )
    if tool_choice is not None:
        create_kwargs["tool_choice"] = tool_choice
    if reasoning_effort is not None:
        create_kwargs["reasoning_effort"] = reasoning_effort
    if resolved_temperature is not None:
        create_kwargs["temperature"] = resolved_temperature

    try:
        response = client.chat.completions.create(**create_kwargs)
    except APITimeoutError:
        raise AIError(f"Error: timed out after {timeout}s")
    except APIError as exc:
        _log_api_error(exc)
        raise AIError(f"Error: {exc}")
    except Exception as exc:
        raise AIError(f"Error: {exc}")

    response_dict = response.model_dump()
    _log_response(response_dict)

    choice = response.choices[0] if response.choices else None
    if choice is None:
        raise AIError("Error: response contained no choices")

    message_dict = choice.message.model_dump(exclude_none=True)
    message_dict.pop("reasoning_content", None)

    tool_calls = [
        ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
        for tc in (choice.message.tool_calls or [])
    ]
    text = (choice.message.content or "").strip()
    input_tokens = response.usage.prompt_tokens if response.usage else 0
    output_tokens = response.usage.completion_tokens if response.usage else 0

    return ToolAskResult(
        message=message_dict,
        tool_calls=tool_calls,
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=resolved_model,
        effort=resolved_effort,
        raw_response=response_dict,
    )
