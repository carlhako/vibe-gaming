# M3 (MiniMax) provider integration — follow-ups

The M3 provider toggle shipped in 2026-07 with the schema mapping
hard-coded based on the one error response we've seen live
(`invalid params, invalid thinking.type: "enabled" (allowed: adaptive,
disabled) (2013)`). Three things remain unverified because no
`MINIMAX_API_KEY` is configured in the development environment this
codebase lives in. They are flagged in `ai_client.py` with
`# TODO(probe-with-minimax-key):` comments. Resolve them with the
recipe below the next time a key is available locally — or by asking
someone who has one to run it.

## What ships unverified

1. **Top-level `reasoning_effort` kwarg.** `_resolve_thinking` returns it
   as a separate top-level kwarg (DeepSeek's documented shape), and
   `ask()` / `ask_with_tools()` forward it unconditionally. M3's API
   docs-as-known only describe `thinking.type`; whether M3 accepts
   (or silently drops, or 400s on) the unknown `reasoning_effort` kwarg
   is unknown. If M3 400s, gate the forwarding on provider — see
   `ai_client.py` line ~390 and ~482.

2. **`reasoning_content` echo behavior.** The `ask_with_tools` strip
   (`message_dict.pop("reasoning_content", None)`) is None-safe and
   harmless if M3 doesn't return the field. It only matters if M3
   actively rejects echoing `reasoning_content` back in the next
   request — same posture as DeepSeek. If M3 400s on a stripped-but-
   not-omitted echo, the strip is already correct and no code change
   is needed. If M3 400s because the field is *present* in our outgoing
   message, we have a deeper bug — the SDK isn't fully stripping it
   before resend.

3. **`max_tokens` ceiling.** DeepSeek's documented ceiling is 384K
   (`ai_client.MAX_OUTPUT_TOKENS = 150000` is probed, not just
   documented). M3 may cap lower — the generation loop's
   `finish_reason == "length"` truncation path in `game_generator.py`
   would fire more often, which is fine but worth knowing. If M3 caps
   higher, no change. **Probe with one large-output call** (set
   `askaiwebgame.max_tokens: 100000` and ask "write a 100000-token
   essay on the history of video games") and see where the API stops.

## Manual probe recipe

```bash
# 1. Set the env var (do this in a sandboxed test checkout, not prod):
echo 'MINIMAX_API_KEY=sk-...' >> .env

# 2. Flip the admin toggle to M3 (requires ADMIN_TOKEN in .env):
curl -X POST "http://localhost:8600/admin/ai-provider?token=$ADMIN_TOKEN" \
     -d "provider=minimax"

# 3. Verify the toggle took:
curl "http://localhost:8600/admin/stats?token=$ADMIN_TOKEN" | grep ai_provider

# 4. Submit one /games/new request with a tiny prompt so the call is
#    cheap and you can inspect the raw response payload:
curl -X POST "http://localhost:8600/games/new" \
     -d "description=a tiny pong clone"

# 5. Check /admin/stats for the raw response (DEBUG logging captures
#    the full payload minus tool-call arguments):
tail -n 200 vibegames.log | grep -A20 "minimax response payload\|error response"
```

Specifically check:

- (a) Does the call accept `reasoning_effort="high"` or 400 on it? Look
      at the raw request payload — `reasoning_effort` will be in the
      kwargs. If it's 400ing, that's why. If it's silent, M3 ignores
      it (fine).
- (b) Does the response include `reasoning_content`? Look at the
      raw response payload — `choices[0].message.reasoning_content`.
- (c) Submit a follow-up tool-result turn and see if M3 400s on the
      echo of `reasoning_content`. If yes, the strip is already doing
      the right thing. If it 400s even with the strip, the openai SDK
      is leaking the field past `message_dict.pop` — would need a
      deeper look.
- (d) What's the practical `max_tokens` ceiling before
      `finish_reason="length"` fires? Set `askaiwebgame.max_tokens` to a
      high value and ask for a long output.

## What to update based on probe results

- **(a) rejects**: gate `if reasoning_effort is not None` on
  `db.get_ai_provider() != "minimax"` in `ask()` and `ask_with_tools()`,
  and remove the `TODO(probe-with-minimax-key)` comments.
- **(a) silently drops**: no change. Drop the TODO.
- **(b) absent**: no change. The strip is already a no-op. Drop the TODO.
- **(b) present, (c) 400s on echo**: no change. The strip is correct. Drop the TODO.
- **(c) 400s even with strip**: deep bug — file an issue, don't ship.
- **(d) lower than 150000**: document the probed value as a comment
  near `MAX_OUTPUT_TOKENS` and consider a per-provider constant if the
  difference is material (likely not — the truncation retry path
  handles it).

After resolving: remove all three `TODO(probe-with-minimax-key)` comments
from `ai_client.py`.
