(() => {
  const panel = document.getElementById("status-panel");
  const jobId = panel.dataset.jobId;
  const heading = document.getElementById("status-heading");
  const detail = document.getElementById("status-detail");
  const eta = document.getElementById("status-eta");
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

  let timer = null;

  function formatMinutes(seconds) {
    const minutes = Math.round(seconds / 60);
    return minutes < 1 ? "under a minute" : `about ${minutes} min`;
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
      } else {
        detail.textContent = "DeepSeek is writing the game and running the safety/smoke checks now.";
        const verb = KIND_VERB[job.kind] || "Jobs";
        eta.textContent = job.avg_duration_seconds != null
          ? `${verb} like this usually take ${formatMinutes(job.avg_duration_seconds)}.`
          : "";
      }
      return;
    }

    powerLight.classList.remove("on");
    clearInterval(timer);
    eta.textContent = "";

    if (job.status === "success") {
      detail.textContent = `"${job.result_title}" is ready.`;
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
        clearInterval(timer);
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
  timer = setInterval(poll, 2000);
})();
