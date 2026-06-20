// Shareable grade card: composites a clean PNG (card + grade + value) on a <canvas>
// and offers it via the native share sheet or a download. Shared by the result panel
// (app.js) and the public share page (share-page.js).
(function () {
  const V = (window.Viridian = window.Viridian || {});

  const ACCENT = "#2dd4a0", GOLD = "#e8c878", MUTE = "#9fb0a9", INK = "#eaf2ee";
  const gradeColor = (g) => (g == null ? MUTE : g >= 9 ? GOLD : g >= 7 ? ACCENT : "#d6ded9");
  const GRADE_NAMES = {
    10: "Gem Mint", 9: "Mint", 8: "NM-MT", 7: "Near Mint", 6: "EX-MT",
    5: "Excellent", 4: "VG-EX", 3: "Very Good", 2: "Good", 1: "Poor",
  };

  function money(sym, n) {
    return sym + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // ---- toast + clipboard -------------------------------------------------
  V.toast = function (msg) {
    let t = document.getElementById("v-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "v-toast";
      t.className = "v-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove("show"), 2200);
  };

  V.copy = async function (text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        return true;
      } catch (e2) {
        return false;
      }
    }
  };

  // ---- canvas helpers ----------------------------------------------------
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function fitText(ctx, text, max) {
    if (ctx.measureText(text).width <= max) return text;
    let s = text;
    while (s.length > 1 && ctx.measureText(s + "…").width > max) s = s.slice(0, -1);
    return s + "…";
  }

  function loadImg(url) {
    return new Promise((res, rej) => {
      const i = new Image();
      i.crossOrigin = "anonymous";
      i.onload = () => res(i);
      i.onerror = rej;
      // route card art through our same-origin proxy so the canvas stays exportable
      i.src = "/api/proxy-image?u=" + encodeURIComponent(url);
    });
  }

  // ---- the composite -----------------------------------------------------
  // norm: { name, sub, grade, slab, image, rawUsd, gradedUsd, gradedReal, subs:[[label,val]] }
  V.shareCard = async function (norm) {
    try { await document.fonts.ready; } catch (e) {}
    const W = 1200, H = 630;
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");

    // background
    const bg = ctx.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, "#0a130f");
    bg.addColorStop(1, "#0f1c17");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    // accent glow top-left
    const glow = ctx.createRadialGradient(120, 90, 0, 120, 90, 520);
    glow.addColorStop(0, "rgba(45,212,160,0.16)");
    glow.addColorStop(1, "rgba(45,212,160,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, W, H);

    // card image (5:7), rounded, with shadow
    const ch = 490, cw = Math.round((ch * 5) / 7), cx = 64, cy = 70;
    if (norm.image) {
      try {
        const img = await loadImg(norm.image);
        ctx.save();
        ctx.shadowColor = "rgba(0,0,0,0.55)";
        ctx.shadowBlur = 40;
        ctx.shadowOffsetY = 18;
        roundRect(ctx, cx, cy, cw, ch, 18);
        ctx.fillStyle = "#000";
        ctx.fill();
        ctx.restore();
        ctx.save();
        roundRect(ctx, cx, cy, cw, ch, 18);
        ctx.clip();
        // cover-fit
        const ar = img.width / img.height, box = cw / ch;
        let dw = cw, dh = ch, dx = cx, dy = cy;
        if (ar > box) { dh = ch; dw = ch * ar; dx = cx - (dw - cw) / 2; }
        else { dw = cw; dh = cw / ar; dy = cy - (dh - ch) / 2; }
        ctx.drawImage(img, dx, dy, dw, dh);
        ctx.restore();
        // hairline border
        ctx.strokeStyle = "rgba(255,255,255,0.10)";
        ctx.lineWidth = 1.5;
        roundRect(ctx, cx, cy, cw, ch, 18);
        ctx.stroke();
      } catch (e) { /* image unavailable — text panel still renders */ }
    }

    const x0 = cx + cw + 56;       // right column origin
    const colW = W - x0 - 64;

    // wordmark
    ctx.fillStyle = ACCENT;
    ctx.font = "600 22px 'Space Grotesk', sans-serif";
    ctx.textBaseline = "alphabetic";
    ctx.fillText("VIRIDIAN GRADING LAB", x0, cy + 22);

    // grade headline
    const gc = gradeColor(norm.grade);
    ctx.fillStyle = MUTE;
    ctx.font = "600 26px 'Space Grotesk', sans-serif";
    ctx.fillText("PSA", x0, cy + 86);
    ctx.fillStyle = gc;
    ctx.font = "700 150px 'Space Grotesk', sans-serif";
    const numStr = norm.grade == null ? "—" : String(norm.grade);
    ctx.fillText(numStr, x0 - 4, cy + 210);
    const numW = ctx.measureText(numStr).width;
    ctx.fillStyle = INK;
    ctx.font = "600 40px 'Space Grotesk', sans-serif";
    const gname = norm.slab ? "Graded slab" : (GRADE_NAMES[norm.grade] || "Ungraded");
    ctx.fillText(gname, x0 + numW + 24, cy + 150);
    ctx.fillStyle = MUTE;
    ctx.font = "500 22px 'Inter', sans-serif";
    ctx.fillText(norm.slab ? "verified grade" : "estimated grade", x0 + numW + 26, cy + 188);

    // card name + set
    ctx.fillStyle = INK;
    ctx.font = "600 46px 'Space Grotesk', sans-serif";
    ctx.fillText(fitText(ctx, norm.name || "Pokémon card", colW), x0, cy + 300);
    if (norm.sub) {
      ctx.fillStyle = MUTE;
      ctx.font = "500 26px 'Inter', sans-serif";
      ctx.fillText(fitText(ctx, norm.sub, colW), x0, cy + 338);
    }

    // subgrade chips
    let chipX = x0;
    const chipY = cy + 366;
    ctx.font = "700 22px 'Space Mono', monospace";
    (norm.subs || []).forEach(([label, val]) => {
      const txt = `${label} ${val == null ? "—" : val}`;
      const w = ctx.measureText(txt).width + 30;
      if (chipX + w > x0 + colW) return;
      roundRect(ctx, chipX, chipY, w, 40, 12);
      ctx.fillStyle = "rgba(255,255,255,0.05)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#cdd8d2";
      ctx.fillText(txt, chipX + 15, chipY + 27);
      chipX += w + 12;
    });

    // value
    const vy = cy + 452;
    if (norm.rawUsd != null || norm.gradedUsd != null) {
      ctx.font = "500 27px 'Inter', sans-serif";
      let vx = x0;
      if (norm.rawUsd != null) {
        ctx.fillStyle = MUTE;
        ctx.fillText("Raw ", vx, vy);
        vx += ctx.measureText("Raw ").width;
        ctx.fillStyle = INK;
        const rs = money("$", norm.rawUsd);
        ctx.fillText(rs, vx, vy);
        vx += ctx.measureText(rs).width + 22;
      }
      if (norm.gradedUsd != null) {
        ctx.fillStyle = MUTE;
        const lbl = norm.gradedReal ? "Graded " : "PSA 10 est. ";
        ctx.fillText(lbl, vx, vy);
        vx += ctx.measureText(lbl).width;
        ctx.fillStyle = gc;
        ctx.font = "600 27px 'Inter', sans-serif";
        ctx.fillText(money("$", norm.gradedUsd), vx, vy);
      }
    }

    // footer url
    ctx.fillStyle = MUTE;
    ctx.font = "500 22px 'Inter', sans-serif";
    ctx.fillText("Grade your card free — viridian", x0, cy + 500);

    return cv;
  };

  function canvasBlob(cv) {
    return new Promise((res) => cv.toBlob(res, "image/png"));
  }

  V.saveOrShare = async function (norm, linkUrl) {
    let cv;
    try {
      cv = await V.shareCard(norm);
    } catch (e) {
      V.toast("Couldn't build the image");
      return;
    }
    const blob = await canvasBlob(cv);
    if (!blob) { V.toast("Couldn't build the image"); return; }
    const fname = ("viridian-" + (norm.name || "card") + (norm.grade != null ? "-PSA" + norm.grade : ""))
      .replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() + ".png";
    const file = new File([blob], fname, { type: "image/png" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({
          files: [file],
          title: "My Viridian grade",
          text: (norm.name || "My card") + (norm.grade != null ? " · PSA " + norm.grade : "") +
            (linkUrl ? "\n" + linkUrl : ""),
        });
        return;
      } catch (e) { if (e && e.name === "AbortError") return; }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    V.toast("Image saved");
  };

  // Build the normalized share object from a /api/grade result (`r`) or an
  // /api/share snapshot (`d`) — both share the same card/grade/value shape.
  function usdOf(values) {
    const u = (values || []).find((v) => v.currency === "USD" || v.symbol === "$");
    return u || {};
  }
  V.normFromResult = function (r) {
    const card = (r.match && r.match.card) || {};
    const g = r.grade || {};
    const slab = r.slab && r.slab.grade != null;
    const overall = slab ? r.slab.grade : g.overall;
    const usd = usdOf((r.value || {}).values);
    return {
      name: card.name, image: card.image, grade: overall, slab,
      sub: [card.set, card.number ? "#" + card.number : null].filter(Boolean).join(" · "),
      rawUsd: usd.raw, gradedUsd: usd.graded, gradedReal: (r.value || {}).graded_real === true,
      subs: [
        ["Cen", g.centering && g.centering.ok ? g.centering.grade : null],
        ["Cor", (g.corners || {}).grade],
        ["Edg", (g.edges || {}).grade],
        ["Sur", (g.surface || {}).grade],
      ],
    };
  };
  V.normFromShare = function (d) {
    const usd = usdOf(d.values);
    return {
      name: d.name, image: d.image, grade: d.grade, slab: d.slab,
      sub: [d.set, d.number ? "#" + d.number : null].filter(Boolean).join(" · "),
      rawUsd: usd.raw, gradedUsd: usd.graded, gradedReal: d.graded_real === true,
      subs: [
        ["Cen", d.centering && d.centering.ok ? d.centering.grade : null],
        ["Cor", d.corners], ["Edg", d.edges], ["Sur", d.surface],
      ],
    };
  };
})();
