(() => {
  const panel = document.getElementById("enhance-panel");
  if (!panel) return;

  const lockPlate = document.getElementById("lock-plate");
  const lockLight = document.getElementById("lock-light");
  const lockLabel = document.getElementById("lock-label");
  const banner = document.getElementById("lock-banner");
  const form = document.getElementById("enhance-form");
  const submitBtn = document.getElementById("enhance-submit");
  const fields = [document.getElementById("description-field"), document.getElementById("new_title")];

  const PING_INTERVAL_MS = 10000;

  const phase = panel.dataset.lockPhase; // "form" | "job"
  let locked = panel.dataset.locked === "true";
  const heldByMe = panel.dataset.heldByMe === "true";
  const jobStatus = panel.dataset.jobStatus;
  const jobStartedAt = panel.dataset.jobStartedAt ? new Date(panel.dataset.jobStartedAt) : null;
  const lockExpiresAt = panel.dataset.lockExpiresAt ? new Date(panel.dataset.lockExpiresAt) : null;
  const lockToken = panel.dataset.lockToken;
  const pingUrl = panel.dataset.pingUrl;
  const releaseUrl = panel.dataset.releaseUrl;

  function setLock(text, css) {
    lockPlate.hidden = false;
    lockLabel.textContent = text;
    lockLight.className = "lock-light " + css;
  }

  function showBanner(text) {
    banner.textContent = text;
    banner.hidden = false;
  }

  function disableForm(message) {
    locked = true;
    fields.forEach((f) => f && (f.disabled = true));
    submitBtn.disabled = true;
    if (message) showBanner(message);
  }

  function formatCountdown(ms) {
    const total = Math.max(0, Math.round(ms / 1000));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function formatElapsed(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  let tickTimer = null;

  if (phase === "job") {
    // Someone's enhancement (possibly the current visitor's own) is
    // actually generating server-side — this has no fixed length, it
    // depends on how long the AI model takes, so there is no countdown
    // here, only an elapsed clock.
    const who = heldByMe ? "Your enhancement" : "Someone's enhancement";
    setLock(heldByMe ? "🔒 GENERATING (YOURS)" : "🔒 GENERATING", "on locked-job");
    if (jobStatus === "queued") {
      showBanner(`${who} is queued and will start shortly. This form stays locked until it finishes.`);
    } else {
      const tick = () => {
        const elapsed = jobStartedAt ? Date.now() - jobStartedAt.getTime() : 0;
        showBanner(
          `${who} is generating (${formatElapsed(elapsed)} so far). This can take longer than ` +
          `10 minutes depending on how big the change is — the form stays locked until it's done.`
        );
      };
      tick();
      tickTimer = setInterval(tick, 1000);
    }
    disableForm();
  } else if (locked && !heldByMe) {
    // Someone else has the form open (phase A, still pre-submission).
    setLock("🔒 LOCKED", "on locked-other");
    const tick = () => {
      if (!lockExpiresAt) return;
      const remaining = lockExpiresAt.getTime() - Date.now();
      if (remaining <= 0) {
        showBanner("Their lock should be freeing up any moment — reopen this page to try again.");
        clearInterval(tickTimer);
        return;
      }
      showBanner(`Another visitor is filling out this form. Free again in ${formatCountdown(remaining)} at the latest.`);
    };
    tick();
    tickTimer = setInterval(tick, 1000);
    disableForm();
  } else if (!locked && heldByMe && phase === "form") {
    // We hold the lock — show the countdown, keep it alive with a
    // heartbeat, and watch for losing it (idle timeout, or a fresher
    // acquisition elsewhere) so we can stop the user from submitting
    // into a lock that's no longer theirs. Each successful heartbeat
    // slides the server's deadline forward (see db.heartbeat_enhance_lock),
    // so an actively-open tab shouldn't ever actually hit zero here — but
    // track the server's latest expires_at rather than the page-load-time
    // value so the displayed countdown matches reality if a ping is ever
    // delayed.
    let currentExpiresAt = lockExpiresAt;
    setLock("🔒 LOCKED BY YOU", "on locked-mine");
    const tick = () => {
      if (!currentExpiresAt) return;
      const remaining = currentExpiresAt.getTime() - Date.now();
      showBanner(`You've locked this game for enhancing — ${formatCountdown(Math.max(0, remaining))} remaining.`);
    };
    tick();
    tickTimer = setInterval(tick, 1000);

    let pingTimer = null;

    async function ping() {
      try {
        const body = new URLSearchParams({ lock_token: lockToken });
        const res = await fetch(pingUrl, { method: "POST", body });
        const data = await res.json();
        if (!data.ok) {
          clearInterval(pingTimer);
          clearInterval(tickTimer);
          disableForm("You lost your lock on this game (idle too long) — reopen this page to try again.");
          setLock("🔒 LOCK LOST", "locked-other");
        } else if (data.expires_at) {
          currentExpiresAt = new Date(data.expires_at);
        }
      } catch (err) {
        // transient network error — keep trying on the next tick
      }
    }

    pingTimer = setInterval(ping, PING_INTERVAL_MS);

    // A backgrounded tab can have its timers throttled by the browser,
    // so switching back to this tab might find the lock already expired
    // server-side — check immediately on refocus instead of waiting for
    // the next scheduled ping.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") ping();
    });

    // Submitting the form navigates the page away, which fires
    // pagehide/beforeunload just like closing the tab — but here the
    // server-side POST handler is what owns the lock (it heartbeats it,
    // creates the job, then releases it). If we also fired the release
    // beacon it would race the POST and often delete the lock first,
    // making the submit's heartbeat fail with a bogus "lock expired".
    // So suppress the unload-release once a real submit is under way.
    let submitting = false;
    if (form) form.addEventListener("submit", () => { submitting = true; });

    function release() {
      if (locked || submitting) return; // lost/disabled, or the POST owns it now
      navigator.sendBeacon(releaseUrl, new URLSearchParams({ lock_token: lockToken }));
    }
    window.addEventListener("pagehide", release);
    window.addEventListener("beforeunload", release);
  }
})();
