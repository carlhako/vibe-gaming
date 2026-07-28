(() => {
  const pane = document.getElementById("chat-pane");
  if (!pane) return;
  const jobId = pane.dataset.jobId;
  const log = document.getElementById("chat-log");
  const usageBar = document.getElementById("chat-usage-bar");

  const TOOL_ICON = {
    snapshot: "📦",
    read_map: "📖",
    list_files: "📂",
    read_file: "📖",
    search: "🔍",
    edit_file: "✂️",
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
    } else if (data.tool === "write_file" || data.tool === "edit_file") {
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

  // Every live approval card on the page, so a decision (or the run moving on
  // without one — timeout, cancel) can disable all of them. The prompt is a
  // real event in a permanent archive, which means it is also replayed on
  // finished jobs and on the admin History tab: the buttons must be inert
  // there, and the only reliable signal for that is the poll's
  // `awaiting_approval`, not the event itself.
  const approvalCards = [];
  // Latched the moment a button is clicked, so the 1s poll that lands between
  // the click and the server's write can't re-arm the buttons under the
  // user's cursor.
  let approvalAnswered = false;

  function setApprovalLive(live, note) {
    if (approvalAnswered) live = false;
    for (const card of approvalCards) {
      for (const btn of card.buttons) btn.disabled = !live;
      card.buttons[0].parentNode.hidden = !live;
      if (!live && note) card.note.textContent = note;
      card.note.hidden = !(!live && note);
    }
  }

  async function answerApproval(extraSteps, card, label) {
    approvalAnswered = true;
    for (const btn of card.buttons) btn.disabled = true;
    card.note.hidden = false;
    card.note.textContent = "Sending…";
    try {
      const res = await fetch(`/api/jobs/${jobId}/approve-steps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ extra_steps: extraSteps }),
      });
      // A 409 means the run stopped waiting on its own (timed out, or was
      // cancelled) between the card rendering and the click. Not an error to
      // shout about — the next poll renders what actually happened.
      if (!res.ok && res.status !== 409) throw new Error(`approve failed: ${res.status}`);
      setApprovalLive(false, res.status === 409 ? "The run stopped waiting." : label);
    } catch (err) {
      console.error("approve-steps failed", err);
      approvalAnswered = false;
      for (const btn of card.buttons) btn.disabled = false;
      card.note.textContent = "Could not send that — try again.";
    }
  }

  function renderApprovalRequest(event) {
    const data = event.data || {};
    const extra = data.extra_steps || 0;
    const card = el("div", "chat-msg chat-msg-approval");
    card.appendChild(el("div", "chat-approval-head", "⏸️ Waiting for you"));
    card.appendChild(el("div", null, event.content || ""));

    const actions = el("div", "chat-approval-actions");
    const more = el("button", "chat-approval-btn is-primary", `Give it ${extra} more turns`);
    const stop = el("button", "chat-approval-btn", "Stop and ship what's done");
    more.type = "button";
    stop.type = "button";
    actions.appendChild(more);
    actions.appendChild(stop);
    card.appendChild(actions);

    const note = el("div", "chat-approval-note");
    note.hidden = true;
    card.appendChild(note);

    const entry = { buttons: [more, stop], note };
    approvalCards.push(entry);
    more.addEventListener("click", () =>
      answerApproval(extra, entry, `Granted ${extra} more turns.`));
    stop.addEventListener("click", () =>
      answerApproval(0, entry, "Wrapping up now."));
    // Inert until the poll confirms this job is actually waiting — see
    // approvalCards. A replay renders the card with its buttons already gone.
    for (const btn of entry.buttons) btn.disabled = true;
    actions.hidden = true;
    return card;
  }

  function renderApprovalResult(event) {
    const outcome = (event.data || {}).outcome;
    const icon = outcome === "granted" ? "▶️" : "⏹️";
    setApprovalLive(false, null);
    return el("div", "chat-msg chat-msg-approval-result", icon + " " + (event.content || ""));
  }

  function renderFinal(event) {
    const frag = document.createDocumentFragment();
    const data = event.data || {};
    const card = el("div", "chat-msg chat-msg-final");
    if (data.incomplete) {
      // Shipped, but the agent never confirmed it was done — the build passed,
      // which is not the same as the request having been carried out. A 🎉
      // here is actively misleading; it sent a real requester off to play a
      // half-changed game (job 837b2b8c).
      card.className += " is-incomplete";
      card.appendChild(el("div", null, event.content || "Shipped without a final check."));
    } else {
      card.appendChild(el("div", null, "🎉 " + (event.content || "Enhancement complete.")));
    }
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
    approval_request: renderApprovalRequest,
    approval_result: renderApprovalResult,
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
  let kindApplied = false;
  const MAX_CONSECUTIVE_ERRORS = 10;
  // "cancelled" belongs here: without it a cancelled job kept polling this
  // endpoint once a second forever, successfully, so the consecutive-error
  // escape never fired either.
  const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled"]);

  async function poll() {
    try {
      const res = await fetch(`/api/jobs/${jobId}/events?since=${lastSeq}`);
      if (!res.ok) throw new Error(`events ${res.status}`);
      consecutiveErrors = 0;
      const data = await res.json();
      // A brand-new game request has no live tool-call/build steps like a
      // multi-file enhance does — just the model's own thinking — so the
      // generic "Waiting for the agent…" line gets a friendlier one-time
      // rewrite once we know the job's kind, before any event replaces it.
      if (!kindApplied && placeholder.isConnected && data.kind === "create") {
        placeholder.textContent =
          "We've sent your new game request over to our expert game-making agent — it's thinking it through now.";
        kindApplied = true;
      }
      for (const event of data.events) {
        appendEvent(event);
        lastSeq = Math.max(lastSeq, event.seq);
        sawAnyEvent = true;
      }
      // After the events, so a card that arrived in this same batch is armed
      // by the very poll that delivered it.
      setApprovalLive(!!data.awaiting_approval, null);
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
