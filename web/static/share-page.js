// Public share page: fetch the grade snapshot behind /g/<token> and render it.
(function () {
  const root = document.getElementById("shareRoot");
  const token = location.pathname.split("/").filter(Boolean).pop();
  const GRADE_NAMES = {
    10: "Gem Mint", 9: "Mint", 8: "NM-MT", 7: "Near Mint", 6: "EX-MT",
    5: "Excellent", 4: "VG-EX", 3: "Very Good", 2: "Good", 1: "Poor",
  };
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (sym, n) =>
    sym + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function bar(label, val, measured) {
    return `<div class="subgrade">
      <span class="sg-label">${label}</span>
      <div class="sg-bar"><div class="sg-fill ${measured ? "" : "heur"}" style="width:${val ? val * 10 : 0}%"></div></div>
      <span class="sg-val">${val ?? "—"}</span>
    </div>`;
  }

  function render(d) {
    const isHolo = /holo|rare|illustration|ex|gx|secret/i.test(d.rarity || "");
    const cls = d.grade >= 9 ? "gold" : d.grade >= 7 ? "" : "low";
    const sub = [d.set, d.number ? "#" + d.number : null].filter(Boolean).join(" · ");
    const usd = (d.values || []).find((v) => v.currency === "USD" || v.symbol === "$") || {};

    let valHtml = "";
    if (usd.raw != null || usd.graded != null) {
      const rows = [];
      if (usd.raw != null) rows.push(`<div class="kv"><span class="k">Raw</span><span class="v">${money(usd.symbol || "$", usd.raw)}</span></div>`);
      if (usd.graded != null) {
        const lbl = d.graded_real ? "Graded" : "PSA 10 est.";
        rows.push(`<div class="kv"><span class="k">${lbl}</span><span class="v value-big">${money(usd.symbol || "$", usd.graded)}</span></div>`);
      }
      valHtml = `<div class="share-value">${rows.join("")}</div>`;
    }

    root.innerHTML = `
      <div class="share-card-wrap">
        <div class="holo-card ${isHolo ? "holo-on" : ""}" id="shareHolo">
          <img src="${esc(d.image)}" alt="${esc(d.name)}" />
        </div>
        <div class="share-info">
          <div class="grade-hero">
            <div class="seal ${cls}">
              <svg class="seal-ring" viewBox="0 0 132 132">
                <circle class="ring-bg" cx="66" cy="66" r="54"/>
                <circle class="ring-fg" cx="66" cy="66" r="54" stroke-dasharray="339.292" stroke-dashoffset="${339.292 * (1 - (d.grade || 0) / 10)}"/>
              </svg>
              <div class="seal-inner"><span class="seal-psa">PSA</span><span class="seal-num">${d.grade ?? "—"}</span></div>
            </div>
            <div class="grade-meta">
              <h3>${d.slab ? "PSA " + d.grade : esc(GRADE_NAMES[d.grade] || "Ungraded")}</h3>
              <p>${d.slab ? "Graded slab" + (d.cert ? " · cert " + esc(d.cert) : "") : "Estimated overall grade"}</p>
            </div>
          </div>
          <p class="card-caption"><strong>${esc(d.name)}</strong>${sub ? " · " + esc(sub) : ""}${d.rarity ? " · " + esc(d.rarity) : ""}</p>
          <div class="subgrades">
            ${bar("Centering", d.centering && d.centering.ok ? d.centering.grade : null, true)}
            ${bar("Corners", d.corners)}
            ${bar("Edges", d.edges)}
            ${bar("Surface", d.surface)}
          </div>
          ${valHtml}
          <div class="share-bar" id="shareBar">
            <div class="share-btns">
              <button type="button" class="share-btn" id="shareLink"><span class="share-ic">🔗</span> Copy link</button>
              <button type="button" class="share-btn" id="shareImg"><span class="share-ic">📸</span> Save image</button>
            </div>
          </div>
          <a class="btn primary full share-cta" href="/">Grade your own card free →</a>
        </div>
      </div>`;

    wireHolo();
    const V = window.Viridian;
    if (!V) return;
    const norm = V.normFromShare(d);
    const linkBtn = document.getElementById("shareLink");
    const imgBtn = document.getElementById("shareImg");
    linkBtn && linkBtn.addEventListener("click", async () => {
      const ok = await V.copy(location.href);
      V.toast(ok ? "Link copied — paste it anywhere" : location.href);
    });
    imgBtn && imgBtn.addEventListener("click", async () => {
      imgBtn.disabled = true;
      const old = imgBtn.innerHTML;
      imgBtn.innerHTML = `<span class="share-ic">⏳</span> Building…`;
      await V.saveOrShare(norm, location.href);
      imgBtn.disabled = false;
      imgBtn.innerHTML = old;
    });
  }

  // pointer-reactive holo tilt (same as the result panel)
  function wireHolo() {
    const card = document.getElementById("shareHolo");
    if (!card) return;
    card.addEventListener("pointermove", (e) => {
      const b = card.getBoundingClientRect();
      const px = (e.clientX - b.left) / b.width, py = (e.clientY - b.top) / b.height;
      card.style.setProperty("--active", "1");
      card.style.setProperty("--mx", `${px * 100}%`);
      card.style.setProperty("--my", `${py * 100}%`);
      card.style.setProperty("--bx", `${px * 100}%`);
      card.style.setProperty("--by", `${py * 100}%`);
      card.style.setProperty("--rx", `${(px - 0.5) * 18}deg`);
      card.style.setProperty("--ry", `${(0.5 - py) * 18}deg`);
    });
    card.addEventListener("pointerleave", () => {
      card.style.setProperty("--active", "0");
      card.style.setProperty("--rx", "0deg");
      card.style.setProperty("--ry", "0deg");
    });
  }

  fetch(`/api/share/${encodeURIComponent(token)}`)
    .then((r) => (r.ok ? r.json() : Promise.reject()))
    .then((j) => {
      if (!j.ok) throw new Error();
      render(j.share);
    })
    .catch(() => {
      root.innerHTML = `<div class="share-missing">
        <h2>This grade link has expired or doesn't exist.</h2>
        <a class="btn primary" href="/">Grade a card →</a>
      </div>`;
    });
})();
