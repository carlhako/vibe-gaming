(() => {
  const panel = document.getElementById("status-panel");
  const jobId = panel.dataset.jobId;
  const heading = document.getElementById("status-heading");
  const sourceEl = document.getElementById("status-source");
  const promptEl = document.getElementById("status-prompt");
  const detail = document.getElementById("status-detail");
  const eta = document.getElementById("status-eta");
  const timerEl = document.getElementById("status-timer");
  const statsEl = document.getElementById("status-stats");
  const actions = document.getElementById("status-actions");
  const powerLight = document.getElementById("power-light");
  const cancelBtn = document.getElementById("cancel-enhance-btn");
  const cancelDialog = document.getElementById("cancel-dialog");
  const cancelDialogConfirm = document.getElementById("cancel-dialog-confirm");
  const cancelDialogDone = document.getElementById("cancel-dialog-done");
  const cancelDialogPrompt = document.getElementById("cancel-dialog-prompt");
  const cancelDialogYes = document.getElementById("cancel-dialog-yes");
  const cancelDialogNo = document.getElementById("cancel-dialog-no");
  const cancelDialogCopy = document.getElementById("cancel-dialog-copy");
  const cancelDialogClose = document.getElementById("cancel-dialog-close");
  const cancelDialogError = document.getElementById("cancel-dialog-error");

  const LABELS = {
    queued: "Queued…",
    generating: "Generating…",
    success: "Done!",
    failed: "Failed",
    cancelled: "Cancelled",
  };

  const KIND_VERB = {
    create: "Builds",
    enhance: "Enhancements",
  };

  let pollTimer = null;
  let tickTimer = null;
  let generatingStartedAt = null; // Date, derived from the server's timestamp
  let lastPrompt = ""; // kept around so the cancel dialog can show it post-cancel

  function formatMinutes(seconds) {
    const minutes = Math.round(seconds / 60);
    return minutes < 1 ? "under a minute" : `about ${minutes} min`;
  }

  // "Xm Ys" for a finished duration.
  function formatDuration(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  }

  // "MM:SS" ticking clock. Recomputed from generatingStartedAt (a fixed
  // wall-clock timestamp) on every tick rather than incremented, so it
  // self-corrects instantly after the tab was backgrounded/throttled —
  // no drift, no catching up.
  function formatElapsed(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  function tick() {
    if (!generatingStartedAt) return;
    timerEl.textContent = `Generating for ${formatElapsed(Date.now() - generatingStartedAt)}`;
  }

  function stopTicking() {
    clearInterval(tickTimer);
    tickTimer = null;
    generatingStartedAt = null;
    timerEl.hidden = true;
  }

  function render(job) {
    heading.textContent = LABELS[job.status] || job.status;
    if (job.prompt) lastPrompt = job.prompt;

    if (sourceEl.hidden && job.source_title) {
      sourceEl.textContent = `Enhancing "${job.source_title}" (v${job.source_version})`;
      sourceEl.hidden = false;
    }

    if (promptEl.hidden && job.prompt) {
      const verb = job.kind === "enhance" ? "Enhancing" : "Request";
      promptEl.textContent = `${verb}: "${job.prompt}"`;
      promptEl.hidden = false;
    }

    const cancellable = job.kind === "enhance" && (job.status === "queued" || job.status === "generating");
    cancelBtn.hidden = !cancellable;

    if (job.status === "queued" || job.status === "generating") {
      if (job.status === "queued") {
        detail.textContent = job.queue_position > 0
          ? `Position ${job.queue_position + 1} in queue.`
          : "You're next in line.";
        eta.textContent = job.eta_seconds != null
          ? `Approx ${formatMinutes(job.eta_seconds)} until your turn.`
          : "We don't have a time estimate yet.";
        stopTicking();
      } else if (job.awaiting_approval) {
        // Still 'generating' as far as the job row is concerned — it just
        // can't proceed until someone answers. The buttons live in the
        // transcript pane, because the transcript is what the answer depends
        // on; this pane's job is to make sure you notice.
        detail.textContent = "Paused — the agent used all its turns and is waiting "
          + "for you. Review the transcript on the right and choose whether to "
          + "give it more.";
      } else {
        detail.textContent = "DeepSeek is writing the game and running the safety/smoke checks now.";
        const verb = KIND_VERB[job.kind] || "Jobs";
        eta.textContent = job.avg_duration_seconds != null
          ? `${verb} like this usually take ${formatMinutes(job.avg_duration_seconds)}.`
          : "";

        if (job.generating_started_at) {
          const startedAt = new Date(job.generating_started_at);
          if (!generatingStartedAt || generatingStartedAt.getTime() !== startedAt.getTime()) {
            generatingStartedAt = startedAt;
            timerEl.hidden = false;
            tick();
            clearInterval(tickTimer);
            tickTimer = setInterval(tick, 1000);
          }
        }
      }
      return;
    }

    powerLight.classList.remove("on");
    clearInterval(pollTimer);
    stopTicking();
    eta.textContent = "";

    if (job.status === "success") {
      const stats = [
        job.duration_seconds != null ? `Took ${formatDuration(job.duration_seconds)}` : null,
        job.tokens_used != null ? `${job.tokens_used} tokens` : null,
      ].filter(Boolean).join(" · ");
      statsEl.textContent = stats;

      detail.textContent = `"${job.result_title}" is ready.`;
      const link = document.createElement("a");
      link.className = "play-link";
      link.href = "/play/" + encodeURIComponent(job.result_slug);
      link.textContent = "Play now →";
      actions.appendChild(link);
      actions.hidden = false;
    } else if (job.status === "failed") {
      detail.textContent = job.error || "Something went wrong.";
    } else if (job.status === "cancelled") {
      detail.textContent = "Cancelled by you.";
    }
  }

  let consecutiveErrors = 0;
  const MAX_CONSECUTIVE_ERRORS = 10;

  async function poll() {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      if (res.status === 404) {
        // The job genuinely doesn't exist (bad/stale URL) — this is terminal.
        clearInterval(pollTimer);
        stopTicking();
        heading.textContent = "Unknown job";
        detail.textContent = "This status page's job could not be found.";
        return;
      }
      if (!res.ok) {
        // A transient server error (e.g. a momentary DB hiccup) — the job
        // itself is still running, so don't declare it "unknown". Keep
        // polling; only give up after repeated consecutive failures.
        throw new Error(`status ${res.status}`);
      }
      consecutiveErrors = 0;
      render(await res.json());
    } catch (err) {
      consecutiveErrors += 1;
      console.error("status poll failed", err);
      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        clearInterval(pollTimer);
        stopTicking();
        heading.textContent = "Lost connection";
        detail.textContent =
          "Couldn't reach the server after several attempts. Generation may still be running in the background — reload this page to check.";
      }
    }
  }

  function openCancelDialog() {
    cancelDialogConfirm.hidden = false;
    cancelDialogDone.hidden = true;
    cancelDialogYes.disabled = false;
    cancelDialogYes.textContent = "Yes, cancel";
    cancelDialogError.hidden = true;
    cancelDialog.showModal();
  }

  cancelBtn.addEventListener("click", openCancelDialog);
  cancelDialogNo.addEventListener("click", () => cancelDialog.close());
  cancelDialogClose.addEventListener("click", () => cancelDialog.close());

  cancelDialogYes.addEventListener("click", async () => {
    cancelDialogYes.disabled = true;
    cancelDialogYes.textContent = "Cancelling…";
    try {
      const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      if (!res.ok && res.status !== 409) {
        throw new Error(`cancel failed: ${res.status}`);
      }
      // Either it cancelled just now, or a 409 means it already reached a
      // terminal state on its own — either way, one more poll picks up the
      // real status and renders it through the normal terminal branches.
      await poll();
      cancelDialogConfirm.hidden = true;
      cancelDialogDone.hidden = false;
      cancelDialogPrompt.value = lastPrompt;
    } catch (err) {
      console.error("cancel request failed", err);
      cancelDialogYes.disabled = false;
      cancelDialogYes.textContent = "Yes, cancel";
      cancelDialogError.textContent = "Couldn't cancel — please try again.";
      cancelDialogError.hidden = false;
    }
  });

  cancelDialogCopy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(cancelDialogPrompt.value);
      cancelDialogCopy.textContent = "Copied!";
      setTimeout(() => { cancelDialogCopy.textContent = "Copy prompt"; }, 1500);
    } catch (err) {
      cancelDialogPrompt.select();
    }
  });

  poll();
  pollTimer = setInterval(poll, 2000);
})();
