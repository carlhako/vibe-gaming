# Sprint 2 — ReAct Agent Core (headless)

See [00-overview.md](00-overview.md). Depends on
[Sprint 1](01-multifile-build.md)'s `builder.py` and the build→scan→smoke
helper. This sprint builds the editing agent but ships it **headless** —
verified via DB/logs, no UI yet (that's Sprints 3–4).

## Goals

1. A ReAct (reason → act → observe) loop that edits a multi-file game by
   targeting only the files a change touches, so no single model response
   ever contains (or reads) the whole game.
2. Build → `safety.scan()` → `smoke_test.py` runs as the agent's
   verification step, with failures fed back as observations to fix.
3. Wired into `job_runner` for `kind='enhance'` on multi-file games;
   single-file games keep using `run_generation_attempts()`.

## Part A: `agent.py` (new file) — the tool loop

Reuses `ai_client.ask_with_tools()` (the same multi-turn function-calling
entry point `run_generation_attempts` already uses). Tools exposed to the
model:

- `read_map()` → `game.md` contents.
- `list_files()` → each `src/` file with its byte size (so the model can
  budget what to pull).
- `read_file(path)` → contents of one `src/` file.
- `write_file(path, contents)` → replace one whole module (create if new).
  **Ceiling guard:** reject a write whose contents exceed a configured
  `max_module_bytes` (default derived from `ai_client.MAX_OUTPUT_TOKENS`);
  the rejection tells the model to split the module. This is where the
  ceiling problem is finally structural — no write can be whole-game-sized.
- `finish(summary)` → the model declares it's done; triggers the
  verification pass.

Loop shape (mirrors `run_generation_attempts`'s conversation model — the
caller owns the message list, appends tool results between calls):

```
system prompt (task + rules + game.md already NOT inlined — model calls read_map)
loop up to max_steps:
    resp = ai.ask_with_tools(messages, tools=AGENT_TOOLS, tool_choice="auto", ...)
    for each tool_call: execute, append {"role":"tool", ...} observation
    if finish() called: run verification (Part B); break or continue on failure
    guard: step budget, no-op detection (repeated identical reads)
```

## Part B: verification as an observation

- On `finish()`: build `src/` → write `index.html` → `safety.scan()` →
  `smoke_test.run_smoke_test()`.
- **Pass** → done; register the forked game (same `db.register_web_game`
  bookkeeping as `enhance_game` today, `parent_game_id`/`root_game_id`
  preserved) and run `run_moderation_pass()`.
- **Fail** → feed the concrete failure back as a `finish`-tool observation
  (`"REJECTED: smoke test failed: <detail> — fix and finish again"`), same
  spirit as the current `reject()`. The agent edits and re-finishes, up to a
  verification-retry budget.
- A build error (missing ref, oversized module, escape) is fed back the same
  way — the agent never sees a raw traceback, only an actionable message.

## Part C: fork/write semantics

- Enhancing a multi-file game **forks** exactly as today: a brand-new
  `games/<slug>/` is written (new `game_id`/slug, `parent_game_id` = source,
  `root_game_id` = source's root). The agent's edits apply to a **copy** of
  the source `src/` tree staged in the new directory; the source is never
  touched. A failed job deletes the new directory (same rollback path as
  `run_generation_attempts`).
- `game.md` is a writable file: if the agent changes structure (adds/splits
  a module) it must update `game.md` via `write_file("game.md", …)` so the
  map stays accurate for the next enhancement. The system prompt requires
  this.

## Part D: context management

- Prune superseded content: once the agent has `write_file`'d a module, drop
  the earlier `read_file` observation for that same path from the running
  message list (keep only the latest known contents) so a multi-step edit
  doesn't accumulate stale copies. Keep `game.md` and the task.
- Track cumulative input/output tokens across steps (sum of per-call usage)
  for the result dict and audit, same fields `run_generation_attempts`
  returns.
- Respect `ai_client.MAX_OUTPUT_TOKENS` per call (already pinned) and the
  thinking-mode/`tool_choice` downgrade already handled in `ai_client`.

## Part E: job_runner integration

- `job_runner` dispatches a `kind='enhance'` job to the agent when the
  source game is `format: "multi-file"`, else to `enhance_game()`
  (single-file, unchanged). `kind='create'` stays on `generate_game()` for
  now (new games are born single-file; they become multi-file only via the
  Sprint 5 explode/enhance path).
- The agent honors `db.is_ai_generation_enabled()` (via `ai_client`) — a
  disabled switch aborts before any write, same as today.

## Part F: tests

`tests/test_agent.py`, mocking `ai.ask_with_tools` with a scripted sequence
of tool calls (as `test_generation_loop.py` already does):

- A scripted run that reads the map, reads one module, writes it, and
  finishes → produces a built, forked game; only the targeted module
  changed, others byte-identical to source.
- `write_file` over `max_module_bytes` → rejected with the split message;
  the agent's next (smaller) write succeeds.
- Smoke-test failure on first `finish` → failure observation fed back → a
  second `finish` after an edit passes.
- Structure change requires a `game.md` update (assert the prompt/loop
  surfaces the rule; assert a run that edits `game.md` persists it).
- Fork linkage: `parent_game_id`/`root_game_id` set correctly; source
  directory untouched; failed run leaves no partial directory.

## Acceptance criteria

- Enhancing the Sprint-1 multi-file fixture through the agent changes only
  the relevant module(s), rebuilds a valid `index.html`, passes scan+smoke,
  and registers a correctly-linked fork.
- No single `ask_with_tools` call in the run contains the whole game in its
  input or output; total tokens for a localized edit are materially below a
  whole-file resubmit of the same game (record the numbers in the PR).
- Single-file enhance and create paths are unchanged (`enhance_game` /
  `generate_game` untouched); their tests still pass.
- `pytest` green including `tests/test_agent.py`.
