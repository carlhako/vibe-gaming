"""ai_client provider routing — proves `_client()` picks the right base URL
and API key for each provider, fails loudly with the relevant env-var name
when the key is missing, and resolves `_resolve_model` per-provider.
"""

from unittest import mock

import pytest
from openai import OpenAI

import ai_client
import db


@pytest.fixture(autouse=True)
def _clear_provider_cache(monkeypatch):
    """Each test sets the provider fresh; no in-process cache to bust because
    _client() reads db.get_ai_provider() on every call."""
    yield


def test_client_uses_deepseek_base_url_by_default(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    client = ai_client._client()
    assert isinstance(client, OpenAI)
    assert str(client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_client_uses_minimax_when_provider_set(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    client = ai_client._client()
    # The openai SDK normalizes base_url to add a trailing slash; compare
    # without that artifact.
    assert str(client.base_url).rstrip("/") == "https://api.minimax.io/v1"


def test_client_raises_when_deepseek_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    with pytest.raises(ai_client.AIError, match=r"DEEPSEEK_API_KEY is not set"):
        ai_client._client()


def test_client_raises_when_minimax_key_missing(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    with pytest.raises(ai_client.AIError, match=r"MINIMAX_API_KEY is not set"):
        ai_client._client()


def test_client_raises_when_ai_generation_disabled(monkeypatch):
    """Even with a valid key, the global kill switch blocks _client() — same
    shape as before the provider toggle existed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(db, "is_ai_generation_enabled",
                        lambda conn=None: False)
    with pytest.raises(ai_client.AIError, match=r"currently disabled"):
        ai_client._client()


def test_resolve_model_picks_provider_default(monkeypatch):
    """An explicit None model arg falls back to the active provider's
    default model id (not just MODEL_DEFAULT)."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    assert ai_client._resolve_model(None) == "deepseek-v4-pro"
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    assert ai_client._resolve_model(None) == "MiniMax-M3"


def test_resolve_model_pin_honored_on_matching_provider(monkeypatch):
    """An explicit per-pipeline `model=` is honored when it matches the
    active provider's known model set. Both directions, both providers:
    deepseek-v4-flash on deepseek, MiniMax-M3 on minimax, and v4-pro on
    deepseek — all should flow through unchanged."""
    # deepseek provider, deepseek-flash pin
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    assert ai_client._resolve_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert ai_client._resolve_model("deepseek-v4-pro") == "deepseek-v4-pro"
    # minimax provider, MiniMax-M3 pin
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    assert ai_client._resolve_model("MiniMax-M3") == "MiniMax-M3"


def test_resolve_model_cross_provider_falls_back(monkeypatch, caplog):
    """A pin that names a model the active provider doesn't expose falls
    back to the provider default and logs a WARNING — the friendly
    counterpart of the 400 the API would otherwise return. Both
    directions: deepseek pin on minimax, and minimax pin on deepseek.
    Without this, a config.yaml that still names `deepseek-v4-pro` after
    the admin flipped the toggle to MiniMax would 400 with `unknown
    model 'deepseek-v4-pro'` on every enhance / generate / ask /
    moderation call (job failing case, 2026-07-28)."""
    import logging
    caplog.set_level(logging.WARNING, logger="ai_client")

    # deepseek model name on the minimax provider -> MiniMax-M3
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    assert ai_client._resolve_model("deepseek-v4-pro") == "MiniMax-M3"
    assert any(
        "deepseek-v4-pro" in rec.getMessage() and "minimax" in rec.getMessage()
        for rec in caplog.records
    ), "expected a WARNING naming both the rejected model and the active provider"
    caplog.clear()

    # minimax model name on the deepseek provider -> deepseek-v4-pro
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    assert ai_client._resolve_model("MiniMax-M3") == "deepseek-v4-pro"
    assert any(
        "MiniMax-M3" in rec.getMessage() and "deepseek" in rec.getMessage()
        for rec in caplog.records
    ), "expected a WARNING naming both the rejected model and the active provider"


def test_resolve_model_none_falls_through_to_provider_default(monkeypatch, caplog):
    """A None / empty model arg still falls through to the provider default
    without a WARNING — only explicit-but-incompatible pins warn."""
    import logging
    caplog.set_level(logging.WARNING, logger="ai_client")
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    assert ai_client._resolve_model(None) == "deepseek-v4-pro"
    assert ai_client._resolve_model("") == "deepseek-v4-pro"
    assert caplog.records == []
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    assert ai_client._resolve_model(None) == "MiniMax-M3"
    assert caplog.records == []


def test_known_model_sets_cover_current_catalog():
    """Sanity check: the frozensets _DEEPSEEK_MODELS and _MINIMAX_MODELS in
    ai_client.py match the model ids the rest of this test file and
    config.yaml.example actually pass around. A drift here means a new
    model shipped and the cross-provider fallback would mask it
    silently."""
    assert ai_client._DEEPSEEK_MODELS == frozenset({"deepseek-v4-pro", "deepseek-v4-flash"})
    assert ai_client._MINIMAX_MODELS == frozenset({"MiniMax-M3"})


def test_model_default_constant_is_v4_pro():
    """Sanity-check the constant flip itself — separate from the per-provider
    fallback logic, this is the hard-coded value of `ai.MODEL_DEFAULT`.
    The single-source-of-truth contract is: when provider == deepseek and
    no explicit model was passed, _resolve_model returns this value."""
    assert ai_client.MODEL_DEFAULT == "deepseek-v4-pro"


def test_minimax_constants():
    assert ai_client.MINIMAX_BASE_URL == "https://api.minimax.io/v1"
    assert ai_client.MINIMAX_MODEL == "MiniMax-M3"


# ---------------------------------------------------------------------------
# ai_client._resolve_thinking / _thinking_type_on — per-provider wire-schema
# mapping (M3 400s on DeepSeek's "enabled"; the wire string for "thinking on"
# is provider-specific).
# ---------------------------------------------------------------------------

def test_thinking_type_on_per_provider():
    """The 'thinking on' wire string is provider-specific. DeepSeek uses
    'enabled' (caller picks reasoning depth via reasoning_effort); M3 uses
    'adaptive' (server picks per-call). Both must be returned by the same
    helper, so callers that detect thinking-on via dict comparison stay
    correct under either provider's extra_body."""
    assert ai_client._thinking_type_on("deepseek") == "enabled"
    assert ai_client._thinking_type_on("minimax") == "adaptive"
    # Unknown provider must default to DeepSeek's value — same posture as
    # _client() / _resolve_model treating unknown providers as deepseek.
    assert ai_client._thinking_type_on("unknown-provider") == "enabled"


def test_resolve_thinking_deepseek_high_enables(monkeypatch):
    """Regression: DeepSeek + effort='high' still emits the legacy wire
    string. _resolve_thinking has been provider-aware since 2026-08; this
    pins the DeepSeek branch to its old behavior."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    extra_body, reasoning_effort, resolved_effort, temperature = (
        ai_client._resolve_thinking("high", None)
    )
    assert extra_body == {"thinking": {"type": "enabled"}}
    assert reasoning_effort == "high"
    assert resolved_effort == "high"
    # temperature forwarded unchanged in thinking mode (documented no-op).
    assert temperature is None


def test_resolve_thinking_deepseek_none_disables(monkeypatch):
    """Regression: DeepSeek + effort=None still disables thinking with
    temperature defaulted to 0.0."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    extra_body, reasoning_effort, resolved_effort, temperature = (
        ai_client._resolve_thinking(None, None)
    )
    assert extra_body == {"thinking": {"type": "disabled"}}
    assert reasoning_effort is None
    assert resolved_effort == "non-thinking"
    assert temperature == 0.0


def test_resolve_thinking_minimax_high_uses_adaptive(monkeypatch):
    """M3 400s on 'enabled' (verified live 2026-08):
        invalid thinking.type: "enabled" (allowed: adaptive, disabled) (2013)
    so the M3 branch must emit 'adaptive' instead. reasoning_effort is
    forwarded unchanged — whether M3 actually accepts it is unverified
    (TODO(probe-with-minimax-key) in _resolve_thinking)."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    extra_body, reasoning_effort, resolved_effort, temperature = (
        ai_client._resolve_thinking("high", None)
    )
    assert extra_body == {"thinking": {"type": "adaptive"}}
    assert reasoning_effort == "high"
    assert resolved_effort == "high"
    assert temperature is None


def test_resolve_thinking_minimax_max_uses_adaptive(monkeypatch):
    """M3 + effort='max' — server picks depth per-call, so the wire string
    is the same as 'high'. The 'max' label still flows through to
    resolved_effort so callers see what the caller asked for."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    extra_body, reasoning_effort, resolved_effort, _temperature = (
        ai_client._resolve_thinking("max", None)
    )
    assert extra_body == {"thinking": {"type": "adaptive"}}
    assert reasoning_effort == "max"
    assert resolved_effort == "max"


def test_resolve_thinking_minimax_none_disables(monkeypatch):
    """M3 + effort=None — disabled wire string is identical across
    providers; only the 'on' value differs. temperature defaulted to 0.0."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    extra_body, reasoning_effort, resolved_effort, temperature = (
        ai_client._resolve_thinking(None, None)
    )
    assert extra_body == {"thinking": {"type": "disabled"}}
    assert reasoning_effort is None
    assert resolved_effort == "non-thinking"
    assert temperature == 0.0


def test_resolve_thinking_preserves_caller_temperature(monkeypatch):
    """An explicit temperature must survive the resolve — DeepSeek's
    documented no-op-in-thinking-mode applies; in non-thinking mode the
    caller-supplied value flows through unchanged (no defaulting to 0.0
    on top of a user-set value). Both providers, both modes."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    _, _, _, temperature = ai_client._resolve_thinking(None, 0.7)
    assert temperature == 0.7

    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    _, _, _, temperature = ai_client._resolve_thinking(None, 0.7)
    assert temperature == 0.7


# --- ask_with_tools() defensive response processing ---------------------------

def _patched_ask_with_tools(monkeypatch, mock_create):
    """Wire ask_with_tools() through a fake openai client whose
    `chat.completions.create` is `mock_create`. Bypasses the env-var key
    check in _client() by setting the deepseek key."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(ai_client, "_client", lambda: mock.Mock())
    monkeypatch.setattr(ai_client, "_client").chat.completions.create = mock_create
    return lambda *a, **kw: ai_client.ask_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "submit_game"}}],
        tool_choice="auto",
        effort="low",
    )


def test_ask_with_tools_unexpected_response_shape_raises_ai_error(monkeypatch):
    """A non-OpenAI-shaped response (e.g. from M3) that breaks the response
    processing after the network call must surface as AIError, not as a bare
    exception that kills the worker thread silently. See the NOTE in
    ai_client.ask_with_tools() for the full reasoning."""
    # Build a response where `choice.message` is None, so the
    # `choice.message.model_dump(...)` call raises AttributeError.
    # Previously this propagated out, killing the worker thread; now it
    # must come back as AIError with the provider name in the message.
    fake_response = mock.Mock()
    fake_response.model_dump.return_value = {"id": "x", "choices": [{"message": None}], "usage": {}}
    fake_response.choices = [mock.Mock()]
    fake_response.choices[0].message = None  # <-- the trigger
    fake_response.choices[0].finish_reason = "stop"
    fake_response.usage = mock.Mock(prompt_tokens=10, completion_tokens=5)

    fake_client = mock.Mock()
    fake_client.chat.completions.create.return_value = fake_response

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(ai_client, "_client", lambda: fake_client)
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")

    with pytest.raises(ai_client.AIError) as exc_info:
        ai_client.ask_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "submit_game"}}],
            tool_choice="auto",
            effort="low",
        )
    # Must mention the provider and the exception type so the ai_error
    # attempt's `detail` is diagnostic, not just "AttributeError".
    msg = str(exc_info.value)
    assert "deepseek" in msg
    assert "AttributeError" in msg


def test_ask_with_tools_normal_response_still_works(monkeypatch):
    """The defensive wrapping must not regress the happy path: a standard
    OpenAI-shaped response with a single tool_call still produces a
    ToolAskResult with the call's id/name/arguments extracted."""
    fake_response = mock.Mock()
    fake_response.model_dump.return_value = {"id": "x", "choices": [], "usage": {}}
    fake_response.choices = [mock.Mock()]
    fake_response.choices[0].message = mock.Mock()
    fake_response.choices[0].message.model_dump.return_value = {
        "role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "submit_game", "arguments": "{}"}}],
    }
    tc = mock.Mock()
    tc.id = "t1"
    tc.function.name = "submit_game"
    tc.function.arguments = "{}"
    fake_response.choices[0].message.tool_calls = [tc]
    fake_response.choices[0].message.content = ""
    fake_response.choices[0].finish_reason = "tool_calls"
    fake_response.usage = mock.Mock(prompt_tokens=10, completion_tokens=5)

    fake_client = mock.Mock()
    fake_client.chat.completions.create.return_value = fake_response

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-dummy")
    monkeypatch.setattr(ai_client, "_client", lambda: fake_client)
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")

    result = ai_client.ask_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "submit_game"}}],
        tool_choice="auto",
        effort="low",
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "submit_game"
    assert result.finish_reason == "tool_calls"
