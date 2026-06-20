// Home page extras: live card count in the hero band + a "recently graded" strip.
(function () {
  // ── live cards-indexed count in the stat band (animated count-up) ──
  const countEl = document.getElementById("hsCount");
  function countUp(el, to, dur = 1100) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = to.toLocaleString();
      return;
    }
    const t0 = performance.now();
    (function tick(now) {
      const t = Math.min(1, (now - t0) / dur);
      const v = Math.round(to * (1 - Math.pow(1 - t, 3)));   // easeOutCubic
      el.textContent = v.toLocaleString();
      if (t < 1) requestAnimationFrame(tick);
    })(t0);
  }
  fetch("/api/health")
    .then((r) => r.json())
    .then((h) => {
      const n = Number(h.indexed ?? h.index_size ?? 0);
      if (countEl && n > 0) countUp(countEl, n);
    })
    .catch(() => {});

  // ── "just graded" sliding strip (same component as /activity) ──
  const wrap = document.getElementById("homeStrip");
  const track = document.getElementById("homeStripTrack");
  if (!wrap || !track) return;

  const gradeClass = (g) =>
    g == null ? "g-none" : g >= 9 ? "g-gem" : g >= 7 ? "g-high" : "g-low";
  const thumbUrl = (u) => (u || "").replace("/high.png", "/low.png");

  function buildSlide(it) {
    const a = document.createElement("a");
    a.className = "home-slide";
    a.href = `/library?card=${encodeURIComponent(it.id)}`;
    a.title = `${it.name}${it.grade != null ? " · PSA " + it.grade : ""}`;
    const img = document.createElement("img");
    img.src = thumbUrl(it.image);
    img.alt = it.name || "";
    img.loading = "lazy";
    a.appendChild(img);
    if (it.grade != null) {
      const g = document.createElement("span");
      g.className = `home-slide-grade ${gradeClass(it.grade)}`;
      g.textContent = "PSA " + it.grade;
      a.appendChild(g);
    }
    return a;
  }

  let key = "";
  async function load() {
    let items;
    try {
      items = (await (await fetch("/api/activity?limit=30")).json()).items || [];
    } catch {
      return;
    }
    const cards = items.filter((it) => it.image && it.id);
    if (!cards.length) {
      wrap.hidden = true;
      return;
    }
    const k = cards.map((c) => c.id + ":" + c.ts).join("|");
    if (k === key) {
      wrap.hidden = false;
      return; // unchanged — keep sliding
    }
    key = k;
    // repeat to overflow the viewport, then duplicate for a seamless -50% loop
    const per = Math.max(cards.length, Math.ceil((window.innerWidth || 1200) / 150) + 2);
    const block = [];
    for (let i = 0; i < per; i++) block.push(cards[i % cards.length]);
    track.replaceChildren(...block.map(buildSlide), ...block.map(buildSlide));
    track.style.animationDuration = Math.max(20, block.length * 3.5) + "s";
    wrap.hidden = false;
  }

  load();
  setInterval(load, 8000);
})();
