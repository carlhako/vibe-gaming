#!/usr/bin/env python3
"""scripts/backfill_engine_meta.py — mark pre-existing three.js games as 3D.

Six games in this arcade were generated before the engine option existed, and
reach three.js through a hand-written `<script type="importmap">` pointing at
jsDelivr/unpkg. Their meta.json has no "engine" field, so everything downstream
treats them as 2D games — and `safety.scan()` now rejects an import map in a
game with no engine, which is exactly the hole those six went through.

Left alone, they keep *playing* fine (nothing scans on serve) but can never be
enhanced again: the scan fails, and the only fix the message allows is deleting
the import map, which breaks the game. That is the unsatisfiable-gate failure
mode docs/multifile-agent/agent-contracts.md exists to prevent.

Marking them engine=three fixes that, and their next enhance normalizes them
onto the vendored, self-hosted runtime — no external link left. It does move
them from three r157/r160 to r185; any API drift surfaces as a smoke-test
failure inside that same enhance, where the retry loop can fix it, rather than
silently.

Serving is untouched: normalize() runs during generation/enhance/build, never
on the way out, so this script does not change a single byte any player sees
today.

    python3 scripts/backfill_engine_meta.py --dry-run
    python3 scripts/backfill_engine_meta.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engines  # noqa: E402

_GAMES_DIR = Path(__file__).resolve().parent.parent / "games"
_IMPORTMAP_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']importmap["\'][^>]*>(.*?)</script\s*>',
    re.IGNORECASE | re.DOTALL,
)
_EXTERNAL_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*["\'](https?://[^"\']+)', re.IGNORECASE)


def candidates():
    """Games with a hand-written import map and no engine recorded."""
    for index in sorted(_GAMES_DIR.glob("*/index.html")):
        game_dir = index.parent
        meta_path = game_dir / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("engine"):
            continue
        html = index.read_text(encoding="utf-8", errors="replace")
        match = _IMPORTMAP_RE.search(html)
        if not match:
            continue
        yield game_dir, meta_path, meta, match.group(1), _EXTERNAL_SCRIPT_RE.findall(html)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed = skipped = 0
    for game_dir, meta_path, meta, imports, external in candidates():
        # A game that also pulls a non-three library from a CDN cannot become a
        # 3D game: only three.js is vendored, and a 3D game may not load an
        # external script. Report it rather than half-converting it.
        if external:
            print(f"SKIP  {game_dir.name}\n"
                  f"      also loads {', '.join(external)}, which is not vendored")
            skipped += 1
            continue
        if "three" not in imports:
            print(f"SKIP  {game_dir.name}: import map does not mention three")
            skipped += 1
            continue

        meta["engine"] = engines.ENGINE_THREE
        meta["engine_version"] = engines.DEFAULT_THREE_VERSION
        if args.dry_run:
            print(f"WOULD {game_dir.name} -> engine=three "
                  f"{engines.DEFAULT_THREE_VERSION}")
        else:
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            print(f"OK    {game_dir.name} -> engine=three "
                  f"{engines.DEFAULT_THREE_VERSION}")
        changed += 1

    print(f"\n{changed} game(s) {'would be ' if args.dry_run else ''}marked, "
          f"{skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
