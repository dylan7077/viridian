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

  // colour helpers (keep sharecard.py in visual sync)
  function hexRgb(h) { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
  function rgba(hex, a) { const [r, g, b] = hexRgb(hex); return `rgba(${r},${g},${b},${a})`; }
  function mix(hex, f) { const [r, g, b] = hexRgb(hex), bg = [12, 24, 19];
    return `rgb(${[r, g, b].map((c, i) => Math.round(c * f + bg[i] * (1 - f))).join(",")})`; }

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

    // ── background: deep green gradient + edge vignette ──
    const bg = ctx.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, "#091711");
    bg.addColorStop(1, "#0c1813");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    const vg = ctx.createRadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, W * 0.62);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.30)");
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, W, H);

    // geometry
    const ch = 486, cw = Math.round((ch * 5) / 7), cx = 66, cy = 72, rad = 16;
    const x0 = cx + cw + 74;
    const colW = W - x0 - 60;
    const gc = gradeColor(norm.grade);
    const sealCx = x0 + 92, sealCy = 250, R = 90;

    // grade-coloured glow behind the seal + soft accent halo behind the card
    function radial(px, py, r, color, a) {
      const g = ctx.createRadialGradient(px, py, 0, px, py, r);
      g.addColorStop(0, rgba(color, a));
      g.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }
    radial(sealCx, sealCy, 320, gc, 0.2);
    radial(cx + cw / 2, cy + ch / 2, 360, ACCENT, 0.07);

    // ── card art: shadow + slab bezel + hairline ──
    if (norm.image) {
      try {
        const img = await loadImg(norm.image);
        ctx.save();
        ctx.shadowColor = "rgba(0,0,0,0.6)";
        ctx.shadowBlur = 44;
        ctx.shadowOffsetY = 20;
        roundRect(ctx, cx, cy, cw, ch, rad);
        ctx.fillStyle = "#000";
        ctx.fill();
        ctx.restore();
        ctx.save();
        roundRect(ctx, cx, cy, cw, ch, rad);
        ctx.clip();
        const ar = img.width / img.height, box = cw / ch;
        let dw = cw, dh = ch, dx = cx, dy = cy;
        if (ar > box) { dh = ch; dw = ch * ar; dx = cx - (dw - cw) / 2; }
        else { dw = cw; dh = cw / ar; dy = cy - (dh - ch) / 2; }
        ctx.drawImage(img, dx, dy, dw, dh);
        ctx.restore();
        ctx.strokeStyle = rgba(gc, 0.47);      // slab bezel, grade-tinted
        ctx.lineWidth = 2;
        roundRect(ctx, cx - 13, cy - 13, cw + 26, ch + 26, rad + 8);
        ctx.stroke();
        ctx.strokeStyle = "rgba(255,255,255,0.13)";  // inner hairline
        ctx.lineWidth = 2;
        roundRect(ctx, cx, cy, cw, ch, rad);
        ctx.stroke();
      } catch (e) { /* image unavailable — text panel still renders */ }
    }

    // ── brand lockup ──
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillStyle = ACCENT;
    ctx.fillRect(x0, 98, 13, 13);
    ctx.fillStyle = INK;
    ctx.font = "700 24px 'Space Grotesk', sans-serif";
    ctx.fillText("VIRIDIAN", x0 + 26, 112);
    ctx.fillStyle = ACCENT;
    ctx.font = "700 17px 'Space Mono', monospace";
    ctx.fillText("GRADING LAB", x0 + 152, 112);

    // ── grade seal ──
    function circle(r) { ctx.beginPath(); ctx.arc(sealCx, sealCy, r, 0, Math.PI * 2); }
    circle(R); ctx.fillStyle = rgba(gc, 0.09); ctx.fill();
    circle(R); ctx.strokeStyle = gc; ctx.lineWidth = 5; ctx.stroke();
    circle(R - 12); ctx.strokeStyle = mix(gc, 0.45); ctx.lineWidth = 2; ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = MUTE; ctx.font = "700 20px 'Space Mono', monospace";
    ctx.fillText("PSA", sealCx, sealCy - 50);
    const numStr = norm.grade == null ? "—" : String(norm.grade);
    ctx.fillStyle = gc; ctx.font = "700 112px 'Space Grotesk', sans-serif";
    ctx.fillText(numStr, sealCx + (numStr === "—" ? 0 : 2), sealCy + 15);
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";

    const tx = sealCx + R + 34;
    ctx.fillStyle = INK; ctx.font = "600 44px 'Space Grotesk', sans-serif";
    const gname = norm.slab ? "Graded slab" : (GRADE_NAMES[norm.grade] || "Ungraded");
    ctx.fillText(fitText(ctx, gname, x0 + colW - tx), tx, sealCy - 6);
    ctx.fillStyle = MUTE; ctx.font = "700 18px 'Space Mono', monospace";
    ctx.fillText(norm.slab ? "VERIFIED GRADE" : "ESTIMATED GRADE", tx, sealCy + 34);

    // ── divider ──
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, 372); ctx.lineTo(x0 + colW, 372); ctx.stroke();

    // ── card identity ──
    ctx.fillStyle = INK; ctx.font = "600 46px 'Space Grotesk', sans-serif";
    ctx.fillText(fitText(ctx, norm.name || "Pokémon card", colW), x0, 418);
    if (norm.sub) {
      ctx.fillStyle = MUTE; ctx.font = "500 26px 'Inter', sans-serif";
      ctx.fillText(fitText(ctx, norm.sub, colW), x0, 452);
    }

    // ── subgrade chips ──
    let chipX = x0;
    const chipY = 476;
    ctx.font = "700 22px 'Space Mono', monospace";
    (norm.subs || []).forEach(([label, val]) => {
      const txt = `${label} ${val == null ? "—" : val}`;
      const w = ctx.measureText(txt).width + 30;
      if (chipX + w > x0 + colW) return;
      roundRect(ctx, chipX, chipY, w, 40, 11);
      ctx.fillStyle = "rgba(255,255,255,0.06)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.12)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#d2dcd6";
      ctx.fillText(txt, chipX + 15, chipY + 27);
      chipX += w + 11;
    });

    // ── value block ──
    if (norm.gradedUsd != null || norm.rawUsd != null) {
      if (norm.gradedUsd != null) {
        ctx.fillStyle = MUTE; ctx.font = "700 17px 'Space Mono', monospace";
        ctx.fillText(norm.gradedReal ? "GRADED VALUE" : "PSA 10 VALUE (EST.)", x0, 536);
        ctx.fillStyle = gc; ctx.font = "700 46px 'Space Grotesk', sans-serif";
        ctx.fillText(money("$", norm.gradedUsd), x0, 580);
      }
      if (norm.rawUsd != null) {
        const rx = x0 + 322;
        ctx.fillStyle = MUTE; ctx.font = "700 17px 'Space Mono', monospace";
        ctx.fillText("RAW", rx, 536);
        ctx.fillStyle = INK; ctx.font = "600 30px 'Inter', sans-serif";
        ctx.fillText(money("$", norm.rawUsd), rx, 578);
      }
    }

    // ── footer ──
    ctx.fillStyle = mix(MUTE, 0.85); ctx.font = "500 21px 'Inter', sans-serif";
    ctx.fillText("Grade any card free at viridian", x0, 614);

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
