// Viridian Grading Lab — front-end logic
const $ = (s) => document.querySelector(s);
document.getElementById("year").textContent = new Date().getFullYear();

const CARD_W = 500, CARD_H = 700;            // canonical warp size (matches backend)
const GRADE_NAMES = {
  10: "Gem Mint", 9: "Mint", 8: "NM-MT", 7: "Near Mint", 6: "EX-MT",
  5: "Excellent", 4: "VG-EX", 3: "Very Good", 2: "Good", 1: "Poor",
};

const fileInput = $("#file");
const dropzone = $("#dropzone");
const preview = $("#preview");
const analyzeBtn = $("#analyze");
const resultPanel = $("#result");
const resultBody = $("#result-body");
let selectedFile = null;

// ---- back-of-card photo (required) ----------------------------------------
let backFile = null;
const backSection = $("#backSection");
const backInput = $("#backFile");
const backDropzone = $("#backDropzone");
const backPreview = $("#backPreview");
const backTakeBtn = $("#backTakePhoto");

function updateAnalyzeEnabled() {
  // Back photo is encouraged (two-sided PSA-style grade) but never blocks a grade.
  analyzeBtn.disabled = !selectedFile;
}
function setBackFile(file) {
  if (!file || !file.type.startsWith("image/")) return;
  backFile = file;
  if (backPreview) { backPreview.src = URL.createObjectURL(file); backPreview.hidden = false; }
  updateAnalyzeEnabled();
}
if (backInput) backInput.addEventListener("change", (e) => setBackFile(e.target.files[0]));
if (backTakeBtn) backTakeBtn.addEventListener("click", () => {
  if (window.Camera) Camera.open(setBackFile); else backInput.click();
});
if (backDropzone) {
  ["dragenter", "dragover"].forEach((ev) =>
    backDropzone.addEventListener(ev, (e) => { e.preventDefault(); backDropzone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    backDropzone.addEventListener(ev, (e) => { e.preventDefault(); backDropzone.classList.remove("drag"); }));
  backDropzone.addEventListener("drop", (e) => setBackFile(e.dataTransfer.files[0]));
}

// ---- lab status bar -------------------------------------------------------
const DASH = "—";

function paintNum(el, n) {
  el.textContent = n.toLocaleString();
}

function animateNum(el, from, to, dur = 650) {
  const start = performance.now();
  (function tick(now) {
    const k = Math.min((now - start) / dur, 1);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = Math.round(from + (to - from) * e).toLocaleString();
    if (k < 1) requestAnimationFrame(tick);
  })(start);
}

(function liveCount() {
  let prev = null;                       // null = never painted yet
  let offline = false;
  const els = [$("#stat-cards"), $("#stat-cards-row")].filter(Boolean);

  async function poll() {
    try {
      const h = await (await fetch("/api/health")).json();
      const n = Number(h.indexed ?? h.index_size ?? 0) || 0;
      offline = false;
      document.body.classList.remove("engine-offline");

      if (prev === null) {
        // first paint — show real value immediately, no counting from 0
        els.forEach((el) => paintNum(el, n));
      } else if (n !== prev) {
        els.forEach((el) => animateNum(el, prev, n));
      }
      prev = n;
    } catch {
      if (!offline) {
        offline = true;
        document.body.classList.add("engine-offline");
        els.forEach((el) => { el.textContent = DASH; });
      }
    }
  }

  poll();
  setInterval(poll, 4000);
})();

// ---- upload ---------------------------------------------------------------
function setFile(file) {
  if (!file || !file.type.startsWith("image/")) return;
  selectedFile = file;
  if (backSection) backSection.hidden = false;   // now ask for the back
  updateAnalyzeEnabled();                         // stays disabled until back is added too
  if (window.Crop) {
    Crop.load(file);                 // open the manual corner-align tool
  } else {
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
  }
}
fileInput.addEventListener("change", (e) => setFile(e.target.files[0]));

// "Take a photo" → in-app camera with a card-outline guide; the capture is already
// cropped to the template, so it drops straight into the same flow as an upload.
const takePhotoBtn = $("#takePhoto");
if (takePhotoBtn) {
  takePhotoBtn.addEventListener("click", () => {
    if (window.Camera) Camera.open(setFile);
    else fileInput.click();
  });
}
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));
analyzeBtn.addEventListener("click", analyze);

async function analyze() {
  if (!selectedFile) return;
  resultPanel.hidden = false;
  resultBody.innerHTML = `
    <div class="scanner"><img src="${preview.src}" alt="" /><div class="scan-line"></div></div>
    <p class="scan-label">ANALYZING SPECIMEN…</p>
    <p class="sg-note" style="text-align:center;margin-top:6px">Matching against the card database — this may take a few moments the first time a card is seen.</p>`;
  resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });

  const fd = new FormData();
  fd.append("file", selectedFile);
  const cn = window.Crop && Crop.corners();
  if (cn) fd.append("corners", JSON.stringify(cn));
  if (backFile) fd.append("back_file", backFile);
  try {
    const res = await fetch("/api/grade", { method: "POST", body: fd });
    render(await res.json());
  } catch (err) {
    resultBody.innerHTML = `<div class="banner error">Request failed: ${err}</div>`;
  }
}

// ---- animation helpers ----------------------------------------------------
function countUp(el, target, dur = 900, fmt = (v) => Math.round(v)) {
  if (target == null) { el.textContent = "—"; return; }
  const start = performance.now();
  (function tick(t) {
    const k = Math.min((t - start) / dur, 1);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = fmt(target * eased);
    if (k < 1) requestAnimationFrame(tick);
  })(start);
}

// ---- render ---------------------------------------------------------------
function bar(label, grade, heuristic, note) {
  return `
    <div class="subgrade">
      <span class="sg-label">${label}</span>
      <div class="sg-bar"><div class="sg-fill ${heuristic ? "heur" : ""}" data-pct="${grade ? grade * 10 : 0}"></div></div>
      <span class="sg-val">${grade ?? "—"}</span>
      ${note ? `<span class="sg-note">${note}</span>` : ""}
    </div>`;
}

function buildVerdict(r, g, c, overall) {
  const proChips = [];
  const conChips = [];

  // collect evidence as chips
  let centeringPro = false, centeringCon = false;
  if (c.ok) {
    const lr = (c.left_right || "").split("/").map(Number);
    const tb = (c.top_bottom || "").split("/").map(Number);
    const lrOff = lr.length === 2 ? Math.abs(lr[0] - lr[1]) : 999;
    const tbOff = tb.length === 2 ? Math.abs(tb[0] - tb[1]) : 999;
    proChips.push(`L/R ${c.left_right}`);
    proChips.push(`T/B ${c.top_bottom}`);
    if (lrOff > 10 || tbOff > 10) centeringCon = true;
    else centeringPro = true;
  } else {
    conChips.push("centering n/a");
  }

  const pickGrade = (obj) => obj && obj.grade != null ? obj.grade : null;
  const corners = pickGrade(g.corners);
  const edges   = pickGrade(g.edges);
  const surface = pickGrade(g.surface);
  if (corners != null) proChips.push(`corners ${corners}`);
  if (edges != null)   proChips.push(`edges ${edges}`);
  if (surface != null) proChips.push(`surface ${surface}`);

  // Wear that counts toward the grade = surface only (corners/edges are non-predictive
  // noise on single photos — see FINDINGS.md — so they don't drive the verdict either).
  const wear = (surface != null && surface <= 6) ? 1 : 0;
  if (wear > 0) conChips.push(`surface wear`);

  if (overall >= 9) proChips.push(`overall ${overall}`);
  else if (overall != null && overall <= 6) conChips.push(`overall ${overall}`);

  if (r.match && r.match.card) proChips.push(r.match.card.name);
  const mconf = r.match && (r.match.confidence != null ? r.match.confidence : r.match.score);
  if (mconf != null) {
    const pct = Math.round(mconf * 100);
    if (mconf >= 0.75) proChips.push(`match ${pct}%`);
    else if (mconf < 0.5) conChips.push(`match ${pct}%`);
  }

  if (r.value && r.value.ok && r.value.values && r.value.values[0]) {
    const v0 = r.value.values[0];
    const val = v0.graded ?? v0.raw;
    if (val != null) {
      const s = `${v0.symbol}${val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (val >= 100) proChips.push(s);
      else if (val < 10) conChips.push(s);
    }
  }

  if (r.capture_warning) conChips.push("photo quality");
  if (r.detection_warning) conChips.push("detection warn");
  if (r.match_warning) conChips.push("match warn");

  // compose the one-line verdicts
  let proLine, conLine;
  if (overall >= 9 && centeringPro && wear === 0) {
    proLine = `A clean copy — this one grades up there with the best of them.`;
  } else if (overall >= 8 && centeringPro) {
    proLine = `Strong overall. Centering is the headline strength.`;
  } else if (centersPro_helper(centeringPro, overall)) {
    proLine = `The numbers look solid — a respectable submission.`;
  } else if (proChips.some(c => /match \d+%/.test(c) && parseInt(c.match(/\d+/)[0]) >= 75)) {
    proLine = `Confidently identified — the rest is judgment call territory.`;
  } else {
    proLine = `No major red flags in the scan.`;
  }

  if (overall != null && overall <= 5) {
    conLine = `Real wear here — surface condition and centering are dragging the grade down.`;
  } else if (centeringCon) {
    conLine = `Off-center print is the grade-killer here.`;
  } else if (wear > 0) {
    conLine = `Surface wear is the main thing holding the grade back.`;
  } else if (conChips.length) {
    conLine = `A couple of things worth a closer look.`;
  } else {
    conLine = `Honestly, nothing major to flag.`;
  }

  return { proLine, conLine, proChips, conChips };
}

function centersPro_helper(centeringPro, overall) {
  return centeringPro && overall != null && overall >= 7;
}

function centeringDiagram(c) {
  if (!c.ok || !c.borders_px) return "";
  const b = c.borders_px;
  const L = (b.left / CARD_W) * 100, R = (b.right / CARD_W) * 100;
  const T = (b.top / CARD_H) * 100, B = (b.bottom / CARD_H) * 100;

  // parse L/R and T/B ratios like "62/38" → numbers
  const lr = (c.left_right || "").split("/").map(Number);
  const tb = (c.top_bottom || "").split("/").map(Number);
  const lrOff = lr.length === 2 ? Math.abs(lr[0] - lr[1]) : 0;
  const tbOff = tb.length === 2 ? Math.abs(tb[0] - tb[1]) : 0;

  // 200x280 viewBox = 5:7 card aspect
  const VBW = 200, VBH = 280;
  const cx = VBW / 2, cy = VBH / 2;

  // measured art-box in viewBox units
  const ax = (L / 100) * VBW;
  const ay = (T / 100) * VBH;
  const aw = VBW - ax - (R / 100) * VBW;
  const ah = VBH - ay - (B / 100) * VBH;
  const artCx = ax + aw / 2;
  const artCy = ay + ah / 2;

  // PSA 60/40 target box
  const tL = 0.20 * VBW, tR = 0.80 * VBW, tT = 0.20 * VBH, tB = 0.80 * VBH;

  // lean vector from card center to art center
  const dx = artCx - cx, dy = artCy - cy;
  const leanLen = Math.min(Math.hypot(dx, dy), 38);
  const leanAngle = Math.atan2(dy, dx) * 180 / Math.PI;
  const leanEx = cx + leanLen * Math.cos(leanAngle * Math.PI / 180);
  const leanEy = cy + leanLen * Math.sin(leanAngle * Math.PI / 180);

  // a small chevron at the tip of the lean arrow
  const a = (leanAngle * Math.PI) / 180;
  const tip1x = leanEx - 6 * Math.cos(a - 0.4);
  const tip1y = leanEy - 6 * Math.sin(a - 0.4);
  const tip2x = leanEx - 6 * Math.cos(a + 0.4);
  const tip2y = leanEy - 6 * Math.sin(a + 0.4);

  const psaOk = (lrOff <= 10 && tbOff <= 10) ? "ok" : "miss";

  return `
    <div class="cen-diagram">
      <div class="cen-vis">
        <svg viewBox="0 0 ${VBW} ${VBH}" class="cen-svg" aria-label="Centering visualization">
          <defs>
            <pattern id="cv-grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>
            </pattern>
          </defs>
          <rect width="${VBW}" height="${VBH}" fill="url(#cv-grid)" />
          <rect class="cv-card" x="2" y="2" width="${VBW-4}" height="${VBH-4}" rx="4" />
          <rect class="cv-target ${psaOk}" x="${tL}" y="${tT}"
                width="${tR - tL}" height="${tB - tT}" rx="2" />
          <rect class="cv-art" x="${ax}" y="${ay}" width="${aw}" height="${ah}" rx="2" />
          <line class="cv-cross" x1="0" y1="${cy}" x2="${VBW}" y2="${cy}" />
          <line class="cv-cross" x1="${cx}" y1="0" x2="${cx}" y2="${VBH}" />
          <circle class="cv-origin" cx="${cx}" cy="${cy}" r="2.5" />
          <line class="cv-lean" x1="${cx}" y1="${cy}" x2="${leanEx}" y2="${leanEy}" />
          <polygon class="cv-lean" points="${leanEx},${leanEy} ${tip1x},${tip1y} ${tip2x},${tip2y}" />
          <circle class="cv-dot" cx="${artCx}" cy="${artCy}" r="3" />
        </svg>
      </div>
      <div class="cen-readout">
        <div class="cen-row big">
          <span class="cen-grade">${c.grade ?? "—"}</span>
          <span class="cen-grade-label">centering</span>
        </div>
        <div class="cen-row"><span>L/R</span><b>${c.left_right || "—"}</b><span class="cen-delta">&Delta; ${lrOff}%</span></div>
        <div class="cen-row"><span>T/B</span><b>${c.top_bottom || "—"}</b><span class="cen-delta">&Delta; ${tbOff}%</span></div>
        <div class="cen-bar">
          <div class="cen-bar-track">
            <div class="cen-bar-mark psa-min" style="left:20%"></div>
            <div class="cen-bar-mark psa-max" style="left:80%"></div>
            <div class="cen-bar-fill ${psaOk}" style="width:${Math.min(100, (lrOff + tbOff) * 2.5)}%"></div>
          </div>
          <span class="cen-bar-label">${psaOk === "ok" ? "within PSA 60/40" : "outside PSA 60/40"}</span>
        </div>
      </div>
    </div>`;
}

function render(r) {
  if (!r.ok) {
    resultBody.innerHTML = `<div class="banner error">${r.message || "Could not grade."}</div>`;
    return;
  }
  const g = r.grade;
  const slabbed = r.slab && r.slab.grade != null;
  const overall = slabbed ? r.slab.grade : g.overall;
  const cls = overall >= 9 ? "gold" : overall >= 7 ? "" : "low";
  const c = g.centering;
  const C = 2 * Math.PI * 54;                 // seal ring circumference

  let html = `<div class="report-reveal">
    <div class="grade-hero">
      <div class="seal ${cls}">
        <svg class="seal-ring" viewBox="0 0 132 132">
          <circle class="ring-bg" cx="66" cy="66" r="54"/>
          <circle class="ring-fg" cx="66" cy="66" r="54"
                  stroke-dasharray="${C}" stroke-dashoffset="${C}"/>
        </svg>
        <div class="seal-inner">
          <span class="seal-psa">PSA</span>
          <span class="seal-num">0</span>
        </div>
      </div>
      <div class="grade-meta">
        <h3>${slabbed ? "PSA " + overall : (GRADE_NAMES[overall] || "Ungraded")}</h3>
        <p>${slabbed ? "Graded slab" + (r.slab.cert ? " · cert " + r.slab.cert : "") : "Estimated overall grade"}</p>
      </div>
    </div>`;
  if (slabbed) html += `<div class="banner info">Graded slab detected — showing PSA's printed grade (${r.slab.grade_label || ""} ${overall}). The sub-grades below are the raw-card estimate through the case.</div>`;

  if (r.overlay) {
    html += `<figure class="overlay-fig">
      <img class="overlay-img" src="${r.overlay}" alt="Detection analysis" />
      <figcaption>Detection &amp; centering — green frame = measured borders ·
        gold boxes = corner samples · shaded = edge samples</figcaption>
    </figure>`;
  }

  if (r.capture_warning) html += `<div class="banner warn">📷 ${r.capture_warning} A clearer photo grades more accurately.</div>`;
  if (r.detection_warning) html += `<div class="banner warn">${r.detection_warning}</div>`;
  if (r.match_warning) html += `<div class="banner warn">${r.match_warning}</div>`;

  if (c.ok) html += centeringDiagram(c);

  html += `<div class="subgrades">
    ${bar("Centering", c.ok ? c.grade : null, false, c.ok ? (c.confidence === "low" ? "low confidence — reshoot flat" : "measured · counts") : "could not measure")}
    ${bar("Surface", g.surface.grade, true, "rough · counts")}
    ${bar("Corners", g.corners.grade, true, "experimental")}
    ${bar("Edges", g.edges.grade, true, "experimental")}
  </div>
  <p class="sg-note" style="margin-bottom:18px">The grade is driven by <b>centering</b> (measured) and <b>surface</b> (validated on real cards). Corners &amp; edges are shown for reference only — single-photo heuristics aren't reliable enough to count toward the grade.</p>`;

  // ── Back of card (when a back photo was graded) ──────────────────────────
  const gb = g.back;
  if (gb && !slabbed) {
    const bc = gb.centering || {};
    html += `<div class="back-report">
      <h4 class="back-report-h">🂠 Back of card</h4>`;
    if (r.back_overlay) {
      html += `<figure class="overlay-fig">
        <img class="overlay-img" src="${r.back_overlay}" alt="Back analysis" />
        <figcaption>Back detection &amp; centering — whitening pops against the blue back</figcaption>
      </figure>`;
    }
    if (r.back_capture_warning) html += `<div class="banner warn">📷 ${r.back_capture_warning} A clearer back photo grades more accurately.</div>`;
    html += `<div class="subgrades">
      ${bar("Centering", bc.ok ? bc.grade : null, false, bc.ok ? "measured · counts" : "could not align the back")}
      ${bar("Surface", gb.surface.grade, true, "rough · counts")}
      ${bar("Corners", gb.corners.grade, true, "whitening")}
      ${bar("Edges", gb.edges.grade, true, "whitening")}
    </div>`;
    if (g.front_overall != null && gb.overall != null) {
      const worse = gb.overall < g.front_overall ? " (the back)" : (g.front_overall < gb.overall ? " (the front)" : "");
      html += `<p class="sg-note" style="margin-bottom:18px">Front grades <b>${g.front_overall}</b>, back grades <b>${gb.overall}</b> — your overall <b>${overall}</b> is the worse side${worse}.</p>`;
    }
    html += `</div>`;
  } else if (!slabbed) {
    html += `<p class="sg-note">🂠 Front-only grade — add a <b>back photo</b> for the full two-sided grade (back whitening is where most cards lose it).</p>`;
  }

  if (r.match) {
    const card = r.match.card;
    const isHolo = /holo|rare|illustration|ex|gx|secret/i.test(card.rarity || "");
    html += `
      <div class="holo-card ${isHolo ? "holo-on" : ""}" id="holo"><img src="${card.image}" alt="${card.name}" /></div>
      <p class="card-caption"><strong>${card.name}</strong> · ${card.set} #${card.number}
        ${card.rarity ? "· " + card.rarity : ""}
        ${r.match.confidence != null ? `<span class="conf-chip">🎯 ${Math.round(r.match.confidence * 100)}% match</span>` : ""}</p>`;
    if (r.match.print_uncertain) {
      html += `<p class="sg-note">⚠️ This artwork exists in several sets — the exact print (and its price) may differ.</p>`;
    }
  }

  const v = r.value;
  if (v && v.ok && v.values && v.values.length) {
    const graded = v.values[0].graded != null;
    const real = v.graded_real === true;
    const note = !graded ? "Raw market value"
      : real ? `Real graded price · ${v.graded_source || "PSA sold data"}`
      : `Estimated value at grade ${overall} · no graded sales data, estimate shown`;
    html += `<p class="sg-note" style="margin:14px 0 4px">${note} · GBP first</p>`;
    // GBP leads — it's the number a UK user acts on.
    const ordered = [...v.values].sort((a, b) => (a.currency !== "GBP") - (b.currency !== "GBP"));
    ordered.forEach((x, i) => {
      const val = graded ? x.graded : x.raw;
      const src = x.source ? ` <span class="v-src">${x.source}</span>` : "";
      const vbCls = i === 0 ? " value-big " + (overall >= 9 ? "vb-gold" : overall >= 7 ? "" : "vb-low") : "";
      html += `<div class="kv"><span class="k">${x.currency}</span>
        <span class="v${vbCls}">${x.symbol}${val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}${src}</span></div>`;
    });
  }

  // share bar — only for confidently identified cards (a link + a clean image)
  if (r.share_token) {
    html += `
      <div class="share-bar" id="shareBar">
        <span class="share-head">Share your pull</span>
        <div class="share-btns">
          <button type="button" class="share-btn" id="shareLink">
            <span class="share-ic">🔗</span> Copy link
          </button>
          <button type="button" class="share-btn primary" id="shareImg">
            <span class="share-ic">📸</span> Save image
          </button>
        </div>
      </div>`;
  }

  // pros / cons — verdict-style: one bold sentence per side + chip row of evidence
  const verdict = buildVerdict(r, g, c, overall);
  html += `
    <div class="verdict">
      <div class="v-col v-pro">
        <div class="v-head">
          <span class="v-mark">+</span>
          <span class="v-label">The good</span>
        </div>
        <p class="v-line">${verdict.proLine}</p>
        <div class="v-chips">
          ${verdict.proChips.map(t => `<span class="v-chip v-chip-pro">${t}</span>`).join("")}
        </div>
      </div>
      <div class="v-col v-con">
        <div class="v-head">
          <span class="v-mark">&minus;</span>
          <span class="v-label">The catch</span>
        </div>
        <p class="v-line">${verdict.conLine}</p>
        <div class="v-chips">
          ${verdict.conChips.map(t => `<span class="v-chip v-chip-con">${t}</span>`).join("")}
        </div>
      </div>
    </div>`;

  html += `</div>`;
  resultBody.innerHTML = html;

  // ---- animate after insertion ----
  requestAnimationFrame(() => {
    countUp(resultBody.querySelector(".seal-num"), overall, 1000);
    const ring = resultBody.querySelector(".ring-fg");
    if (ring && overall) ring.style.strokeDashoffset = `${C * (1 - overall / 10)}`;
    resultBody.querySelectorAll(".sg-fill").forEach((el) => {
      el.style.width = `${el.dataset.pct}%`;
    });
    if (v && v.ok && v.graded_estimate != null) {
      const cur = v.currency || "$";
      countUp($("#value-num"), v.graded_estimate, 1100, (x) => cur + x.toFixed(2));
    }
  });
  wireHolo();
  wireShare(r);
}

// ---- share actions --------------------------------------------------------
function wireShare(r) {
  if (!r.share_token || !window.Viridian) return;
  const link = r.share_url || `${location.origin}/g/${r.share_token}`;
  const norm = Viridian.normFromResult(r);
  const linkBtn = document.getElementById("shareLink");
  const imgBtn = document.getElementById("shareImg");
  if (linkBtn) {
    linkBtn.addEventListener("click", async () => {
      const ok = await Viridian.copy(link);
      Viridian.toast(ok ? "Link copied — paste it anywhere" : link);
    });
  }
  if (imgBtn) {
    imgBtn.addEventListener("click", async () => {
      imgBtn.disabled = true;
      const old = imgBtn.innerHTML;
      imgBtn.innerHTML = `<span class="share-ic">⏳</span> Building…`;
      await Viridian.saveOrShare(norm, link);
      imgBtn.disabled = false;
      imgBtn.innerHTML = old;
    });
  }
}

// pointer-reactive holo / tilt — inspired by simeydotme/pokemon-cards-css
function wireHolo() {
  const card = document.getElementById("holo");
  if (!card) return;
  card.addEventListener("pointermove", (e) => {
    const b = card.getBoundingClientRect();
    const px = (e.clientX - b.left) / b.width, py = (e.clientY - b.top) / b.height;
    card.style.setProperty("--active", "1");
    card.style.setProperty("--mx", `${px * 100}%`);
    card.style.setProperty("--my", `${py * 100}%`);
    card.style.setProperty("--bx", `${px * 100}%`);
    card.style.setProperty("--by", `${py * 100}%`);
    card.style.setProperty("--rx", `${(px - 0.5) * 24}deg`);
    card.style.setProperty("--ry", `${(0.5 - py) * 24}deg`);
  });
  card.addEventListener("pointerleave", () => {
    card.style.setProperty("--active", "0");
    card.style.setProperty("--rx", "0deg");
    card.style.setProperty("--ry", "0deg");
  });
}
