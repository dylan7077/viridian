// Site-wide premium micro-interactions: page-fade nav, button ripples, card tilt.
(function () {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── page transition: fade the page out, then navigate ──────
  if (!reduce) {
    document.addEventListener("click", (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      const a = e.target.closest("a[href]");
      if (!a) return;
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || a.target === "_blank" ||
          a.hasAttribute("download") || /^(https?:|mailto:|tel:)/.test(href) &&
          a.hostname !== location.hostname) return;
      if (a.hostname && a.hostname !== location.hostname) return;  // external
      e.preventDefault();
      document.documentElement.classList.add("page-leaving");
      setTimeout(() => { location.href = href; }, 200);
    });
    // restore on back/forward (page shown from bfcache)
    window.addEventListener("pageshow", () =>
      document.documentElement.classList.remove("page-leaving"));
  }

  // ── button press ripple ────────────────────────────────────
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest(".btn, .lib-pg-btn");
    if (!btn || btn.disabled) return;
    const r = btn.getBoundingClientRect();
    const size = Math.max(r.width, r.height);
    const rip = document.createElement("span");
    rip.className = "ripple";
    rip.style.width = rip.style.height = size + "px";
    rip.style.left = e.clientX - r.left - size / 2 + "px";
    rip.style.top = e.clientY - r.top - size / 2 + "px";
    btn.appendChild(rip);
    rip.addEventListener("animationend", () => rip.remove());
  });

  // ── 3D hover-tilt on library grid cards (cursor parallax) ──
  if (!reduce) {
    const grid = document.getElementById("libGrid");
    if (grid) {
      grid.addEventListener("pointermove", (e) => {
        const card = e.target.closest(".card-item");
        if (!card) return;
        const b = card.getBoundingClientRect();
        const px = (e.clientX - b.left) / b.width;
        const py = (e.clientY - b.top) / b.height;
        card.style.setProperty("--rx", ((0.5 - py) * 11).toFixed(2) + "deg");
        card.style.setProperty("--ry", ((px - 0.5) * 11).toFixed(2) + "deg");
      });
      grid.addEventListener("pointerout", (e) => {
        const card = e.target.closest(".card-item");
        if (card) {
          card.style.setProperty("--rx", "0deg");
          card.style.setProperty("--ry", "0deg");
        }
      });
    }
  }
})();
