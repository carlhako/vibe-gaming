(() => {
  const panel = document.getElementById("status-panel");
  const jobId = panel.dataset.jobId;
  const heading = document.getElementById("status-heading");
  const detail = document.getElementById("status-detail");
  const eta = document.getElementById("status-eta");
  const timerEl = document.getElementById("status-timer");
  const statsEl = document.getElementById("status-stats");
  const actions = document.getElementById("status-actions");
  const powerLight = document.getElementById("power-light");

  const LABELS = {
    queued: "Queued…",
    generating: "Generating…",
    success: "Done!",
    failed: "Failed",
  };

  const KIND_VERB = {
    create: "Builds",
    enhance: "Enhancements",
  };

  let pollTimer = null;
  let tickTimer = null;
  let generatingStartedAt = null; // Date, derived from the server's timestamp

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

    if (job.status === "queued" || job.status === "generating") {
      if (job.status === "queued") {
        detail.textContent = job.queue_position > 0
          ? `Position ${job.queue_position + 1} in queue.`
          : "You're next in line.";
        eta.textContent = job.eta_seconds != null
          ? `Approx ${formatMinutes(job.eta_seconds)} until your turn.`
          : "We don't have a time estimate yet.";
        stopTicking();
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
      detail.textContent = `"${job.result_title}" is ready.`;
      const stats = [
        job.duration_seconds != null ? `Took ${formatDuration(job.duration_seconds)}` : null,
        job.tokens_used != null ? `${job.tokens_used} tokens` : null,
      ].filter(Boolean).join(" · ");
      statsEl.textContent = stats;
      const link = document.createElement("a");
      link.className = "play-link";
      link.href = "/play/" + encodeURIComponent(job.result_slug);
      link.textContent = "Play now →";
      actions.appendChild(link);
      actions.hidden = false;
    } else if (job.status === "failed") {
      detail.textContent = job.error || "Something went wrong.";
    }
  }

  async function poll() {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      if (!res.ok) {
        clearInterval(pollTimer);
        stopTicking();
        heading.textContent = "Unknown job";
        detail.textContent = "This status page's job could not be found.";
        return;
      }
      render(await res.json());
    } catch (err) {
      // transient network error — keep polling
    }
  }

  poll();
  pollTimer = setInterval(poll, 2000);
})();
