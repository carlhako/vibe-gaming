# Sprint 4 — Live Chat UI + Wider Layout

See [00-overview.md](00-overview.md). Depends on
[Sprint 3](03-agent-event-stream.md)'s `agent_events` table and
`/api/jobs/<job_id>/events` endpoint. **This is the headline user-facing
feature**: a Claude-chat-style conversation on the Create/Enhance screens
that streams the agent's work, in a wider layout with a persistent
transcript.

## Goals

1. A wide, two-pane Create/Enhance screen: the prompt/controls on one side,
   a live conversation transcript on the other.
2. The transcript renders the agent's think → act → observe steps as chat
   messages as they arrive, ending in the finished game (Play link) or an
   error.
3. One shared experience for both `create` and `enhance` jobs.

## Part A: layout — widen and split

- Today `new_game.html`, `enhance.html`, and `status.html` are narrow,
  single-column forms. Introduce a shared wide two-pane layout (CSS grid):
  - **Left / input pane:** the existing prompt textarea + options (enhance's
    optional new-title field, etc.) and the submit button. After submit, it
    collapses to a compact summary (the prompt, the game being enhanced) so
    the transcript gets the room.
  - **Right / conversation pane:** the scrolling transcript of agent
    messages — the "chat between the platform and the AI."
- Widen the page container for these screens specifically (the arcade menu
  keeps its current width). Add a `--chat-max-width` and the two-pane grid
  to `static/style.css`; collapse to single-column stacked panes on narrow
  viewports (mobile) and support dark mode (the site already themes).

## Part B: the transcript component

- After submit, the page holds a `job_id` and polls
  `GET /api/jobs/<job_id>/events?since=<lastSeq>` (~1s), appending each new
  event as a chat message. Distinct styles per `role`:
  - `thought` → muted "thinking" bubble (collapsible; long reasoning is
    truncated with a "show more").
  - `tool_call` → an action chip: "📖 Reading `enemies.js`", "✏️ Writing
    `enemies.js` (8.1 KB)", built from `data`.
  - `tool_result` → a compact observation line under its action.
  - `build` / `smoke` → status rows (✓/✗ with detail on failure).
  - `assistant` → normal message text.
  - `final` → a success card with the **Play** link (to
    `/play/<result_slug>`) and the change summary; `error` → an error card.
- Autoscroll to newest while pinned to bottom; stop polling when status is
  terminal. Reloading the page mid-job replays the full transcript from
  `since=0` (it's all persisted) — no lost history.
- New `static/agent_chat.js` owns this; `static/status.js` stays for legacy
  single-file jobs (or is folded in behind a capability check — single-file
  jobs simply have no agent events and show the classic status view).

## Part C: routes/templates

- `POST /games/new` and `POST /games/<id>/enhance` already redirect to a
  status page keyed by `job_id`; point them at the new wide chat view
  (`templates/job.html` or extended `status.html`) rendering the two-pane
  layout with the `job_id` baked in.
- The view degrades gracefully: a single-file `create` job (no agent events)
  shows the existing queued→generating→success flow inside the same shell,
  so we don't fork the templates by format.
- Keep everything server-rendered + progressive: the transcript is additive;
  if JS is disabled the page still shows final status via a plain poll (as
  today).

## Part D: verification (browser preview)

Per the project's preview workflow — this sprint is visual, so verify in the
browser and share proof:

- Start the dev server, submit an enhance on the multi-file fixture, and
  confirm the transcript streams action chips and ends in a Play card.
- `read_console_messages` / `preview_logs` clean; `read_network_requests`
  shows the incremental `events?since=` polling advancing `seq`.
- `resize_window` to mobile → panes stack; dark mode styled.
- Screenshot the live conversation for the PR.

## Part E: tests

- Endpoint-shape and rendering-logic unit tests where practical (the event
  → message mapping is pure and testable without a browser).
- A small integration check that a job's transcript replays fully from
  `since=0` after a simulated reload.
- Browser-preview verification (above) stands in for end-to-end UI proof.

## Acceptance criteria

- Submitting a multi-file enhance shows a live, growing conversation of the
  agent's thoughts and file actions, ending in a working Play link — visibly
  Claude-chat-like.
- The screen is noticeably wider with a dedicated conversation pane;
  responsive and dark-mode correct.
- Reloading mid-job restores the full transcript; a finished job shows the
  complete history.
- Single-file create/enhance still works in the same shell with no
  regression.
- `pytest` green; browser-preview proof attached to the PR.
