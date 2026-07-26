"""Sprint 5 of docs/multifile-agent/ (05-migration-and-pilot.md): the
explode pass (Part A) and the dual-format enhance policy (Part B). Mocks
ai_client.ask_with_tools with scripted tool-call sequences and
smoke_test.run_smoke_test, same technique as tests/test_agent.py — no
network or browser needed."""

import copy
import json
import shutil
from pathlib import Path
from unittest import mock

import agent
import ai_client as ai
import builder
import db
import game_enhancer as ge

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "enhanceaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                          "max_attempts": 3, "smoke_test_timeout_seconds": 5},
    "multifile_agent": {
        "model": "", "effort": "high", "timeout_seconds": 5,
        "max_steps": 20, "max_verification_retries": 3,
        "max_module_bytes": 100_000,
    },
}

SOURCE_GAME_ID = "1" * 32
MULTIFILE_SOURCE_GAME_ID = "2" * 32

SPLIT_INDEX_HTML = (
    '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
    '</head><body><div id="count">0</div><button id="btn">Click</button>'
    '<script src="core.js"></script></body></html>'
)
SPLIT_STYLE_CSS = "body { background: #111; color: #eee; }\n"
SPLIT_CORE_JS = (
    '(function () {\n'
    '  var count = 0;\n'
    '  var countEl = document.getElementById("count");\n'
    '  document.getElementById("btn").addEventListener("click", function () {\n'
    '    count += 1;\n'
    '    countEl.textContent = String(count);\n'
    '  });\n'
    '})();\n'
)
SPLIT_GAME_MD = (
    "# Old School Arcade\n\nA tiny click counter.\n\n"
    "| file | purpose |\n| --- | --- |\n"
    "| src/index.html | shell |\n| src/style.css | styling |\n| src/core.js | logic |\n"
)


# The single-file source is deliberately the SAME program the scripted
# split re-emits, just inline: explode's declaration-parity gate rejects a
# split that loses any name the original declared, so a fixture whose
# "split" quietly renames things would (correctly) fail verification.
SINGLE_FILE_HTML = (
    '<!doctype html><html><head><style>' + SPLIT_STYLE_CSS + '</style></head>'
    '<body><div id="count">0</div><button id="btn">Click</button>'
    '<script>' + SPLIT_CORE_JS + '</script></body></html>'
)


def _setup_single_file_source(games_dir, html=None, title="Old School Arcade",
                               game_id=SOURCE_GAME_ID) -> dict:
    slug = f"old-school-arcade-{game_id[:4]}"
    game_dir = games_dir / slug
    game_dir.mkdir(parents=True)
    html = html if html is not None else SINGLE_FILE_HTML
    (game_dir / "index.html").write_text(html, encoding="utf-8")
    (game_dir / "meta.json").write_text(json.dumps({
        "game_id": game_id, "title": title, "description": "d",
        "requested_by": "web:a", "created_at": db.now_iso(), "version": 1,
        "prompt": "d", "format": "single-file",
    }), encoding="utf-8")
    db.register_web_game(
        game_id=game_id, slug=slug, title=title, description="d",
        requested_by="web:a", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=game_id,
    )
    return db.get_web_game(game_id)


def _setup_multi_file_source(games_dir) -> dict:
    """Copy the Sprint 1 fixture into games_dir as a registered, multi-file
    game — same fixture tests/test_agent.py uses, duplicated locally per
    this repo's convention of not importing helpers across test files."""
    slug = "click-counter-src"
    shutil.copytree(FIXTURE_DIR, games_dir / slug)
    db.register_web_game(
        game_id=MULTIFILE_SOURCE_GAME_ID, slug=slug, title="Click Counter",
        description="Press the button, watch the number climb.",
        requested_by="web:t", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=MULTIFILE_SOURCE_GAME_ID,
    )
    return db.get_web_game(MULTIFILE_SOURCE_GAME_ID)


def _tool_call(name, args, call_id):
    arguments = json.dumps(args)
    raw = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
    return raw, ai.ToolCall(id=call_id, name=name, arguments=arguments)


def _turn(calls, tokens=(5, 5)):
    raws, tool_calls = [], []
    for i, (name, args) in enumerate(calls):
        raw, tc = _tool_call(name, args, f"call_{i}_{name}")
        raws.append(raw)
        tool_calls.append(tc)
    message = {"role": "assistant", "content": None, "tool_calls": raws}
    return ai.ToolAskResult(
        message=message, tool_calls=tool_calls, text="",
        input_tokens=tokens[0], output_tokens=tokens[1],
        model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message}],
                      "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}},
    )


EXPLODE_RESPONSES = [
    _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
    _turn([("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS})]),
    _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})]),
    _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
    _turn([("finish", {"summary": "split into modules"})]),
]


# ---------------------------------------------------------------------------
# Part A: explode_game()
# ---------------------------------------------------------------------------

def test_explode_produces_multifile_fork_with_lineage_to_source(isolated_db, games_dir):
    _setup_single_file_source(games_dir)

    with mock.patch.object(ai, "ask_with_tools", side_effect=EXPLODE_RESPONSES), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert result["success"], result["error"]
    assert result["parent_game_id"] == SOURCE_GAME_ID
    assert result["root_game_id"] == SOURCE_GAME_ID
    assert result["title"] == "Old School Arcade"

    fork_dir = games_dir / result["slug"]
    assert builder.is_multi_file(fork_dir)
    built_html = fork_dir.joinpath("index.html").read_text(encoding="utf-8")
    assert "background: #111" in built_html, "style.css must be inlined into the build"
    assert "count += 1" in built_html, "core.js must be inlined into the build"

    row = db.get_web_game(result["game_id"])
    assert row["parent_game_id"] == SOURCE_GAME_ID
    assert row["root_game_id"] == SOURCE_GAME_ID
    assert row["version"] == 2, "fork's version must be one more than its source's"

    fork_meta = json.loads((fork_dir / "meta.json").read_text(encoding="utf-8"))
    assert fork_meta["version"] == 2

    # Source untouched.
    source_dir = games_dir / db.get_web_game(SOURCE_GAME_ID)["slug"]
    assert not builder.is_multi_file(source_dir)


def test_explode_rejects_an_already_multifile_source(isolated_db, games_dir):
    _setup_multi_file_source(games_dir)
    result = agent.explode_game(MULTIFILE_SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)
    assert not result["success"]
    assert "already multi-file" in result["error"]


def test_explode_fails_cleanly_and_rolls_back_on_persistent_failure(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    before = {p.name for p in games_dir.iterdir()}

    bad_cfg = copy.deepcopy(CONFIG)
    bad_cfg["multifile_agent"]["max_verification_retries"] = 1
    responses = [
        _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
        _turn([("finish", {"summary": "done"})]),
    ]
    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(False, "console error: boom")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", bad_cfg, games_dir=games_dir)

    assert not result["success"]
    after = {p.name for p in games_dir.iterdir()}
    assert before == after, "a failed explode must not leave any new directory behind"


def test_explode_announce_completion_false_emits_note_not_final(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    job_id = "9" * 32
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="irrelevant", requested_by="web:t",
        source_game_id=SOURCE_GAME_ID,
    )

    with mock.patch.object(ai, "ask_with_tools", side_effect=EXPLODE_RESPONSES), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir, job_id=job_id,
            announce_completion=False,
        )

    assert result["success"], result["error"]
    events = db.get_agent_events(job_id)
    roles = [e["role"] for e in events]
    assert "final" not in roles
    assert "assistant" in roles
    assert any("multi-file format" in (e["content"] or "") for e in events if e["role"] == "assistant")


# ---------------------------------------------------------------------------
# Sprint 6 step 2: explode must SPLIT, not just reformat. A real run produced
# one 151KB core.js for a 159KB game -- passing every gate while leaving a
# later edit exactly as expensive as before the split, which is the whole
# cost this initiative exists to remove.
# ---------------------------------------------------------------------------

def test_explode_enforces_its_own_tighter_module_ceiling(isolated_db, games_dir):
    """The explode pass runs under explode_max_module_bytes, well below the
    ordinary max_module_bytes -- a module that would sail through a normal
    edit is rejected here, with the existing "split it" guidance."""
    _setup_single_file_source(games_dir)
    cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(
            CONFIG["multifile_agent"],
            max_module_bytes=100_000,      # an ordinary edit would allow this...
            explode_max_module_bytes=500,  # ...but explode must not.
        ),
    }
    oversized = "// " + "x" * 2000
    responses = [
        _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
        _turn([("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS})]),
        _turn([("write_file", {"path": "everything.js", "contents": oversized})]),
        _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})]),
        _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
        _turn([("finish", {"summary": "split into modules"})]),
    ]
    seen = []

    def scripted(messages, **_kwargs):
        seen.append(copy.deepcopy(messages))
        return responses[len(seen) - 1]

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", cfg, games_dir=games_dir)

    assert result["success"], result["error"]

    # The oversized module was rejected and never written...
    assert not (games_dir / result["slug"] / "src" / "everything.js").exists()
    # ...and the rejection told the model to split rather than shrink.
    notes = " ".join(
        m["content"] for m in seen[-1]
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    )
    assert "REJECTED" in notes
    assert "500-byte" in notes
    assert "Split this module" in notes


def test_explode_prompt_demands_several_modules_not_one(isolated_db, games_dir):
    """The prompt is the primary lever here; the ceiling is only a backstop.
    The previous wording listed exactly one JS module by name
    (write_file("core.js", ...)) as its worked example, and a real run
    reproduced precisely that four-file shape with all logic in core.js --
    the same imitate-the-example failure as the stub-write bug."""
    source_html = "<html>" + "y" * 150_000 + "</html>"
    prompt = agent._build_explode_system_prompt("Big Game", source_html, 60_000)

    # States the enforced ceiling and a plural, size-derived module target.
    assert "60,000 bytes" in prompt
    target = agent._explode_target_module_count(len(source_html))
    assert target >= 5
    assert f"{target} " in prompt
    # Names several distinct modules rather than anchoring on a single one.
    for name in ("entities.js", "render.js", "input.js"):
        assert name in prompt
    assert "DEFEATS THE ENTIRE PURPOSE" in prompt

    # Small sources still get a sane floor rather than 0 or 1 modules.
    assert agent._explode_target_module_count(1_000) == 3


# ---------------------------------------------------------------------------
# Part B: enhance_game_auto_format()
# ---------------------------------------------------------------------------

def test_auto_format_routes_multifile_source_directly(isolated_db, games_dir):
    _setup_multi_file_source(games_dir)
    responses = [_turn([("finish", {"summary": "no changes"})])]

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            MULTIFILE_SOURCE_GAME_ID, "make it better", "web:t", CONFIG,
            games_dir=games_dir,
        )

    assert result["success"], result["error"]
    assert result["parent_game_id"] == MULTIFILE_SOURCE_GAME_ID


def test_auto_format_falls_back_to_legacy_for_small_singlefile_source(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    args = json.dumps({
        "title": "ignored", "description": "d",
        "html": "<!doctype html><html><body>v2</body></html>", "notes": "",
    })
    raw = {"id": "c1", "type": "function", "function": {"name": "submit_game", "arguments": args}}
    message = {"role": "assistant", "content": None, "tool_calls": [raw]}
    submit_response = ai.ToolAskResult(
        message=message, tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message, "finish_reason": "tool_calls"}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )

    with mock.patch.object(ai, "ask_with_tools", return_value=submit_response), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "polish it", "web:t", CONFIG, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    fork_dir = games_dir / result["slug"]
    assert not builder.is_multi_file(fork_dir), "small sources must stay on the legacy path"


def test_auto_format_explodes_then_enhances_large_singlefile_source(isolated_db, games_dir):
    large_html = SINGLE_FILE_HTML.replace(
        "</body>",
        ("<!-- padding " + "x" * 200 + " -->") * (ge.LARGE_SOURCE_BYTES // 200 + 1)
        + "</body>")
    assert len(large_html.encode("utf-8")) >= ge.LARGE_SOURCE_BYTES
    _setup_single_file_source(games_dir, html=large_html)

    enhance_responses = [
        _turn([("read_map", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS.replace("count += 1", "count += 2")}),
               ("finish", {"summary": "double the increment"})]),
    ]
    responses = EXPLODE_RESPONSES + enhance_responses

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "double the increment", "web:t", CONFIG, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    intermediate_id = result["parent_game_id"]
    assert intermediate_id != SOURCE_GAME_ID, "parent must be the exploded fork, not the original"
    assert result["root_game_id"] == SOURCE_GAME_ID, "root must still trace to the original single-file source"

    intermediate_row = db.get_web_game(intermediate_id)
    assert intermediate_row is not None
    assert bool(intermediate_row["hidden"]) is True, "the auto-explode step must not clutter the sidebar"
    assert intermediate_row["parent_game_id"] == SOURCE_GAME_ID

    fork_dir = games_dir / result["slug"]
    assert builder.is_multi_file(fork_dir)


def test_auto_format_falls_back_to_legacy_when_explode_fails(isolated_db, games_dir):
    large_html = (
        "<!doctype html><html><body>v1"
        + ("x" * ge.LARGE_SOURCE_BYTES) + "</body></html>"
    )
    _setup_single_file_source(games_dir, html=large_html)
    before = {p.name for p in games_dir.iterdir()}

    bad_cfg = copy.deepcopy(CONFIG)
    bad_cfg["multifile_agent"]["max_verification_retries"] = 1

    args = json.dumps({
        "title": "ignored", "description": "d",
        "html": "<!doctype html><html><body>v2</body></html>", "notes": "",
    })
    raw = {"id": "c1", "type": "function", "function": {"name": "submit_game", "arguments": args}}
    message = {"role": "assistant", "content": None, "tool_calls": [raw]}
    submit_response = ai.ToolAskResult(
        message=message, tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message, "finish_reason": "tool_calls"}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )

    # First (explode) conversation always fails the safety scan (eval() is
    # banned — deterministic, unlike smoke_test, so the mocked smoke test
    # can stay a plain success and not also sink the legacy fallback
    # below); once enhance_game_auto_format falls back to the legacy path,
    # a fresh conversation starts and gets the submit_game response instead.
    explode_fail_responses = [
        _turn([("write_file", {"path": "index.html", "contents": "<html><script>eval('x')</script></html>"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    call_log = []

    def scripted(messages, **_kwargs):
        call_log.append(messages)
        if len(call_log) <= len(explode_fail_responses):
            return explode_fail_responses[len(call_log) - 1]
        return submit_response

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "polish it", "web:t", bad_cfg, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    fork_dir = games_dir / result["slug"]
    assert not builder.is_multi_file(fork_dir), "fallback result must be the legacy single-file path"

    after = {p.name for p in games_dir.iterdir()}
    # Only the source + the one successful legacy fork should remain -- the
    # failed explode attempt's half-written directory must be cleaned up.
    assert len(after - before) == 1


# ---------------------------------------------------------------------------
# The declaration-parity gate: build -> safety scan -> smoke test structurally
# cannot catch a split that silently drops code, so explode adds its own.
# ---------------------------------------------------------------------------

def test_explode_rejects_a_split_that_drops_a_declaration(isolated_db, games_dir):
    """The Darkhold pilot's real failure, in miniature. The original wraps
    everything in one IIFE and declares `screenX`; the split moves that into
    global scope, where it collides with the read-only window.screenX
    built-in, and the model resolves the collision by deleting the function
    while leaving its call sites. Nothing errors on load — `screenX` still
    resolves, to the built-in number — so a page-load smoke test is green and
    the game breaks the moment that code path runs."""
    source_js = (
        '(function () {\n'
        '  var camera = { x: 0 };\n'
        '  function screenX(wx) { return wx - camera.x; }\n'
        '  function draw() { return screenX(10); }\n'
        '  draw();\n'
        '})();\n'
    )
    _setup_single_file_source(games_dir, html=(
        '<!doctype html><html><body><canvas id="c"></canvas>'
        '<script>' + source_js + '</script></body></html>'))

    # The split keeps every call site but loses the declaration itself.
    lossy_core_js = (
        'var camera = { x: 0 };\n'
        'function draw() { return screenX(10); }\n'
        'draw();\n'
    )
    responses = [
        _turn([("write_file", {"path": "index.html", "contents":
                '<!doctype html><html><body><canvas id="c"></canvas>'
                '<script src="core.js"></script></body></html>'})]),
        _turn([("write_file", {"path": "core.js", "contents": lossy_core_js})]),
        _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
        _turn([("finish", {"summary": "split into modules"})]),
        _turn([("finish", {"summary": "still split"})]),
    ]

    bad_cfg = copy.deepcopy(CONFIG)
    bad_cfg["multifile_agent"]["max_verification_retries"] = 1
    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SOURCE_GAME_ID, "web:t", bad_cfg, games_dir=games_dir)

    # The standard gate passed (mocked smoke test succeeds, safety scan is
    # clean) — only the parity check stands between this and a broken game.
    assert not result["success"]
    assert "screenX" in result["error"]
    # The message has to name the surviving call sites, because "it is still
    # referenced" is the whole difference between this and a legal rename.
    assert "reference(s) left" in result["error"]


def test_explode_declaration_check_ignores_pure_reorganisation(isolated_db, games_dir):
    """The gate must not fire on a legitimate split: same names, different
    files, IIFE removed, whitespace and ordering changed."""
    source = ('<!doctype html><html><body><script>'
              '(function () { var a = 1; function go() { return a; } go(); })();'
              '</script></body></html>')
    built = ('<!doctype html><html><body><script>var a = 1;</script>'
             '<script>\n  function go() {\n    return a;\n  }\n  go();\n</script>'
             '</body></html>')
    assert agent._missing_declarations(source, built) == []


def test_explode_declaration_check_names_every_missing_symbol(isolated_db, games_dir):
    source = ('<html><script>function alpha(){} const beta = 2; let gamma = 3;'
              '</script></html>')
    built = '<html><script>function alpha(){}</script></html>'
    assert agent._missing_declarations(source, built) == ["beta", "gamma"]


def test_explode_succeeds_end_to_end_when_the_model_renames_a_collision(
        isolated_db, games_dir):
    """The production failure, whole-pipeline. Both Sorcerer With A Minigun
    explodes (2026-07-26) died here: the model renamed `screenX` to
    `toScreenX` at the declaration and every call site — the remedy the gate's
    own message prescribes — and the gate failed it anyway, then repeated the
    demand until the attempts ran out."""
    source_js = (
        '(function () {\n'
        '  var camera = { x: 0, y: 0 };\n'
        '  function screenX(wx) { return wx - camera.x; }\n'
        '  function screenY(wy) { return wy - camera.y; }\n'
        '  function draw() { return screenX(10) + screenY(20); }\n'
        '  draw();\n'
        '})();\n'
    )
    _setup_single_file_source(games_dir, html=(
        '<!doctype html><html><body><canvas id="c"></canvas>'
        '<script>' + source_js + '</script></body></html>'))

    responses = [
        _turn([("write_file", {"path": "index.html", "contents":
                '<!doctype html><html><body><canvas id="c"></canvas>'
                '<script src="world.js"></script>'
                '<script src="render.js"></script></body></html>'})]),
        _turn([("write_file", {"path": "world.js", "contents":
                'var camera = { x: 0, y: 0 };\n'
                'function toScreenX(wx) { return wx - camera.x; }\n'
                'function toScreenY(wy) { return wy - camera.y; }\n'})]),
        _turn([("write_file", {"path": "render.js", "contents":
                'function draw() { return toScreenX(10) + toScreenY(20); }\n'
                'draw();\n'})]),
        _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
        _turn([("finish", {"summary": "split, renaming the window collisions"})]),
    ]

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert result["success"], result["error"]
    built = (games_dir / result["slug"] / "index.html").read_text(encoding="utf-8")
    assert "toScreenX" in built and "toScreenY" in built
    # The rename has to be complete: no bare `screenX` left to bind to the
    # read-only Window built-in.
    assert agent._reference_count("screenX", built) == 0


def test_explode_declaration_check_accepts_a_consistent_rename(isolated_db, games_dir):
    """The gate must pass the exact fix its own message asks for. Dropping the
    IIFE puts `screenX` in global scope where it collides with the read-only
    Window built-in, so the prompt tells the model to rename it at the
    declaration and every call site — which necessarily removes `screenX`
    from the declaration set. Failing that renamed split is what made two
    real Sorcerer With A Minigun explodes unsatisfiable: every remaining
    attempt spent re-doing the rename it had already done correctly."""
    source = ('<html><script>(function () {'
              'function screenX(wx) { return wx - 1; }'
              'function draw() { return screenX(10); }'
              '})();</script></html>')
    built = ('<html><script>function toScreenX(wx) { return wx - 1; }</script>'
             '<script>function draw() { return toScreenX(10); }</script></html>')

    assert agent._missing_declarations(source, built) == ["screenX"]
    assert agent._declaration_parity(source, built) == ([], ["screenX"])
    assert agent._explode_declaration_check(source)(games_dir, built) is None


def test_explode_declaration_check_still_fails_a_half_done_rename(
        isolated_db, games_dir):
    """A rename that missed a call site is the delete case wearing a
    disguise: `screenX` there still binds to the built-in number."""
    source = ('<html><script>(function () {'
              'function screenX(wx) { return wx - 1; }'
              'function draw() { return screenX(10); }'
              'function hud() { return screenX(20); }'
              '})();</script></html>')
    built = ('<html><script>function toScreenX(wx) { return wx - 1; }'
             'function draw() { return toScreenX(10); }'
             'function hud() { return screenX(20); }</script></html>')

    error = agent._explode_declaration_check(source)(games_dir, built)
    assert error and "screenX (1 reference(s) left)" in error


def test_explode_declaration_check_fails_code_dropped_with_its_call_sites(
        isolated_db, games_dir):
    """The other real defect: a module shrunk to fit the ceiling, taking a
    subsystem and its callers with it. Nothing is left referencing the names,
    so only the absence of any replacement declaration distinguishes it from
    a rename."""
    source = ('<html><script>(function () {'
              'function drawPlayer(){} function drawEnemy(){}'
              'function frame(){ drawPlayer(); drawEnemy(); }'
              '})();</script></html>')
    built = '<html><script>function frame(){}</script></html>'

    error = agent._explode_declaration_check(source)(games_dir, built)
    assert error and "drawPlayer, drawEnemy" in error.replace(
        "drawEnemy, drawPlayer", "drawPlayer, drawEnemy")
    assert "no new declaration to replace them" in error


def test_reference_count_ignores_member_access(isolated_db, games_dir):
    """`camera.screenX` is a property, not a reference to the global that was
    renamed away — counting it would fail a legitimate rename."""
    html = ('<html><script>var camera = { screenX: 1 };'
            'function f(){ return camera.screenX; }</script></html>')
    assert agent._reference_count("screenX", html) == 0
    assert agent._reference_count("camera", html) == 2


def test_reference_count_ignores_strings_and_comments(isolated_db, games_dir):
    """A name surviving only in a log message or a commented-out line is not a
    call site, and reporting it as one sends the model looking for code that
    isn't there."""
    html = ('<html><script>function f(){ console.log("screenX broke"); '
            '/* screenX(1) */ }</script></html>')
    assert agent._reference_count("screenX", html) == 0


# ---------------------------------------------------------------------------
# Scope awareness. The parity gate polices names that become real globals when
# explode drops the original's IIFE — it must not police function locals, whose
# binding form a legitimate split is free to change.
# ---------------------------------------------------------------------------

def test_scan_scopes_separates_program_names_from_function_locals(
        isolated_db, games_dir):
    html = ('<html><script>(function () {'
            '  var game = { wave: 1 };'
            '  function fire(t) { let target = t; for (const it of [t]) { target = it; } return target; }'
            '  fire(null);'
            '})();</script></html>')
    scopes = agent._scan_scopes(html)
    # The IIFE body IS the program's top level: dropping the wrapper is what
    # turns these two into globals.
    assert scopes.top_level == {"game", "fire"}
    # ...and everything bound anywhere is still known, so a name reappearing
    # as a parameter can't be reported as an undeclared reference.
    assert {"target", "it", "t"} <= scopes.bound


def test_scan_scopes_ignores_braces_inside_strings_comments_and_regexes(
        isolated_db, games_dir):
    """The walk that decides "is this declaration inside a function?" counts
    braces, so an unmatched brace in a literal would shift every name after it
    into the wrong scope."""
    html = ('<html><script>(function () {'
            '  var label = "score: {";'
            '  var re = /[}{]/g;'
            '  // }\n'
            '  /* } */'
            '  var tail = 1;'
            '})();</script></html>')
    assert agent._scan_scopes(html).top_level == {"label", "re", "tail"}


def test_scan_scopes_does_not_treat_a_declaration_before_an_iife_as_a_wrapper(
        isolated_db, games_dir):
    """`function foo() { … }` followed by `(function () { … })();` reads, to a
    backwards scan from the closing brace, exactly like `}()` — an IIFE. A
    function declaration can never be one, so foo's locals stay local."""
    html = ('<html><script>function foo() { var innerOnly = 1; return innerOnly; }\n'
            '(function () { var wrapped = 2; })();</script></html>')
    scopes = agent._scan_scopes(html)
    assert scopes.top_level == {"foo", "wrapped"}
    assert "innerOnly" not in scopes.top_level


def test_scan_scopes_promotes_a_lone_onload_wrapper(isolated_db, games_dir):
    """A game written entirely inside one window.onload handler has no top
    level of its own. Excluding that body would leave the gate policing
    nothing at all — a silent loss of protection, which is worse than a false
    positive because nothing reports it."""
    html = ('<html><script>window.onload = function () {'
            '  var board = [];'
            '  function draw() { var px = 1; return px; }'
            '  draw();'
            '};</script></html>')
    scopes = agent._scan_scopes(html)
    assert scopes.top_level == {"board", "draw"}
    assert "px" not in scopes.top_level


def test_explode_declaration_check_ignores_locals_the_split_re_expressed(
        isolated_db, games_dir):
    """The production failure this scope awareness exists for: a Tower Maze
    Defense explode (2026-07-26) burned all three verification attempts and
    ~20 minutes on `ct`, `it` and `target` — all three function locals in the
    original. The split legitimately re-expressed them (a `let target` became
    an `applyDamage(target, …)` parameter, `for (const it of …)` became
    `.forEach(it => …)`), which read to a flat regex as "the original declared
    it, the split doesn't" with the surviving locals counted as broken call
    sites. No file the model could write would have satisfied that."""
    source = ('<html><script>(function () {\n'
              '  var towers = [];\n'
              '  function applyDamage(t, dmg) {\n'
              '    var chainTargets = [t], ct = t;\n'
              '    let target = t;\n'
              '    for (const it of towers) { target = it; }\n'
              '    return target && ct && chainTargets;\n'
              '  }\n'
              '  applyDamage(null, 1);\n'
              '})();</script></html>')
    built = ('<html><script>var towers = [];</script>'
             '<script>function applyDamage(target, dmg) {\n'
             '  var chain = [target];\n'
             '  towers.forEach(it => { target = it; });\n'
             '  return target && chain;\n'
             '}\n'
             'applyDamage(null, 1);</script></html>')

    assert agent._declaration_parity(source, built) == ([], [])
    assert agent._explode_declaration_check(source)(games_dir, built) is None


def test_explode_declaration_check_fails_a_function_copied_into_two_modules(
        isolated_db, games_dir):
    """The mirror image of a dropped declaration, from the same Tower Maze run:
    `gameOver` written into both combat.js and main.js. The later copy silently
    wins, so the built game can run a body the original never had — and every
    other gate is happy, since the name is declared and every call resolves."""
    source = ('<html><script>(function () {'
              'function gameOver() { return 1; }'
              'function frame() { return gameOver(); }'
              'frame();})();</script></html>')
    built = ('<html><script>function gameOver() { return 1; }</script>'
             '<script>function gameOver() { return 2; }'
             'function frame() { return gameOver(); }frame();</script></html>')

    error = agent._explode_declaration_check(source)(games_dir, built)
    assert error and "gameOver" in error
    assert "silently replaces" in error
    # One copy, in one module, is the fix — and it has to pass.
    fixed = ('<html><script>function gameOver() { return 1; }</script>'
             '<script>function frame() { return gameOver(); }frame();</script></html>')
    assert agent._explode_declaration_check(source)(games_dir, fixed) is None


def test_explode_succeeds_end_to_end_when_the_split_rebinds_a_local(
        isolated_db, games_dir):
    """The Tower Maze failure through the whole pipeline: a split that only
    ever re-expresses locals must reach a registered fork, not burn its
    attempts."""
    source_js = (
        '(function () {\n'
        '  var enemies = [];\n'
        '  function hit(e) { let target = e; for (const it of enemies) { target = it; } return target; }\n'
        '  hit(null);\n'
        '})();\n'
    )
    _setup_single_file_source(games_dir, html=(
        '<!doctype html><html><body><canvas id="c"></canvas>'
        '<script>' + source_js + '</script></body></html>'))

    responses = [
        _turn([("write_file", {"path": "index.html", "contents":
                '<!doctype html><html><body><canvas id="c"></canvas>'
                '<script src="enemies.js"></script>'
                '<script src="combat.js"></script></body></html>'})]),
        _turn([("write_file", {"path": "enemies.js", "contents": 'var enemies = [];\n'})]),
        _turn([("write_file", {"path": "combat.js", "contents":
                'function hit(target) { enemies.forEach(it => { target = it; }); return target; }\n'
                'hit(null);\n'})]),
        _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
        _turn([("finish", {"summary": "split into enemies + combat"})]),
    ]

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert result["success"], result["error"]


# ---------------------------------------------------------------------------
# The no-progress guard must not abort a run that has written everything and
# is doing a final read-through before finish().
# ---------------------------------------------------------------------------

def _reads(n):
    """n turns that only read — no write, no finish."""
    return [_turn([("read_file", {"path": "core.js"})]) for _ in range(n)]


def test_a_review_pass_before_finish_gets_nudged_not_killed(isolated_db, games_dir):
    """A real explode pilot wrote all 10 modules, spent its last turns
    re-reading them for consistency, and was aborted by the no-progress guard
    having never called finish — throwing away a complete split for being
    careful. One nudge should recover it."""
    _setup_single_file_source(games_dir)
    responses = (
        [_turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
         _turn([("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS})]),
         _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})]),
         _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})])]
        + _reads(agent._MAX_NO_PROGRESS_STEPS)      # the review pass
        + [_turn([("finish", {"summary": "split into modules"})])]
    )

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert result["success"], result["error"]
    assert builder.is_multi_file(games_dir / result["slug"])


def test_the_finish_nudge_is_spent_once_and_a_real_stall_still_aborts(
        isolated_db, games_dir):
    """The nudge must not turn a genuinely stuck run into an unbounded one:
    a second stall after the nudge still ends the run."""
    _setup_single_file_source(games_dir)
    responses = (
        [_turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})])]
        + _reads(agent._MAX_NO_PROGRESS_STEPS * 2 + 4)
    )

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert not result["success"]
    assert "no progress" in result["error"]


def test_a_run_that_never_wrote_anything_is_not_nudged(isolated_db, games_dir):
    """The nudge tells the model to call finish because its files are on
    disk. With nothing written that would be wrong, so the guard aborts at
    the usual threshold."""
    _setup_single_file_source(games_dir)
    responses = _reads(agent._MAX_NO_PROGRESS_STEPS + 3)

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert not result["success"]
    assert "no progress" in result["error"]


def test_a_run_running_out_of_steps_without_verifying_is_told_to_finish(
        isolated_db, games_dir):
    """A run that never calls finish ships nothing — every module written is
    discarded. A real pilot burned all 60 steps self-auditing its split and
    never verified, at 4.5M tokens. The no-progress guard cannot catch that:
    any successful write resets it, so a model alternating read/write never
    trips it. The step budget itself has to be watched."""
    _setup_single_file_source(games_dir)
    cfg = copy.deepcopy(CONFIG)
    cfg["multifile_agent"]["max_steps"] = 12          # nudge threshold: 5 left

    sent = []

    def scripted(messages, **_kwargs):
        sent.append([m for m in messages if m.get("role") == "user"])
        n = len(sent)
        if n == 1:
            return _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})])
        if n == 2:
            return _turn([("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS})])
        if n == 3:
            return _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})])
        if n == 4:
            return _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})])
        # Now alternate read/write forever: made_progress keeps resetting, so
        # only the budget watcher can break this.
        if n % 2 == 0:
            return _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})])
        return _turn([("read_file", {"path": "core.js"})])

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        agent.explode_game(SOURCE_GAME_ID, "web:t", cfg, games_dir=games_dir)

    warnings = [m for turn in sent for m in turn
                if "BUDGET WARNING" in (m.get("content") or "")]
    assert warnings, "the run should have been warned before its budget ran out"
    # Warned exactly once, however many turns it then took.
    assert len({m["content"] for m in warnings}) == 1
    assert "ships NOTHING" in warnings[0]["content"]


def test_the_budget_warning_is_not_sent_once_verification_has_run(
        isolated_db, games_dir):
    """The warning is about never verifying. A run that already called finish
    and is working through a rejection must not be hurried."""
    _setup_single_file_source(games_dir)
    cfg = copy.deepcopy(CONFIG)
    cfg["multifile_agent"]["max_steps"] = 12
    cfg["multifile_agent"]["max_verification_retries"] = 5

    sent = []

    def scripted(messages, **_kwargs):
        sent.append([m for m in messages if m.get("role") == "user"])
        n = len(sent)
        if n == 1:
            return _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})])
        if n == 2:
            return _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})])
        if n == 3:
            return _turn([("finish", {"summary": "done"})])   # fails: no game.md yet
        return _turn([("read_file", {"path": "core.js"})])

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(False, "boom")):
        agent.explode_game(SOURCE_GAME_ID, "web:t", cfg, games_dir=games_dir)

    assert not [m for turn in sent for m in turn
                if "BUDGET WARNING" in (m.get("content") or "")]


# ---------------------------------------------------------------------------
# The agent's own model default. config.yaml is gitignored, so a config-only
# default is invisible to every deployment — this has to live in code.
# ---------------------------------------------------------------------------

def _model_used_for_explode(games_dir, multifile_cfg):
    """Run one explode turn and report the model actually sent to DeepSeek."""
    seen = []

    def scripted(messages, **kwargs):
        seen.append(kwargs.get("model"))
        return _turn([("finish", {"summary": "done"})])

    cfg = copy.deepcopy(CONFIG)
    if multifile_cfg is None:
        cfg.pop("multifile_agent")
    else:
        cfg["multifile_agent"] = multifile_cfg
    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(False, "stop here")):
        agent.explode_game(SOURCE_GAME_ID, "web:t", cfg, games_dir=games_dir)
    return seen[0]


def test_agent_defaults_to_pro_with_no_config_block(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    assert _model_used_for_explode(games_dir, None) == agent.DEFAULT_AGENT_MODEL
    assert agent.DEFAULT_AGENT_MODEL == "deepseek-v4-pro"


def test_agent_defaults_to_pro_when_the_configured_model_is_blank(
        isolated_db, games_dir):
    """A blank model must land on the agent's default, not fall through to
    ai_client's app-wide flash default."""
    _setup_single_file_source(games_dir)
    used = _model_used_for_explode(
        games_dir, {"model": "", "effort": "high", "max_steps": 3})
    assert used == "deepseek-v4-pro"


def test_an_explicit_model_in_config_still_wins(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    used = _model_used_for_explode(
        games_dir, {"model": "deepseek-v4-flash", "effort": "high", "max_steps": 3})
    assert used == "deepseek-v4-flash"


def test_the_agent_default_does_not_leak_into_the_single_file_pipelines(
        isolated_db, games_dir):
    """newaiwebgame/enhanceaiwebgame have no evidence against flash and must
    keep resolving through ai_client's own default."""
    import ai_client
    assert ai_client.MODEL_DEFAULT == "deepseek-v4-flash"
    assert agent.DEFAULT_AGENT_MODEL != ai_client.MODEL_DEFAULT
