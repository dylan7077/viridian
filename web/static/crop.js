// Manual corner-align tool. Lets the user drag 4 handles onto the card's
// corners so the backend can warp a perfect crop. Exposes window.Crop.
(function () {
  const dz = document.getElementById("dropzone");
  const crop = document.getElementById("crop");
  const stage = document.getElementById("crop-stage");
  const poly = document.getElementById("crop-poly");
  const img = document.getElementById("preview");
  const handles = [...document.querySelectorAll(".crop-handle")];
  const detecting = document.getElementById("cropDetecting");
  if (!stage || !poly || handles.length !== 4) return;

  // corners as percentages (0–100) of the displayed image, order TL,TR,BR,BL
  const DEFAULT = [{ x: 7, y: 5 }, { x: 93, y: 5 }, { x: 93, y: 95 }, { x: 7, y: 95 }];
  let pts = null;
  let used = false;
  let active = -1;

  function draw() {
    poly.setAttribute("points", pts.map((p) => `${p.x},${p.y}`).join(" "));
    handles.forEach((h, i) => { h.style.left = pts[i].x + "%"; h.style.top = pts[i].y + "%"; });
  }
  function reset() { pts = DEFAULT.map((p) => ({ ...p })); draw(); }

  function load(file) {
    img.src = URL.createObjectURL(file);
    dz.hidden = true;
    crop.hidden = false;
    used = true;
    if (img.complete) reset();
    else img.onload = reset;
    seed(file);                        // auto-detect corners, then fine-tune
  }

  function showDetecting(on) {
    if (detecting) detecting.hidden = !on;
  }

  async function seed(file) {
    showDetecting(true);                 // spinner + "Auto-framing…" while we detect corners
    try {
      const fd = new FormData();
      fd.append("file", file);
      const d = await (await fetch("/api/detect", { method: "POST", body: fd })).json();
      if (active < 0 && d && Array.isArray(d.corners) && d.corners.length === 4) {
        pts = d.corners.map((c) => ({
          x: Math.max(0, Math.min(100, c[0] * 100)),
          y: Math.max(0, Math.min(100, c[1] * 100)),
        }));
        draw();
      }
    } catch (_) { /* keep default box */ }
    finally { showDetecting(false); }
  }
  function retake() { used = false; crop.hidden = true; dz.hidden = false; }
  function corners() {
    return used && pts ? pts.map((p) => [p.x / 100, p.y / 100]) : null;
  }

  function pctFromEvent(e) {
    const r = stage.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(100, ((e.clientX - r.left) / r.width) * 100)),
      y: Math.max(0, Math.min(100, ((e.clientY - r.top) / r.height) * 100)),
    };
  }

  handles.forEach((h, i) => {
    h.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      active = i;
      h.setPointerCapture(e.pointerId);
    });
  });
  window.addEventListener("pointermove", (e) => {
    if (active < 0) return;
    pts[active] = pctFromEvent(e);
    draw();
  });
  window.addEventListener("pointerup", () => { active = -1; });

  document.getElementById("crop-reset").addEventListener("click", reset);
  document.getElementById("crop-retake").addEventListener("click", retake);

  window.Crop = { load, corners, retake };
})();

// Deep link from the Discord bot: /?align=<token> pre-loads that photo straight
// into the corner-align tool, so you can drag the corners and grade it here.
(function () {
  const token = new URLSearchParams(location.search).get("align");
  if (!token || typeof setFile !== "function") return;
  fetch(`/api/align-image/${encodeURIComponent(token)}`)
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error("not found"))))
    .then((blob) => {
      const file = new File([blob], "card.jpg", { type: blob.type || "image/jpeg" });
      setFile(file);                       // opens the crop tool with corners auto-seeded
      const crop = document.getElementById("crop");
      if (crop) crop.scrollIntoView({ behavior: "smooth", block: "center" });
    })
    .catch(() => { /* token expired or missing — user can just upload again */ });
})();
