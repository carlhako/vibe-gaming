#!/usr/bin/env python3
"""scripts/vendor_three.py — fetch (or re-verify) the self-hosted three.js runtime.

Run by hand, never at boot or from a request: the vendored tree is committed,
and the app only ever reads it off disk. Downloading at startup would put an
external host back on the critical path, which is the whole thing self-hosting
exists to avoid.

    python3 scripts/vendor_three.py            # fetch VERSION into vendor/three/<v>/
    python3 scripts/vendor_three.py --verify   # re-hash what's on disk, no network
    python3 scripts/vendor_three.py --version 0.186.0

Adding a version is purely additive — existing games pin their own version in
meta.json and keep resolving to the tree they were generated against, so a new
three.js release can never retroactively change a game that already works.

Only files whose imports resolve entirely inside this vendored set may be added
to FILES. OrbitControls and PointerLockControls qualify (each imports from
'three' and nothing else); most other addons pull in siblings under
examples/jsm/ and would need those vendored too, or they 404 at runtime with
the import map pointing nowhere. Check with:

    grep -E "^\\s*import" <the addon file>
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

VERSION = "0.185.1"  # three.js r185
_CDN = "https://cdn.jsdelivr.net/npm/three@{version}/{path}"
_VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor" / "three"

# (upstream path within the npm package, path within vendor/three/<version>/)
# three.module.min.js imports "./three.core.min.js" by relative path, so both
# must land in the same directory under their upstream names. addons/ mirrors
# upstream's examples/jsm/ layout so the import map's "three/addons/" prefix
# maps straight onto it.
FILES = [
    ("build/three.module.min.js", "three.module.min.js"),
    ("build/three.core.min.js", "three.core.min.js"),
    ("examples/jsm/controls/OrbitControls.js", "addons/controls/OrbitControls.js"),
    # First-person games want pointer lock, and four pre-existing three.js
    # games in this arcade already import exactly this file.
    ("examples/jsm/controls/PointerLockControls.js",
     "addons/controls/PointerLockControls.js"),
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(version: str) -> int:
    dest_root = _VENDOR_ROOT / version
    lines = [
        f"three.js {version} — vendored by scripts/vendor_three.py",
        "Re-verify with: python3 scripts/vendor_three.py --verify",
        "",
    ]
    for upstream, local in FILES:
        url = _CDN.format(version=version, path=upstream)
        print(f"fetching {url}")
        with urllib.request.urlopen(url, timeout=120) as response:
            if response.status != 200:
                print(f"  ERROR: HTTP {response.status}", file=sys.stderr)
                return 1
            data = response.read()
        dest = dest_root / local
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        digest = _sha256(data)
        print(f"  -> {dest.relative_to(_VENDOR_ROOT.parent.parent)} "
              f"({len(data)} bytes)")
        lines.append(f"{local}\n  url    {url}\n  sha256 {digest}\n  bytes  {len(data)}")

    (dest_root / "SOURCES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {dest_root / 'SOURCES.txt'}")
    return 0


def verify(version: str) -> int:
    """Re-hash the on-disk tree against SOURCES.txt. No network."""
    dest_root = _VENDOR_ROOT / version
    sources = dest_root / "SOURCES.txt"
    if not sources.is_file():
        print(f"ERROR: no {sources}", file=sys.stderr)
        return 1

    recorded: dict[str, str] = {}
    current = None
    for line in sources.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(" ") and line.endswith((".js", ".css")):
            current = line
        elif current and line.strip().startswith("sha256 "):
            recorded[current] = line.strip().split(None, 1)[1]
            current = None

    failed = False
    for _, local in FILES:
        path = dest_root / local
        if not path.is_file():
            print(f"MISSING  {local}", file=sys.stderr)
            failed = True
            continue
        actual = _sha256(path.read_bytes())
        expected = recorded.get(local)
        if expected is None:
            print(f"UNLISTED {local} (not in SOURCES.txt)", file=sys.stderr)
            failed = True
        elif actual != expected:
            print(f"MISMATCH {local}\n  expected {expected}\n  actual   {actual}",
                  file=sys.stderr)
            failed = True
        else:
            print(f"ok       {local}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=VERSION,
                        help=f"three.js npm version to vendor (default {VERSION})")
    parser.add_argument("--verify", action="store_true",
                        help="re-hash the on-disk tree instead of downloading")
    args = parser.parse_args()
    return verify(args.version) if args.verify else fetch(args.version)


if __name__ == "__main__":
    sys.exit(main())
