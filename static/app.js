document.querySelectorAll(".cart-select").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".cart").forEach((c) => c.classList.remove("active"));
    btn.closest(".cart").classList.add("active");

    const frame = document.getElementById("game-frame");
    const empty = document.getElementById("screen-empty");
    const nowPlaying = document.getElementById("now-playing");
    const powerLight = document.getElementById("power-light");

    frame.src = "/play/" + encodeURIComponent(btn.dataset.slug);
    frame.hidden = false;
    empty.hidden = true;
    nowPlaying.textContent = btn.dataset.title;
    powerLight.classList.add("on");
  });
});

// ---- Ratings ----
// Real enforcement is server-side (409 on a repeat cookie/IP vote); the
// "voted" localStorage flag here only saves a round trip by pre-disabling
// buttons the browser already knows it used.
const VOTED_KEY = "vg_voted_games";

function getVotedSet() {
  try {
    return new Set(JSON.parse(localStorage.getItem(VOTED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function markVoted(gameId) {
  const voted = getVotedSet();
  voted.add(gameId);
  localStorage.setItem(VOTED_KEY, JSON.stringify([...voted]));
}

document.querySelectorAll(".cart-rate").forEach((rateBox) => {
  const gameId = rateBox.dataset.gameId;
  if (getVotedSet().has(gameId)) {
    rateBox.querySelectorAll(".rate-btn").forEach((b) => (b.disabled = true));
  }

  rateBox.querySelectorAll(".rate-btn").forEach((btn) => {
    btn.addEventListener("click", async (evt) => {
      evt.stopPropagation();
      const vote = parseInt(btn.dataset.vote, 10);
      try {
        const res = await fetch(`/api/games/${encodeURIComponent(gameId)}/rate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ vote }),
        });
        const data = await res.json();
        rateBox.querySelector(".rate-up .rate-count").textContent = data.thumbs_up;
        rateBox.querySelector(".rate-down .rate-count").textContent = data.thumbs_down;
        if (res.status === 200 || res.status === 409) {
          markVoted(gameId);
          rateBox.querySelectorAll(".rate-btn").forEach((b) => (b.disabled = true));
        }
      } catch (err) {
        // network error - leave buttons as-is, user can retry
      }
    });
  });
});
