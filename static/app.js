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

// ---- Info modal ----
const infoBackdrop = document.getElementById("info-modal-backdrop");
let infoModalLastFocused = null;

function renderLineageLink(item) {
  const link = document.createElement("a");
  link.href = "#";
  link.textContent = item.title;
  link.dataset.slug = item.slug;
  return link;
}

async function openInfoModal(gameId) {
  infoModalLastFocused = document.activeElement;
  try {
    const res = await fetch(`/api/games/${encodeURIComponent(gameId)}/info`);
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById("info-modal-title").textContent = data.title;
    const meta = [
      `by ${data.creator || "anonymous"}`,
      data.model ? `${data.model} (${data.effort || "default"})` : null,
      data.tokens_used != null ? `${data.tokens_used} tokens` : null,
    ].filter(Boolean).join(" · ");
    document.getElementById("info-modal-meta").textContent = meta;
    document.getElementById("info-modal-prompt").textContent =
      data.prompt || "(no prompt recorded)";

    const lineageEl = document.getElementById("info-modal-lineage");
    lineageEl.innerHTML = "";

    if (data.ancestors.length) {
      const h = document.createElement("h3");
      h.textContent = "History";
      lineageEl.appendChild(h);
      const chain = data.ancestors.concat([{ title: data.title, slug: null }]);
      chain.forEach((item, i) => {
        if (item.slug) {
          lineageEl.appendChild(renderLineageLink(item));
        } else {
          const span = document.createElement("span");
          span.textContent = item.title;
          lineageEl.appendChild(span);
        }
        if (i < chain.length - 1) {
          lineageEl.appendChild(document.createTextNode(" → "));
        }
      });
    }

    if (data.siblings.length) {
      const h2 = document.createElement("h3");
      h2.textContent = "Other forks";
      lineageEl.appendChild(h2);
      data.siblings.forEach((s) => {
        lineageEl.appendChild(renderLineageLink(s));
        lineageEl.appendChild(document.createElement("br"));
      });
    }

    infoBackdrop.hidden = false;
    document.getElementById("info-modal-close").focus();
  } catch (err) {
    // network error - no modal opens
  }
}

function closeInfoModal() {
  infoBackdrop.hidden = true;
  if (infoModalLastFocused) infoModalLastFocused.focus();
}

document.querySelectorAll(".cart-info").forEach((btn) => {
  btn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    openInfoModal(btn.dataset.gameId);
  });
});

document.getElementById("info-modal-close").addEventListener("click", closeInfoModal);

infoBackdrop.addEventListener("click", (evt) => {
  if (evt.target === infoBackdrop) closeInfoModal();
});

document.addEventListener("keydown", (evt) => {
  if (evt.key === "Escape" && !infoBackdrop.hidden) closeInfoModal();
});

document.getElementById("info-modal-lineage").addEventListener("click", (evt) => {
  const a = evt.target.closest("a[data-slug]");
  if (!a) return;
  evt.preventDefault();
  closeInfoModal();
  const cart = document.querySelector(
    `.cart-select[data-slug="${CSS.escape(a.dataset.slug)}"]`
  );
  if (cart) cart.click();
});
