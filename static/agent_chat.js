(() => {
  const pane = document.getElementById("chat-pane");
  if (!pane) return;
  const jobId = pane.dataset.jobId;
  const log = document.getElementById("chat-log");
  const usageBar = document.getElementById("chat-usage-bar");

  const TOOL_ICON = {
    read_map: "📖",
    list_files: "📂",
    read_file: "📖",
    search: "🔍",
    write_file: "✏️",
    finish: "🏁",
  };

  const THOUGHT_COLLAPSE_CHARS = 240;

  function isPinnedToBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  }

  function scrollToBottom() {
    log.scrollTop = log.scrollHeight;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderThought(event) {
    const text = event.content || "";
    const wrap = el("details", "chat-msg chat-msg-thought");
    wrap.appendChild(el("summary", null, "🤔 Thinking"));
    wrap.appendChild(el("pre", null, text));
    if (text.length <= THOUGHT_COLLAPSE_CHARS) wrap.open = true;
    return wrap;
  }

  function renderAssistant(event) {
    return el("div", "chat-msg chat-msg-assistant", event.content || "");
  }

  // Set once the first 'usage' event arrives (one per LLM call, Sprint 3)
  // and kept up to date after that — the source for both the always-on
  // running-total bar and the step/token figures in the terminal summary.
  let lastUsage = null;

  function updateUsageBar(data) {
    lastUsage = data;
    usageBar.hidden = false;
    const parts = [
      `Step ${data.step}`,
      `${(data.input_tokens || 0).toLocaleString()} in`,
      `${(data.output_tokens || 0).toLocaleString()} out`,
    ];
    if (data.cached_tokens) parts.push(`${data.cached_tokens.toLocaleString()} cached`);
    parts.push(`${(data.tokens_used || 0).toLocaleString()} total`);
    usageBar.textContent = parts.join(" · ");
  }

  function renderUsage(event) {
    const data = event.data || {};
    updateUsageBar(data);
    return el("div", "chat-msg chat-msg-usage", "📊 " + (event.content || ""));
  }

  function renderSummary() {
    if (!lastUsage) return null;
    const parts = [
      `${lastUsage.step} step${lastUsage.step === 1 ? "" : "s"}`,
      `${(lastUsage.input_tokens || 0).toLocaleString()} in`,
      `${(lastUsage.output_tokens || 0).toLocaleString()} out`,
    ];
    if (lastUsage.cached_tokens) parts.push(`${lastUsage.cached_tokens.toLocaleString()} cached`);
    parts.push(`${(lastUsage.tokens_used || 0).toLocaleString()} total`);
    return el("div", "chat-msg chat-msg-summary", "🧮 " + parts.join(" · "));
  }

  function renderToolCall(event) {
    const tool = (event.data && event.data.tool) || "";
    const icon = TOOL_ICON[tool] || "🔧";
    return el("div", "chat-msg chat-msg-tool_call", `${icon} ${event.content || tool}`);
  }

  function renderToolResult(event) {
    const data = event.data || {};
    let text = event.content || "";
    let cls = "chat-msg chat-msg-tool_result";
    if (data.tool === "list_files" && data.file_count != null) {
      text = `📂 ${data.file_count} file${data.file_count === 1 ? "" : "s"}`;
    } else if (data.tool === "write_file") {
      const ok = data.outcome === "ok";
      cls += ok ? "" : " is-rejected";
      text = (ok ? "✅ " : "❌ ") + text;
    } else if (text.startsWith("ERROR:")) {
      cls += " is-error";
      text = "❌ " + text;
    }
    return el("div", cls, text);
  }

  function renderBuild(event) {
    const data = event.data || {};
    const ok = data.outcome === "success";
    const suffix = data.attempt ? ` (attempt ${data.attempt})` : "";
    const cls = `chat-msg chat-msg-build ${ok ? "is-success" : "is-fail"}`;
    return el("div", cls, (ok ? "✅ " : "❌ ") + (event.content || "") + suffix);
  }

  function renderFinal(event) {
    const frag = document.createDocumentFragment();
    const data = event.data || {};
    const card = el("div", "chat-msg chat-msg-final");
    card.appendChild(el("div", null, "🎉 " + (event.content || "Enhancement complete.")));
    if (data.url) {
      const link = document.createElement("a");
      link.className = "play-link chat-final-play-link";
      link.href = data.url;
      link.textContent = "Play now →";
      card.appendChild(link);
    }
    frag.appendChild(card);
    const summary = renderSummary();
    if (summary) frag.appendChild(summary);
    return frag;
  }

  function renderError(event) {
    const frag = document.createDocumentFragment();
    frag.appendChild(el("div", "chat-msg chat-msg-error", "⚠️ " + (event.content || "Something went wrong.")));
    const summary = renderSummary();
    if (summary) frag.appendChild(summary);
    return frag;
  }

  const RENDERERS = {
    thought: renderThought,
    assistant: renderAssistant,
    usage: renderUsage,
    tool_call: renderToolCall,
    tool_result: renderToolResult,
    build: renderBuild,
    final: renderFinal,
    error: renderError,
  };

  // Reused as the "waiting"/"no transcript" line until the first real event
  // arrives, then removed — avoids showing a stale placeholder alongside
  // real messages.
  const placeholder = el("div", "chat-empty", "Waiting for the agent…");
  log.appendChild(placeholder);

  function appendEvent(event) {
    const render = RENDERERS[event.role];
    if (!render) return;
    if (placeholder.isConnected) placeholder.remove();
    const pinned = isPinnedToBottom();
    log.appendChild(render(event));
    if (pinned) scrollToBottom();
  }

  let lastSeq = 0;
  let sawAnyEvent = false;
  let pollTimer = null;
  let consecutiveErrors = 0;
  const MAX_CONSECUTIVE_ERRORS = 10;
  const TERMINAL_STATUSES = new Set(["success", "failed"]);

  async function poll() {
    try {
      const res = await fetch(`/api/jobs/${jobId}/events?since=${lastSeq}`);
      if (!res.ok) throw new Error(`events ${res.status}`);
      consecutiveErrors = 0;
      const data = await res.json();
      for (const event of data.events) {
        appendEvent(event);
        lastSeq = Math.max(lastSeq, event.seq);
        sawAnyEvent = true;
      }
      if (TERMINAL_STATUSES.has(data.status)) {
        clearInterval(pollTimer);
        pollTimer = null;
        if (!sawAnyEvent) {
          placeholder.textContent = "No live transcript for this job.";
        }
      }
    } catch (err) {
      consecutiveErrors += 1;
      console.error("agent events poll failed", err);
      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  }

  poll();
  pollTimer = setInterval(poll, 1000);
})();
