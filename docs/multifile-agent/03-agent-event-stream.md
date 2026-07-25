# Sprint 3 — Agent Event Stream + Persistence

See [00-overview.md](00-overview.md). Depends on
[Sprint 2](02-react-agent-core.md)'s agent loop. This sprint makes the
agent's work **observable**: every think/act/observe step is persisted and
exposed through an incremental API. It's the backend the live chat UI
(Sprint 4) consumes — deliberately built before the UI so the UI has real
data to render.

## Goals

1. A durable, ordered record of the agent's conversation with the model
   (thoughts, tool calls, observations, build/smoke results, final outcome).
2. An incremental endpoint the browser polls to append new steps as they
   happen — the mechanism behind the "watch it work" feel.
3. Works under multiple gunicorn workers (DB-backed, no in-memory state),
   consistent with `job_runner`.

## Part A: `agent_events` table (new)

```sql
CREATE TABLE agent_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES generation_requests(job_id),
    seq        INTEGER NOT NULL,        -- monotonic per job, 1-based
    role       TEXT NOT NULL,           -- 'thought'|'tool_call'|'tool_result'
                                        -- |'assistant'|'build'|'smoke'|'final'|'error'
    content    TEXT,                    -- human-readable body (chat bubble text)
    data       TEXT,                    -- optional JSON: tool name/args summary,
                                        -- file path, sizes, outcome flags
    created_at TEXT NOT NULL,
    UNIQUE(job_id, seq)
);
CREATE INDEX idx_agent_events_job ON agent_events(job_id, seq);
```

- `seq` is assigned per job (max(seq)+1 within the job's connection) so the
  UI can request "everything after N" and render in order without relying on
  wall-clock time.
- `content` is what shows in a chat bubble; `data` carries structured extras
  (e.g. `{"tool":"read_file","path":"enemies.js","bytes":8123}`) for richer
  rendering (file chips, diffs later). Keep bodies concise — full file
  contents are **not** duplicated here (they're in `src/` / the built
  artifact); a `read_file`/`write_file` event stores the path + size, not
  the bytes. This mirrors the existing redaction discipline in
  `generation_attempts.raw_response`.

## Part B: agent emits events

- Extend `agent.py` to take an `emit(role, content, data=None)` callback
  (default: a no-op, so headless tests stay simple). `job_runner` passes an
  emitter that writes an `agent_events` row.
- Emit points: model reasoning summary (`thought`), each `tool_call`
  (name + arg summary), each `tool_result` (observation summary), any
  `assistant` prose, `build` result, `smoke` result, and a terminal `final`
  (success, with play URL) or `error`.
- Thinking-mode `reasoning_content`, when present, becomes the `thought`
  body (truncated to a sane length). If thinking is off, `thought` is the
  model's own short narration or is skipped.
- Emitting is best-effort and must **never** fail the job: wrap writes like
  `run_moderation_pass` does (swallow-and-log), so an audit write can't turn
  a successful edit into a failed one.

## Part C: incremental API

- `GET /api/jobs/<job_id>/events?since=<seq>` → JSON
  `{status, kind, events: [{seq, role, content, data, created_at}, …],
  result: {slug, title, url} | null}`.
  - Returns only events with `seq > since` (default `since=0` → all).
  - Includes the `generation_requests.status` so the poller knows when the
    job is terminal (`success`/`failed`) and can stop.
  - 404 on unknown `job_id`; validate `job_id` against the existing
    `_GAME_ID_RE`.
- This generalizes today's `GET /api/status/<job_id>`; keep that endpoint
  working (single-file jobs have no agent events, so `events: []` and the
  existing status page still functions).

## Part D: tests

`tests/test_agent_events.py`:

- A scripted agent run (reusing Sprint 2's mock) writes ordered
  `agent_events` with contiguous `seq` starting at 1.
- `?since=N` returns only later events, in order.
- `read_file`/`write_file` events store path+size in `data`, never the file
  bytes in `content`.
- An emitter that raises does not fail the job (best-effort guarantee).
- Endpoint reports terminal status and the result slug/title/url on success.

## Acceptance criteria

- Running a multi-file enhance produces a readable, ordered event trail in
  `agent_events` covering the whole think→act→observe→verify→finish arc.
- `GET /api/jobs/<job_id>/events?since=<seq>` returns strictly-new events in
  order plus terminal status, and 404s on unknown jobs.
- No file contents are duplicated into `agent_events`.
- Best-effort emission proven: a failing emitter leaves the job outcome
  unchanged.
- `pytest` green including `tests/test_agent_events.py`.
