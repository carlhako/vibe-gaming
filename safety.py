"""
game_web/safety.py — static safety scan for AI-generated game HTML.

There is no safe way to statically sandbox arbitrary JS the way
plugin_generator.check_plugin_safety's AST walk sandboxes generated Python —
there is no trustworthy JS AST parser in the stdlib. This is a regex
blocklist for dangerous call shapes, plus a CDN allowlist for any externally
loaded script/stylesheet, mirroring plugin_generator's "blocklist, not
allowlist, for code shape; allowlist for network reach" tradeoff. It runs
before write + smoke test in both game_generator and game_enhancer.

The production iframe (game_web/templates/index.html) already sandboxes
played games (`sandbox="allow-scripts allow-pointer-lock"`, no
allow-same-origin — cookies/localStorage/parent-frame access are opaque-origin
blocked by the browser regardless of this scan). The Playwright smoke test
serves the game over a throwaway 127.0.0.1 origin under game_csp() rather than
in that iframe, so the browser-level sandbox is *not* what protects that load —
this scan still is.

game_csp() lives here rather than in app.py so the served CSP and the
generation-time allowlist below are built from the same data and cannot drift.
smoke_test.py applies the same policy to its own origin, which is what makes a
CSP violation a generation-time failure instead of a production one.
"""

import re

import engines

_BANNED_PATTERNS = [
    (re.compile(r"\beval\s*\("), "call to eval()"),
    (re.compile(r"\bnew\s+Function\s*\("), "use of the Function constructor"),
    (re.compile(r"document\.cookie"), "access to document.cookie"),
    (re.compile(r"document\.write\s*\("), "call to document.write()"),
    (re.compile(r"\blocalStorage\b"), "access to localStorage"),
    (re.compile(r"\bsessionStorage\b"), "access to sessionStorage"),
    (re.compile(r"\bindexedDB\b"), "access to indexedDB"),
    (re.compile(r"window\.parent"), "access to window.parent"),
    (re.compile(r"window\.top\b"), "access to window.top"),
    (re.compile(r"javascript:"), "javascript: URL"),
    (re.compile(r'<meta[^>]+http-equiv\s*=\s*["\']refresh["\']', re.IGNORECASE),
     "meta refresh redirect"),
    (re.compile(r"\blocation\.(?:href|replace|assign)\s*[=(]"),
     "script-based page navigation (location.href/replace/assign)"),
    (re.compile(r"\bwindow\.location\s*="),
     "script-based page navigation (window.location assignment)"),
]

ALLOWED_CDN_HOSTS = {
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
    "ajax.googleapis.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "threejs.org",
}

_CDN_ORIGINS = " ".join(f"https://{host}" for host in sorted(ALLOWED_CDN_HOSTS))

_SRC_RE = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_ACTION_RE = re.compile(r'\baction\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_CSS_URL_RE = re.compile(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', re.IGNORECASE)

# Whole opening tags, so an attribute order this scanner didn't anticipate
# (<link href=... rel=stylesheet> vs <link rel=stylesheet href=...>) can't slip
# a ref past the local-reference rule below.
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_IMPORTMAP_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']importmap["\'][^>]*>.*?</script\s*>',
    re.IGNORECASE | re.DOTALL,
)
_REMOTE_PREFIXES = ("http://", "https://", "//")


def _host_of(url: str) -> str | None:
    m = re.match(r"^(?:https?:)?//([^/]+)", url, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
    return m.group(1) if m else None


def game_csp(origin: str) -> str:
    """Content-Security-Policy for served game HTML — /play/<slug> in
    production, the smoke test's throwaway origin during verification.

    `origin` is the scheme://host[:port] the game itself is served from. It has
    to be spelled out rather than left to 'self' because a game runs in a
    sandbox with no allow-same-origin, which gives it an opaque origin that
    'self' cannot be relied on to match; a host-source with a path prefix is
    plain URL matching and always works. The prefix also keeps the allowance
    narrow: the vendored three.js tree, nothing else on this host.
    """
    vendor_src = origin.rstrip("/") + "/vendor/three/"
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        + vendor_src + " " + _CDN_ORIGINS + "; "
        "style-src 'self' 'unsafe-inline' " + _CDN_ORIGINS + "; "
        "font-src 'self' " + _CDN_ORIGINS + "; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "form-action 'none'; "
        "frame-ancestors 'self'; "
        "base-uri 'none';"
    )


def _scan_local_refs(html: str, engine: str | None, version: str) -> list[str]:
    """Script/stylesheet refs that point somewhere this site does not serve.

    Only index.html is ever served out of a game directory, so a local
    <script src>/<link rel=stylesheet> ref is a guaranteed-broken reference —
    except under the vendored three.js prefix, which a 3D game may legitimately
    name. Deliberately narrow: <img src>, <a href> and fragments are left alone
    because those have existing legitimate uses in generated games.
    """
    allowed_prefix = engines.vendor_url_prefix(version) if engine else None
    violations = []

    refs = [("script", _attr(tag, "src")) for tag in _SCRIPT_TAG_RE.findall(html)]
    for tag in _LINK_TAG_RE.findall(html):
        if (_attr(tag, "rel") or "").strip().lower() == "stylesheet":
            refs.append(("stylesheet", _attr(tag, "href")))

    for kind, ref in refs:
        if not ref:
            continue
        ref = ref.strip()
        if not ref or ref.startswith("#") or ref.startswith(_REMOTE_PREFIXES):
            continue  # remote refs are the CDN allowlist's job, below
        if allowed_prefix and ref.startswith(allowed_prefix):
            continue
        violations.append(
            f"local {kind} reference '{ref}' — nothing but index.html is served "
            f"from a game directory, so this would 404 at runtime; inline it "
            f"instead"
        )
    return violations


def _scan_engine_scripts(html: str, engine: str | None) -> list[str]:
    """A 3D game's engine is served by this site, so it has no business loading
    a script from a CDN — including an allow-listed one.

    Without this rule the generic CDN allowance in the prompts is enough to
    talk a model into `<script src="https://cdn.jsdelivr.net/npm/three...">`,
    which every other check would wave through: jsdelivr is allow-listed, and
    the ref isn't local. That would quietly reintroduce the external
    dependency self-hosting exists to remove.
    """
    if engine != engines.ENGINE_THREE:
        return []
    violations = []
    for tag in _SCRIPT_TAG_RE.findall(html):
        src = (_attr(tag, "src") or "").strip()
        if src.startswith(_REMOTE_PREFIXES):
            violations.append(
                f"3D game loads an external script ({src}) — three.js is served "
                f"by this site and reached with `import * as THREE from 'three'`; "
                f"remove the tag"
            )
    return violations


def _scan_importmaps(html: str, engine: str | None, version: str) -> list[str]:
    """Import maps, which the src=/href= checks below structurally cannot see —
    their URLs are JSON values, not attributes. A 3D game gets exactly one, the
    canonical one engines.normalize() injects; anything else is a violation.
    """
    found = _IMPORTMAP_RE.findall(html)
    if not found:
        return []
    if engine != engines.ENGINE_THREE:
        return ["import map in a game that does not use an engine that needs one"]
    canonical = engines.importmap_html(version)
    if found == [canonical]:
        return []
    return [
        "non-canonical import map — a 3D game's import map is injected by the "
        "site and must not be written or edited by hand"
    ]


def scan(html: str, engine: str | None = None,
         version: str = engines.DEFAULT_THREE_VERSION) -> list[str]:
    """Return a list of violation strings — empty means safe. Never raises.

    `engine`/`version` describe the game being scanned (see engines.py); the
    defaults keep every pre-existing 2D caller working unchanged.
    """
    violations = []
    for pattern, label in _BANNED_PATTERNS:
        if pattern.search(html):
            violations.append(label)

    for url in _SRC_RE.findall(html):
        if url.startswith(("http://", "https://", "//")):
            host = _host_of(url)
            if host and host not in ALLOWED_CDN_HOSTS:
                violations.append(f"external resource from disallowed host '{host}' ({url})")

    # Games have no legitimate reason to submit a form anywhere — not even to
    # an allowlisted CDN host, since CDNs serve static assets and don't accept
    # posts — so any non-empty, non-fragment action is a violation outright.
    for action in _ACTION_RE.findall(html):
        action = action.strip()
        if action and not action.startswith("#"):
            violations.append(f"form with external action '{action}'")

    for url in _CSS_URL_RE.findall(html):
        url = url.strip()
        if url.startswith("data:"):
            continue
        if url.startswith(("http://", "https://", "//")):
            host = _host_of(url)
            if host and host not in ALLOWED_CDN_HOSTS:
                violations.append(f"external resource from disallowed host '{host}' ({url})")

    violations.extend(_scan_importmaps(html, engine, version))
    violations.extend(_scan_local_refs(html, engine, version))
    violations.extend(_scan_engine_scripts(html, engine))

    return violations
