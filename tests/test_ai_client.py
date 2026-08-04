"""ai_client provider routing — proves `_client()` picks the right base URL
and API key for each provider, fails loudly with the relevant env-var name
when the key is missing, and resolves `_resolve_model` per-provider.
"""

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


def test_resolve_model_explicit_pin_still_wins(monkeypatch):
    """A per-pipeline `model="deepseek-v4-flash"` override on the deepseek
    provider still flows through (and survives the provider toggle)."""
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "deepseek")
    assert ai_client._resolve_model("deepseek-v4-flash") == "deepseek-v4-flash"
    # Even on the minimax provider, an explicit override wins.
    monkeypatch.setattr(db, "get_ai_provider", lambda conn=None: "minimax")
    assert ai_client._resolve_model("deepseek-v4-flash") == "deepseek-v4-flash"


def test_model_default_constant_is_v4_pro():
    """Sanity-check the constant flip itself — separate from the per-provider
    fallback logic, this is the hard-coded value of `ai.MODEL_DEFAULT`.
    The single-source-of-truth contract is: when provider == deepseek and
    no explicit model was passed, _resolve_model returns this value."""
    assert ai_client.MODEL_DEFAULT == "deepseek-v4-pro"


def test_minimax_constants():
    assert ai_client.MINIMAX_BASE_URL == "https://api.minimax.io/v1"
    assert ai_client.MINIMAX_MODEL == "MiniMax-M3"
