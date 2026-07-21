function bumpPlayCount(cartSelectBtn) {
  // The server increments the real count as a side effect of the iframe's
  // GET /play/<slug> request, which we have no response payload from (it's
  // a navigation, not a fetch) - so bump the visible count optimistically
  // rather than leaving it stale until the next full page load.
  const countEl = cartSelectBtn.closest(".cart")?.querySelector(".cart-play-count");
  if (!countEl) return;
  const n = parseInt(countEl.textContent, 10);
  if (Number.isNaN(n)) return;
  const next = n + 1;
  countEl.textContent = `${next} play${next === 1 ? "" : "s"}`;
}

function playSlug(slug, title) {
  document.querySelectorAll(".cart").forEach((c) => c.classList.remove("active"));
  const cart = document.querySelector(`.cart-select[data-slug="${CSS.escape(slug)}"]`);
  if (cart) cart.closest(".cart").classList.add("active");

  const frame = document.getElementById("game-frame");
  const empty = document.getElementById("screen-empty");
  const nowPlaying = document.getElementById("now-playing");
  const powerLight = document.getElementById("power-light");

  frame.src = "/play/" + encodeURIComponent(slug);
  frame.hidden = false;
  empty.hidden = true;
  nowPlaying.textContent = title;
  powerLight.classList.add("on");
}

document.querySelectorAll(".cart-select").forEach((btn) => {
  btn.addEventListener("click", () => {
    playSlug(btn.dataset.slug, btn.dataset.title);
    bumpPlayCount(btn);
  });
});

// ---- Fork family accordion ----
// Each family's "N earlier versions" row expands its older versions in
// place; only one family stays open at a time, so opening one collapses
// whichever other family was previously expanded.
document.querySelectorAll(".cart-family-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const children = document.getElementById(btn.getAttribute("aria-controls"));
    if (!children) return;
    const expanding = !children.classList.contains("expanded");

    document.querySelectorAll(".cart-family-children.expanded").forEach((other) => {
      if (other === children) return;
      other.classList.remove("expanded");
      const otherBtn = document.querySelector(`.cart-family-toggle[aria-controls="${other.id}"]`);
      if (otherBtn) otherBtn.setAttribute("aria-expanded", "false");
    });

    children.classList.toggle("expanded", expanding);
    btn.setAttribute("aria-expanded", String(expanding));
  });
});

// ---- Shelf tools collapse ----
// The arrow in the shelf header folds the tools block (marquee, account
// links, New Game) away so the game list gets the vertical space back.
const shelfCollapseBtn = document.getElementById("shelf-collapse");
const shelfTools = document.getElementById("shelf-tools");
if (shelfCollapseBtn && shelfTools) {
  const COLLAPSED_KEY = "vg_shelf_collapsed";

  function setShelfCollapsed(collapsed) {
    shelfTools.classList.toggle("collapsed", collapsed);
    shelfCollapseBtn.classList.toggle("flipped", collapsed);
    shelfCollapseBtn.setAttribute("aria-expanded", String(!collapsed));
    shelfCollapseBtn.title = collapsed ? "Expand menu" : "Collapse menu";
  }

  setShelfCollapsed(localStorage.getItem(COLLAPSED_KEY) === "1");
  shelfCollapseBtn.addEventListener("click", () => {
    const collapsed = !shelfTools.classList.contains("collapsed");
    setShelfCollapsed(collapsed);
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  });
}

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

function formatDuration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

function renderLineageLink(item) {
  const link = document.createElement("a");
  link.href = "#";
  link.textContent = item.hidden ? `${item.title} (hidden)` : item.title;
  link.dataset.slug = item.slug;
  link.dataset.title = item.title;
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
      data.duration_seconds != null ? `${formatDuration(data.duration_seconds)} to generate` : null,
    ].filter(Boolean).join(" · ");
    document.getElementById("info-modal-meta").textContent = meta;
    document.getElementById("info-modal-download").href =
      `/games/${encodeURIComponent(gameId)}/download`;
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

    const count = data.play_count || 0;
    document.getElementById("info-modal-play-count").textContent =
      `Play history — ${count} play${count === 1 ? "" : "s"}`;
    const playsList = document.getElementById("info-modal-plays-list");
    playsList.innerHTML = "";
    (data.recent_plays || []).forEach((ts) => {
      const li = document.createElement("li");
      li.textContent = new Date(ts).toLocaleString();
      playsList.appendChild(li);
    });
    if (!data.recent_plays || data.recent_plays.length === 0) {
      const li = document.createElement("li");
      li.textContent = "No plays yet.";
      playsList.appendChild(li);
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
  playSlug(a.dataset.slug, a.dataset.title || a.textContent);
  const cartSelectBtn = document.querySelector(`.cart-select[data-slug="${CSS.escape(a.dataset.slug)}"]`);
  if (cartSelectBtn) bumpPlayCount(cartSelectBtn);
});
