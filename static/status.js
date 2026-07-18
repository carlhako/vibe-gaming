(() => {
  const panel = document.getElementById("status-panel");
  const jobId = panel.dataset.jobId;
  const heading = document.getElementById("status-heading");
  const detail = document.getElementById("status-detail");
  const actions = document.getElementById("status-actions");
  const powerLight = document.getElementById("power-light");

  const LABELS = {
    queued: "Queued…",
    generating: "Generating…",
    success: "Done!",
    failed: "Failed",
  };

  let timer = null;

  function render(job) {
    heading.textContent = LABELS[job.status] || job.status;

    if (job.status === "queued" || job.status === "generating") {
      detail.textContent = job.status === "queued"
        ? "Waiting for a worker to pick this up."
        : "DeepSeek is writing the game and running the safety/smoke checks now.";
      return;
    }

    powerLight.classList.remove("on");
    clearInterval(timer);

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
