// Decorative-only effects for the index page: intro splash + starfield
// backdrop + game-start hooks. Loaded after app.js; touches no app state.
(() => {
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const INTRO_KEY = "vg_seen_intro";

  // ---- Intro splash ----
  const splash = document.getElementById("intro-splash");
  if (splash && !splash.hidden) {
    let dismissed = false;
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      sessionStorage.setItem(INTRO_KEY, "1");
      splash.classList.add("splash-out");
      const finish = () => { splash.hidden = true; };
      splash.addEventListener("animationend", finish, { once: true });
      setTimeout(finish, 600); // fallback if animationend never fires
    };
    splash.addEventListener("click", dismiss);
    document.addEventListener("keydown", dismiss, { once: true });
    setTimeout(dismiss, 4000);
  } else {
    // Guard script hid it (repeat visit or reduced motion) - keep flag set.
    sessionStorage.setItem(INTRO_KEY, "1");
  }

  // ---- Starfield ----
  const canvas = document.getElementById("starfield");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const COLORS = ["#cdd6e4", "#ffb400", "#57e389"];
  let stars = [];
  let width = 0;
  let height = 0;
  let rafId = null;
  let lastTime = 0;
  let lastDraw = 0;
  let dim = false;

  function spawnStars() {
    const count = Math.min(240, Math.floor((width * height) / 9000));
    stars = Array.from({ length: count }, () => {
      const z = 0.25 + Math.random() * 0.75; // depth: bigger = closer/faster
      const roll = Math.random();
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        size: Math.max(1, Math.round(z * 2)),
        vy: 6 + z * 18,
        phase: Math.random() * Math.PI * 2,
        color: roll < 0.92 ? COLORS[0] : roll < 0.97 ? COLORS[1] : COLORS[2],
      };
    });
  }

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    width = innerWidth;
    height = innerHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    spawnStars();
    if (REDUCED) drawFrame(0);
  }

  function drawFrame(t) {
    ctx.clearRect(0, 0, width, height);
    for (const s of stars) {
      ctx.globalAlpha = 0.35 + 0.5 * Math.abs(Math.sin(t / 1200 + s.phase));
      ctx.fillStyle = s.color;
      ctx.fillRect(s.x | 0, s.y | 0, s.size, s.size);
    }
    ctx.globalAlpha = 1;
  }

  function frame(t) {
    rafId = requestAnimationFrame(frame);
    const dt = Math.min(t - lastTime, 100) / 1000;
    lastTime = t;
    for (const s of stars) {
      s.y += s.vy * dt;
      if (s.y > height) {
        s.y = -2;
        s.x = Math.random() * width;
      }
    }
    // While a game is playing, redraw at ~20fps to leave cycles for the game.
    if (dim && t - lastDraw < 50) return;
    lastDraw = t;
    drawFrame(t);
  }

  function start() {
    if (rafId !== null || REDUCED) return;
    lastTime = performance.now();
    rafId = requestAnimationFrame(frame);
  }

  function stop() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => {
    document.hidden ? stop() : start();
  });
  resize();
  start();

  // ---- Game-start hook ----
  // .cart-select clicks bubble here (only rate/info buttons stopPropagation,
  // and those aren't game starts); modal lineage links replay via cart.click(),
  // which also dispatches a real bubbling click.
  const cartList = document.getElementById("cart-list");
  if (cartList) {
    cartList.addEventListener("click", (e) => {
      if (!e.target.closest(".cart-select")) return;
      document.getElementById("screen").classList.add("live");
      dim = true;
      canvas.classList.add("dim");
    });
  }
})();
