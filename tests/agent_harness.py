"""Shared scaffolding for the agent tests, and the append-only invariant.

The invariant (Sprint 6a, docs/multifile-agent/06a-cache-snapshot-and-edits.md):

    No message that has already been included in a request to the model is
    ever mutated or removed.

It exists because DeepSeek's prefix cache is byte-exact and prefix-only, and
bills a cached token at 1/120th of a fresh one (v4-pro). Editing a message at
position k therefore does not save anything — it re-bills everything from k
onward at full price, on that turn and every turn after it. Sprint 6 pruned
stale read_file results in place and measurement showed each prune collapsing
the cached prefix from ~44,000 tokens back to ~4,500, twice in one run.

This is checked on *every* agent test rather than in one dedicated case
because the failure is invisible from the outside: a run that mutates its
prefix still produces exactly the right files, passes verification, and ships.
Only the bill changes. A test that has to be remembered would not have caught
Sprint 6's regression either.
"""

import contextlib
import copy
from unittest import mock

import ai_client as ai


def assert_append_only(seen):
    """Assert that each request's message list extends the previous one.

    `seen[i]` is a deepcopy of the conversation as it stood at the START of
    call i, so everything turn i adds lands strictly after `len(seen[i])` —
    the prefix comparison is exact, not approximate.

    A fresh run always opens with exactly [system, user], and one patch scope
    can cover more than one run (auto-format explodes and then enhances; the
    explode-fallback test drops through to the single-file path). A 2-message
    entry after the first is therefore a run boundary, not a truncation, and
    each run is checked independently.
    """
    start = 0
    for i in range(1, len(seen)):
        if len(seen[i]) == 2:  # a new run's opening [system, user]
            start = i
            continue
        prev, nxt = seen[i - 1], seen[i]
        assert len(nxt) >= len(prev), (
            f"turn {i} (run starting at turn {start}) sent FEWER messages than "
            f"turn {i - 1} ({len(nxt)} < {len(prev)}) — a message already sent "
            "to the model was removed, which collapses DeepSeek's prefix cache"
        )
        assert nxt[:len(prev)] == prev, (
            f"turn {i} (run starting at turn {start}) mutated the prefix already "
            f"sent at turn {i - 1} — this collapses DeepSeek's prefix cache from "
            "the mutated message onward, for the rest of the run. See "
            "docs/multifile-agent/06a-cache-snapshot-and-edits.md."
        )


@contextlib.contextmanager
def scripted_asks(side_effect=None, *, return_value=None):
    """Patch ai_client.ask_with_tools, recording every conversation sent.

    A drop-in for `mock.patch.object(ai, "ask_with_tools", ...)` accepting the
    same two shapes — a list of scripted results, a callable, or a fixed
    `return_value` — that additionally yields the recorded conversations and
    asserts the append-only invariant on a clean exit. It stays quiet when the
    body raises, so a genuine failure is not buried under a second one.
    """
    seen = []

    def scripted(messages, **kwargs):
        seen.append(copy.deepcopy(messages))
        if callable(side_effect):
            return side_effect(messages, **kwargs)
        if side_effect is not None:
            return side_effect[len(seen) - 1]
        return return_value

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted):
        yield seen
    assert_append_only(seen)
