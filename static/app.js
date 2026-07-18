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
