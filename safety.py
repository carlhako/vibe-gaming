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
played games (`sandbox="allow-scripts allow-forms allow-pointer-lock"`, no
allow-same-origin — cookies/localStorage/parent-frame access are opaque-origin
blocked by the browser regardless of this scan), but the Playwright smoke
test loads generated HTML directly via file:// with no such sandbox, so this
scan is the only thing standing between an unsafe attempt and disk.
"""

import re

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

_SRC_RE = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _host_of(url: str) -> str | None:
    m = re.match(r"^(?:https?:)?//([^/]+)", url, re.IGNORECASE)
    return m.group(1).lower() if m else None


def scan(html: str) -> list[str]:
    """Return a list of violation strings — empty means safe. Never raises."""
    violations = []
    for pattern, label in _BANNED_PATTERNS:
        if pattern.search(html):
            violations.append(label)

    for url in _SRC_RE.findall(html):
        if url.startswith(("http://", "https://", "//")):
            host = _host_of(url)
            if host and host not in ALLOWED_CDN_HOSTS:
                violations.append(f"external resource from disallowed host '{host}' ({url})")

    return violations
