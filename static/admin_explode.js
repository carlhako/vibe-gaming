/*
 * Admin Games tab -> "Explode" button -> live progress dialog.
 *
 * Queues a kind='explode' job (POST /admin/games/<id>/explode) and then
 * follows it on the same /api/jobs/<job_id>/events feed the job status
 * page's chat pane polls. Deliberately does NOT share a renderer with
 * static/agent_chat.js: this is a denser, dialog-sized log that also
 * renders the per-LLM-call 'usage' events agent_chat.js ignores, and the
 * status page's transcript is the user-facing one we don't want to churn.
 */
(() => {
  const dialog = document.getElementById("explode-dialog");
  if (!dialog) return;

  const heading = document.getElementById("explode-heading");
  const intro = document.getElementById("explode-intro");
  const stateEl = document.getElementById("explode-state");
  const usageEl = document.getElementById("explode-usage");
  const log = document.getElementById("explode-log");
  const startBtn = document.getElementById("explode-start");
  const closeBtn = document.getElementById("explode-close");

  const adminToken = dialog.dataset.adminToken || "";
  const inputCostPerMillion = parseFloat(dialog.dataset.inputCostPerMillion) || 0;
  const outputCostPerMillion = parseFloat(dialog.dataset.outputCostPerMillion) || 0;

  const TOOL_ICON = {
    read_map: "📖",
    list_files: "📂",
    read_file: "📖",
    write_file: "✏️",
    finish: "🏁",
  };
  const TERMINAL_STATUSES = new Set(["success", "failed"]);
  const MAX_CONSECUTIVE_ERRORS = 10;

  let current = null; // { gameId, title, jobId, lastSeq, timer, errors }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function append(node) {
    const pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.appendChild(node);
    if (pinned) log.scrollTop = log.scrollHeight;
  }

  function cost(inputTokens, outputTokens) {
    return (inputTokens || 0) / 1e6 * inputCostPerMillion +
           (outputTokens || 0) / 1e6 * outputCostPerMillion;
  }

  function setUsage(data) {
    if (!data) {
      usageEl.textContent = "";
      return;
    }
    const total = data.tokens_used != null
      ? data.tokens_used
      : (data.input_tokens || 0) + (data.output_tokens || 0);
    const parts = [
      `${(data.input_tokens || 0).toLocaleString()} in`,
      `${(data.output_tokens || 0).toLocaleString()} out`,
      `${total.toLocaleString()} total`,
    ];
    const usd = cost(data.input_tokens, data.output_tokens);
    if (usd > 0) parts.push(`$${usd.toFixed(4)}`);
    usageEl.textContent = parts.join(" · ");
  }

  function renderEvent(event) {
    const data = event.data || {};
    switch (event.role) {
      case "thought": {
        const wrap = el("details", "chat-msg chat-msg-thought");
        wrap.appendChild(el("summary", null, "🤔 Thinking"));
        wrap.appendChild(el("pre", null, event.content || ""));
        return wrap;
      }
      case "assistant":
        return el("div", "chat-msg chat-msg-assistant", event.content || "");
      case "usage":
        // The running token/cost readout above is the headline; keep the
        // per-call line too so a run that stalls shows *where* it burned.
        setUsage(data);
        return el("div", "chat-msg chat-msg-usage", "📊 " + (event.content || ""));
      case "tool_call": {
        const icon = TOOL_ICON[data.tool] || "🔧";
        return el("div", "chat-msg chat-msg-tool_call",
                  `${icon} ${event.content || data.tool || ""}`);
      }
      case "tool_result": {
        let text = event.content || "";
        let cls = "chat-msg chat-msg-tool_result";
        if (data.tool === "write_file") {
          const ok = data.outcome === "ok";
          cls += ok ? "" : " is-rejected";
          text = (ok ? "✅ " : "❌ ") + text;
        } else if (text.startsWith("ERROR:")) {
          cls += " is-error";
          text = "❌ " + text;
        }
        return el("div", cls, text);
      }
      case "build": {
        const ok = data.outcome === "success";
        const suffix = data.attempt ? ` (attempt ${data.attempt})` : "";
        return el("div", `chat-msg chat-msg-build ${ok ? "is-success" : "is-fail"}`,
                  (ok ? "✅ " : "❌ ") + (event.content || "") + suffix);
      }
      case "final":
        return el("div", "chat-msg chat-msg-final", "🎉 " + (event.content || "Done."));
      case "error":
        return el("div", "chat-msg chat-msg-error", "⚠️ " + (event.content || "Failed."));
      default:
        return null;
    }
  }

  function stopPolling() {
    if (current && current.timer) {
      clearInterval(current.timer);
      current.timer = null;
    }
  }

  function finish(data) {
    stopPolling();
    const ok = data.status === "success";
    stateEl.textContent = ok ? "✅ success" : "❌ failed";
    stateEl.className = `explode-state ${ok ? "is-success" : "is-fail"}`;
    setUsage(data);

    // A replay from the History tab has no game to explode (current.gameId
    // is null) and may be an enhance job, so it gets neutral wording.
    const isExplodeRun = current && current.gameId;
    const summary = el("div", `chat-msg chat-msg-${ok ? "final" : "error"}`);
    if (ok && data.result) {
      summary.appendChild(el("div", null, isExplodeRun
        ? `Created multi-file fork "${data.result.title}". The single-file ` +
          `original is unchanged.`
        : `Result: "${data.result.title}".`));
      const link = document.createElement("a");
      link.className = "play-link chat-final-play-link";
      link.href = data.result.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = isExplodeRun ? "Play the exploded version →" : "Play →";
      summary.appendChild(link);
    } else if (!ok) {
      summary.appendChild(el("div", null, data.error || "The explode run failed."));
    }
    const detail = [];
    if (data.model) detail.push(data.effort ? `${data.model} (${data.effort})` : data.model);
    if (data.duration_seconds != null) detail.push(`${data.duration_seconds.toFixed(1)}s`);
    if (data.attempts) detail.push(`${data.attempts} verification attempt(s)`);
    if (detail.length) summary.appendChild(el("div", "form-hint", detail.join(" · ")));
    if (isExplodeRun) {
      summary.appendChild(el("div", "form-hint",
        "Full token accounting for this job is on the History tab. " +
        "Reload this page to refresh the Fmt column."));
    }
    append(summary);
  }

  async function poll() {
    if (!current || !current.jobId) return;
    try {
      const res = await fetch(
        `/api/jobs/${encodeURIComponent(current.jobId)}/events?since=${current.lastSeq}`);
      if (!res.ok) throw new Error(`events ${res.status}`);
      current.errors = 0;
      const data = await res.json();
      for (const event of data.events) {
        const node = renderEvent(event);
        if (node) append(node);
        current.lastSeq = Math.max(current.lastSeq, event.seq);
      }
      if (data.status === "generating" && stateEl.textContent !== "running…") {
        stateEl.textContent = "running…";
        stateEl.className = "explode-state is-running";
      }
      if (TERMINAL_STATUSES.has(data.status)) finish(data);
    } catch (err) {
      current.errors += 1;
      console.error("explode events poll failed", err);
      if (current.errors >= MAX_CONSECUTIVE_ERRORS) {
        stopPolling();
        stateEl.textContent = "lost contact with the server";
        stateEl.className = "explode-state is-fail";
      }
    }
  }

  function attach(jobId) {
    current.jobId = jobId;
    current.lastSeq = 0;
    current.errors = 0;
    startBtn.hidden = true;
    stateEl.textContent = "queued…";
    stateEl.className = "explode-state is-running";
    poll();
    current.timer = setInterval(poll, 1000);
  }

  async function start() {
    startBtn.disabled = true;
    stateEl.textContent = "queueing…";
    try {
      const res = await fetch(
        `/admin/games/${encodeURIComponent(current.gameId)}/explode` +
        `?token=${encodeURIComponent(adminToken)}`,
        { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 409 with a job_id means one is already in flight for this game —
        // follow that run rather than making the admin hunt for it.
        if (data.job_id) {
          append(el("div", "chat-msg chat-msg-assistant", data.error));
          attach(data.job_id);
          return;
        }
        stateEl.textContent = "❌ failed";
        stateEl.className = "explode-state is-fail";
        append(el("div", "chat-msg chat-msg-error",
                  "⚠️ " + (data.error || `Request failed (${res.status}).`)));
        return;
      }
      attach(data.job_id);
    } catch (err) {
      stateEl.textContent = "❌ failed";
      stateEl.className = "explode-state is-fail";
      append(el("div", "chat-msg chat-msg-error", "⚠️ Could not queue the job."));
    } finally {
      startBtn.disabled = false;
    }
  }

  function reset(title) {
    stopPolling();
    log.replaceChildren();
    usageEl.textContent = "";
    stateEl.textContent = "ready";
    stateEl.className = "explode-state";
    heading.textContent = title;
  }

  document.querySelectorAll(".explode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      reset(`Explode "${btn.dataset.title}"`);
      current = { gameId: btn.dataset.gameId, title: btn.dataset.title,
                  jobId: null, lastSeq: 0, timer: null, errors: 0 };
      intro.textContent =
        "Splits this game's single index.html into src/ modules so later " +
        "edits only rewrite the modules they touch. This is a real DeepSeek " +
        "run and costs tokens. It forks: the original stays as it is.";
      startBtn.hidden = false;
      startBtn.disabled = false;
      dialog.showModal();
    });
  });

  // History tab: replay a finished (or still-running) agent job's
  // transcript. attach() handles both — a terminal job simply renders
  // every event at once and then finishes.
  document.querySelectorAll(".transcript-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      reset(`Transcript — ${btn.dataset.title}`);
      current = { gameId: null, title: btn.dataset.title, jobId: null,
                  lastSeq: 0, timer: null, errors: 0 };
      intro.textContent =
        "Every step this job's editing agent took, replayed from the " +
        "durable agent_events log.";
      dialog.showModal();
      attach(btn.dataset.jobId);
    });
  });

  startBtn.addEventListener("click", start);

  closeBtn.addEventListener("click", () => {
    // Closing only detaches this viewer — the job keeps running in the
    // background worker, and the History tab will have it either way.
    stopPolling();
    dialog.close();
  });
})();
