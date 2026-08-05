"""
ai_client — prompt an OpenAI-compatible AI provider from anywhere in this project.

    import ai_client as ai
    answer = ai.ask("what is the capital of France?").text

`ask()` is the one-shot text API; `ask_with_tools()` is the multi-turn
function-calling API used by the game-generation loop (caller owns the
message list and appends tool results between calls).

Drop-in replacement for home-net's `irc_bot.libs.ai` (same `ask()` shape:
`AskResult(text, input_tokens, output_tokens, model, effort)`, `AIError`) so
game_generator.py / game_enhancer.py needed near-zero changes when ported
out of that project.

Two providers are supported, selectable at runtime via the admin/stats
provider toggle (db.get_ai_provider returns "deepseek" or "minimax"):

  * **deepseek** — api.deepseek.com via the `openai` SDK pointed at
    DeepSeek's OpenAI-compatible base URL. Requires `DEEPSEEK_API_KEY` in
    the environment (see .env.example). As of 2026-07, DeepSeek's own
    API exposes two model families — `deepseek-v4-flash` and
    `deepseek-v4-pro` (confirmed via `GET /models`) — each with a
    "thinking" (chain-of-thought reasoning) mode that's toggled per-request
    rather than selected via a separate model name. `effort` no longer
    selects the model — `model` does (defaulting to deepseek-v4-pro). It
    toggles thinking mode instead: "high" or "max" enables it (at that
    reasoning depth, via the `thinking`/`reasoning_effort` request fields),
    anything else runs the fast non-thinking path with temperature pinned
    to 0.0 (DeepSeek's own recommended setting for code/math output — see
    https://api-docs.deepseek.com/quick_start/parameter_settings), unless
    the caller passes an explicit `temperature`. Pass `model` explicitly to
    pin deepseek-v4-flash, deepseek-v4-pro, or (until retirement) a legacy
    name.

  * **minimax** — api.minimax.io/v1 via the same `openai` SDK. Requires
    `MINIMAX_API_KEY` in the environment. The model id sent on the wire
    is `MiniMax-M3`. Accepts the same `thinking` per-request toggle as
    DeepSeek but with different wire values — `"adaptive"` (thinking on,
    server picks reasoning depth per-call) vs `"disabled"`. The
    per-pipeline `effort` semantic is identical on both providers;
    `_resolve_thinking` maps `effort in ("high", "max")` to the
    provider's "thinking on" wire string (`enabled` for deepseek,
    `adaptive` for minimax). The top-level `reasoning_effort` kwarg is
    forwarded unconditionally — DeepSeek accepts it; M3's behavior on
    it has not been verified live in this environment (no
    `MINIMAX_API_KEY` configured); see the TODO in `_resolve_thinking`.

Missing-key handling: `_client()` raises `AIError("Error: <VAR> is not set
(see .env.example)")` before opening any connection, so a flip to a
provider whose env var is unset fails loudly with a clear variable name
rather than 401'ing silently mid-request.

The active provider is read fresh inside `_client()` on every call (no
in-process cache), so a gunicorn multi-worker process sees an admin toggle
flip on the very next request — no restart needed.
"""

import copy
import json
import logging
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import APIError, APITimeoutError, OpenAI

import db

# M3's thinking-mode chain-of-thought leaks into message.content as
# ``<think>...</think>`` blocks preceding the actual answer (verified live
# 2026-08-05 across four moderation calls — see moderation_calls rows 8-11
# in vibegames.db). DeepSeek V4 keeps the same reasoning in a SEPARATE
# ``reasoning_content`` field, which ask_with_tools() already strips (see
# the comment on message_dict above). Stripping inline here symmetrizes
# the two providers' ``AskResult.text`` / message-content shape, so:
#   * ask() callers (content_moderation.check_game, ai_qa.answer_question)
#     don't see the reasoning inline and can parse JSON / sanitize HTML
#     without a think-block prefix.
#   * ask_with_tools() callers don't echo the model's old chain-of-thought
#     back as part of the assistant message on the next turn (saves the
#     re-send cost; mostly clean thinking-on responses have idempotent
#     tails anyway).
# raw_response is left untouched on purpose — db.moderation_calls uses
# raw_response verbatim and the chain-of-thought is the part admins want
# to see when a verdict is suspicious. Idempotent on already-clean text.
_THINK_BLOCK_RE = re.compile(r"<think.*?</think>", re.DOTALL)


def _strip_think_blocks(text: str) -> str:
    """Drop M3's chain-of-thought ``<think>...</think>`` blocks from a
    one-shot ``AskResult.text`` or an ``ask_with_tools`` ``message.content``.
    See the comment on ``_THINK_BLOCK_RE`` above for the provider
    asymmetry this closes."""
    return _THINK_BLOCK_RE.sub("", text)

load_dotenv()

BASE_URL = "https://api.deepseek.com"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_MODEL = "MiniMax-M3"

MODEL_DEFAULT = "deepseek-v4-pro"  # 1.6T total / 49B active MoE, 1M ctx — best quality; was deepseek-v4-flash before 2026-07
MODEL_PRO = "deepseek-v4-pro"      # alias kept for callers that named it explicitly

# Per-provider known model sets. _resolve_model uses these to decide whether
# an explicit per-pipeline `model=` pin is honored or silently swapped for
# the provider default (see _resolve_model's docstring). Update in lockstep
# with each provider's `GET /models` if a new model ships — a model not in
# the active provider's set will fall back to the provider default on the
# admin toggle, which is friendlier than a 400 from the API.
_DEEPSEEK_MODELS = frozenset({"deepseek-v4-pro", "deepseek-v4-flash"})
_MINIMAX_MODELS = frozenset({MINIMAX_MODEL})

# DeepSeek V4's per-response completion ceiling, pinned explicitly so it's
# stable and the generation loop's finish_reason == "length" truncation
# handling has a known number to reason about. In thinking mode this budget
# is SHARED with reasoning_content tokens, so chain-of-thought eats into
# what's left for the actual answer (a large enhancement's truncation risk —
# see game_generator's run_generation_attempts, which drops thinking mode on
# a truncation retry).
#
# CORRECTED 2026-07-26 (Sprint 6 of docs/multifile-agent/, prompted by a
# real pilot re-run stalling in a way that pointed at this): the original
# 65536 here was NOT DeepSeek's actual ceiling — it was self-confirming.
# Every prior "verification" always passed max_tokens=65536 explicitly (this
# constant, unconditionally, since every caller's default IS this constant),
# so of course every truncation landed at exactly 65536; nobody had tried
# asking for more. A direct probe this session (see
# docs/multifile-agent/05-migration-and-pilot.md) requested max_tokens as
# high as 384001 and every request was accepted (never rejected for
# exceeding some server-side cap), and a forced long deterministic
# generation with max_tokens=150000 produced exactly 150000 output tokens
# (finish_reason == "length", still generating, not stopping early) — i.e.
# the real ceiling is AT LEAST 150000, confirmed live. DeepSeek's own docs
# (api-docs.deepseek.com, fetched the same day) claim 384K for both
# deepseek-v4-flash and deepseek-v4-pro, in both thinking and non-thinking
# modes, but that number itself hasn't been independently verified live the
# way 150000 has, so this constant only claims what's actually been proven:
MAX_OUTPUT_TOKENS = 150000

# The INPUT side's ceiling — everything the model can see at once: system
# prompt, every prior turn, every tool result. Distinct from MAX_OUTPUT_TOKENS
# above, which caps a single response.
#
# Source: DeepSeek's own docs (api-docs.deepseek.com) and config.yaml.example's
# model table both give 1M context for deepseek-v4-flash and deepseek-v4-pro.
# The largest input actually observed live is far below it (~1.6M tokens across
# a whole 60-turn agent run, no single call close to this), so treat this the
# way MAX_OUTPUT_TOKENS' comment above insists on being treated: it is a
# DOCUMENTED figure, not a probed one. Re-verify before trusting it as a hard
# boundary — the 65536 lesson is that a number nobody has tested is just a
# number somebody wrote down.
#
# Only consumer today: agent.py's context guard, which stops a run at 95% of
# this rather than letting the API 400 mid-run. That use is conservative in the
# right direction — if the real window is larger, the guard fires early and the
# run's files are still verified and shipped; if it were smaller, the guard
# would fire late and the run would end the way it does today anyway.
CONTEXT_WINDOW_TOKENS = 1_048_576

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
    finish_reason: str = "stop"  # "length" means the output hit MAX_OUTPUT_TOKENS and was truncated
    cached_tokens: int = 0  # DeepSeek's prompt_cache_hit_tokens — the slice of input_tokens served from cache


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
    finish_reason: str = "stop"  # "length" means the output hit MAX_OUTPUT_TOKENS and was truncated
    cached_tokens: int = 0  # DeepSeek's prompt_cache_hit_tokens — the slice of input_tokens served from cache


def _client() -> OpenAI:
    """Return an `openai.OpenAI` client pointed at the active provider's
    endpoint. Reads db.get_ai_provider() on every call so an admin toggle
    flip takes effect on the next request, no restart needed.

    Missing-key handling: each provider checks its own env var and raises
    `AIError` with the variable's name in the message, before opening any
    connection — so a flip to a provider whose env var is unset fails
    loudly with a clear, fixable error rather than 401'ing silently
    mid-request."""
    if not db.is_ai_generation_enabled():
        raise AIError("Error: AI generation is currently disabled by an admin")
    provider = db.get_ai_provider()
    if provider == "minimax":
        api_key = os.environ.get("MINIMAX_API_KEY")
        if not api_key:
            raise AIError("Error: MINIMAX_API_KEY is not set (see .env.example)")
        return OpenAI(api_key=api_key, base_url=MINIMAX_BASE_URL)
    # Default: "deepseek" (and any unknown provider, which db.get_ai_provider
    # should never actually return — it validates on set and falls back to
    # "deepseek" on read for unknown values).
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise AIError("Error: DEEPSEEK_API_KEY is not set (see .env.example)")
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def _cached_tokens(usage) -> int:
    """DeepSeek's usage payload carries prompt_cache_hit_tokens alongside the
    standard OpenAI prompt_tokens/completion_tokens fields — not part of the
    openai SDK's typed CompletionUsage, but its BaseModel allows (and keeps)
    unknown fields, so it survives as a plain attribute on the response."""
    return getattr(usage, "prompt_cache_hit_tokens", 0) or 0 if usage else 0


def _resolve_model(model: str | None) -> str:
    """Resolve the model id passed on the wire. An explicit per-pipeline
    `model=` is honored only when it matches the active provider's known
    model set; otherwise we fall back to the active provider's default and
    log a WARNING. See `_resolve_thinking` for the parallel per-provider
    mapping of `effort` onto each provider's `thinking.type` wire string.

    Why the fallback and not a hard raise: the admin provider toggle
    (`db.set_ai_provider` via /admin/stats) overrides per-pipeline defaults
    without editing config.yaml. config.yaml.example hardcodes
    `model: "deepseek-v4-pro"` in newaiwebgame / enhanceaiwebgame /
    multifile_agent, so a fresh clone whose only M3 action is to flip the
    toggle would 400 with `unknown model 'deepseek-v4-pro'` on every
    enhance, generate, ask, and content-moderation call — the worst UX
    this code can deliver. The fallback makes the toggle "just work";
    a user who pinned a deepseek model deliberately and then flipped the
    toggle sees a log warning they can grep for, instead of a hidden
    routing change.

    None / empty `model` falls through to the provider default, unchanged.
    """
    provider = db.get_ai_provider()
    known = _MINIMAX_MODELS if provider == "minimax" else _DEEPSEEK_MODELS
    if model and model in known:
        return model
    if model:
        fallback = MINIMAX_MODEL if provider == "minimax" else MODEL_DEFAULT
        _logger.warning(
            "ai_client: model %r is not in the %s provider's known model set %s; "
            "falling back to the provider default %r. "
            "Update the pipeline's `model:` in config.yaml, set it to null to get "
            "this default unconditionally, or flip the admin provider toggle back.",
            model, provider, sorted(known), fallback,
        )
        return fallback
    return MINIMAX_MODEL if provider == "minimax" else MODEL_DEFAULT


def _thinking_type_on(provider: str) -> str:
    """The wire value of `thinking.type` that means "thinking on" for the
    given provider. DeepSeek uses "enabled" (the caller picks reasoning
    depth via the separate `reasoning_effort` kwarg); M3 uses "adaptive"
    — server picks reasoning depth per-call rather than via a caller
    knob (verified live 2026-08 by the 400 returned when we send
    "enabled": `invalid thinking.type: "enabled" (allowed: adaptive,
    disabled) (2013)`). Used by `_resolve_tool_choice` to detect
    thinking-on regardless of which provider's schema produced the
    extra_body. Both branches must stay in sync with `_resolve_thinking`."""
    if provider == "minimax":
        return "adaptive"
    return "enabled"


def _resolve_thinking(
    effort: str | None, temperature: float | None
) -> tuple[dict, str | None, str, float | None]:
    """Map `effort` onto the active provider's per-request thinking-mode
    toggle.

    "high"/"max" enable thinking mode; anything else disables it. The
    `thinking` payload goes in `extra_body` (no native SDK field for it).
    The wire string for "thinking on" is provider-specific: DeepSeek V4
    accepts `"enabled"` (https://api-docs.deepseek.com/guides/thinking_mode),
    M3 accepts only `"adaptive"` — see `_thinking_type_on` for the
    per-provider mapping and the verification citation.

    The per-pipeline `effort` semantic is identical on both providers;
    only the API wire string differs. The `resolved_effort` label
    returned to callers stays provider-agnostic so `AskResult.effort` /
    `ToolAskResult.effort` and log payloads render uniformly.

    `reasoning_effort` is returned separately to be passed as its own
    top-level kwarg — matching DeepSeek's documented example rather than
    relying on extra_body's top-level merge to carry it. M3's API is not
    (as of writing) documented to accept `reasoning_effort`; we forward
    it anyway because M3's silent drop is friendlier than a 400 in the
    unlikely case the provider rejects unknown kwargs. TODO(probe-with-
    minimax-key): verify this against a live M3 call before relying on
    the forward; if M3 400s on `reasoning_effort`, gate it on provider
    in `ask()` / `ask_with_tools()` where the kwarg is forwarded.

    temperature is documented as a no-op in thinking mode, so it's only
    forwarded if the caller set one explicitly. Anything else disables
    thinking and defaults temperature to 0.0 — DeepSeek's own
    recommended setting for code/math — unless the caller overrode it.

    Returns (extra_body, reasoning_effort_kwarg, resolved_effort_label,
    resolved_temperature).
    """
    provider = db.get_ai_provider()
    thinking_on = effort in ("high", "max")
    if thinking_on:
        extra_body = {"thinking": {"type": _thinking_type_on(provider)}}
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
    max_tokens: int | None = MAX_OUTPUT_TOKENS,
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
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens
    # TODO(probe-with-minimax-key): the top-level reasoning_effort kwarg below
    # is DeepSeek's documented parameter. M3's 400 on `thinking.type=enabled`
    # suggests it may also reject unknown kwargs — verify with a live call
    # before relying on this path. If M3 400s on `reasoning_effort`, gate this
    # on `db.get_ai_provider() != "minimax"`.
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
    # Strip M3's inline thinking-mode chain-of-thought (see _THINK_BLOCK_RE
    # docstring) so callers see only the final answer. raw_response keeps the
    # unstripped full payload for audit/debug.
    text = _strip_think_blocks((choice.message.content or "").strip()) if choice else ""
    input_tokens = response.usage.prompt_tokens if response.usage else 0
    output_tokens = response.usage.completion_tokens if response.usage else 0

    return AskResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=resolved_model,
        effort=resolved_effort,
        raw_response=response_dict,
        finish_reason=(choice.finish_reason or "stop") if choice else "stop",
        cached_tokens=_cached_tokens(response.usage),
    )


def _resolve_tool_choice(tool_choice, extra_body: dict):
    """DeepSeek's thinking mode (verified live, 2026-07-20) accepts `tools`
    but 400s on any forcing tool_choice — both "required" and a named
    {"type": "function", ...} — while accepting "auto"/"none"/omitted.
    M3's thinking mode is expected to share this behavior, but it has
    not been probed live (no `MINIMAX_API_KEY` is configured in this
    environment). Downgrade a forcing choice to "auto" when thinking is
    on — regardless of which provider's wire string for "thinking on"
    (`enabled` for deepseek, `adaptive` for minimax, via
    `_thinking_type_on`) the extra_body carries — so callers can always
    request the forced behavior and still run at effort "high"/"max"."""
    provider = db.get_ai_provider()
    thinking_on = (extra_body.get("thinking") or {}).get("type") == _thinking_type_on(provider)
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
    max_tokens: int | None = MAX_OUTPUT_TOKENS,
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
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens
    if tool_choice is not None:
        create_kwargs["tool_choice"] = tool_choice
    # TODO(probe-with-minimax-key): see same comment in ask() above. If M3
    # 400s on `reasoning_effort`, gate on provider here too.
    if reasoning_effort is not None:
        create_kwargs["reasoning_effort"] = reasoning_effort
    if resolved_temperature is not None:
        create_kwargs["temperature"] = resolved_temperature

    # NOTE: the try/except covers the full request-response cycle, not just
    # the network call. The response processing below assumes a specific
    # OpenAI-shaped payload (choice.message.tool_calls, response.usage, etc.)
    # — and a non-OpenAI-shaped response from M3 (or any future provider)
    # would otherwise raise something that's not AIError, propagate out of
    # the worker thread, and silently kill it: the caller catches AIError
    # and records an `ai_error` attempt, but a bare AttributeError /
    # IndexError / TypeError slips through that catch and leaves the
    # generation_requests row stuck in 'generating' with zero attempts and
    # zero agent_events (no _log_response at prod WARNING level either).
    # This block surfaces any unexpected shape as AIError so the caller
    # gets a chance to retry / sweep / report it instead of orphaning the
    # job. Verified empirically against the DeepSeek response (standard
    # OpenAI shape, still works) and the one M3 response observed live
    # (no `reasoning_content`, extra `service_tier`/`audio_content`/etc.
    # fields the SDK ignores — also works).
    try:
        response = client.chat.completions.create(**create_kwargs)
        response_dict = response.model_dump()
        _log_response(response_dict)

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise AIError("Error: response contained no choices")

        message_dict = choice.message.model_dump(exclude_none=True)
        # TODO(probe-with-minimax-key): the strip below is None-safe and
        # harmless if M3 doesn't return the field — but M3 may actively
        # reject echoing `reasoning_content` back the way DeepSeek does.
        # The current code does the right thing in both cases (strip when
        # present, no-op when absent), so no change needed unless M3
        # returns `reasoning_content` and the follow-up request 400s
        # despite our strip — probe to confirm.
        message_dict.pop("reasoning_content", None)
        # Same M3 chain-of-thought strip as ask() above, applied to the
        # message we'll hand back to the caller (and that callers feed
        # back as the prior assistant turn on the next call). Avoids
        # echoing M3's old reasoning as part of the assistant message —
        # saves the re-send cost on M3 and keeps the conversation history
        # shaped like DeepSeek's (whose reasoning_content is already gone).
        content = message_dict.get("content")
        if isinstance(content, str):
            message_dict["content"] = _strip_think_blocks(content)

        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (choice.message.tool_calls or [])
        ]
        text = _strip_think_blocks((choice.message.content or "").strip())
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
    except APITimeoutError:
        raise AIError(f"Error: timed out after {timeout}s")
    except APIError as exc:
        _log_api_error(exc)
        raise AIError(f"Error: {exc}")
    except AIError:
        raise
    except Exception as exc:
        # Any other exception here is an unexpected response shape from
        # the provider, not a transport/HTTP failure. Re-raising as
        # AIError with the type name keeps the caller's ai_error path
        # (which records the attempt and retries per max_attempts) instead
        # of letting the bare exception kill the worker thread silently.
        provider = db.get_ai_provider()
        raise AIError(
            f"Error: failed to parse {provider} response "
            f"({type(exc).__name__}: {exc})"
        )

    return ToolAskResult(
        message=message_dict,
        tool_calls=tool_calls,
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=resolved_model,
        effort=resolved_effort,
        raw_response=response_dict,
        finish_reason=choice.finish_reason or "stop",
        cached_tokens=_cached_tokens(response.usage),
    )
