// Viridian — in-app camera capture with a card-outline guide.
//
// Opens the rear camera, overlays a 5:7 card template, and on capture crops to the
// guide (plus a small margin) so the grader receives a centred, frame-filling,
// roughly square-on card — which is exactly what the edge detector wants. Falls back
// to the OS file picker when getUserMedia isn't available (old/in-app browsers).
//
// Usage:  Camera.open(file => setFile(file))
window.Camera = (function () {
  const CARD_RATIO = 5 / 7;            // card width / height (matches the backend warp)
  const MARGIN = 0.06;                 // expand the crop a touch so a slightly-off card isn't clipped
  const BURST_FRAMES = 6;              // frames per capture — glare moves between them, wear doesn't
  const BURST_MS = 420;                // burst window; per-pixel MIN suppresses the moving glare

  let stream = null;
  let modal = null;
  let onShot = null;
  let rafId = null;
  let alignHits = 0;            // consecutive aligned frames (debounce flicker)

  const supported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  // Map the on-screen guide rect to intrinsic video pixels. The video renders
  // object-fit:cover, so it's scaled by max() and centre-cropped — invert that.
  function guideVideoRect(video, frame) {
    const vW = video.videoWidth, vH = video.videoHeight;
    if (!vW || !vH) return null;
    const vr = video.getBoundingClientRect();
    const fr = frame.getBoundingClientRect();
    const scale = Math.max(vr.width / vW, vr.height / vH);
    const offX = (vW * scale - vr.width) / 2;
    const offY = (vH * scale - vr.height) / 2;
    const toVid = (x, y) => [(x - vr.left + offX) / scale, (y - vr.top + offY) / scale];
    const [x0, y0] = toVid(fr.left, fr.top);
    const [x1, y1] = toVid(fr.right, fr.bottom);
    return { x0, y0, x1, y1, vW, vH };
  }

  function build() {
    const m = document.createElement("div");
    m.className = "cam-modal";
    m.innerHTML = `
      <video class="cam-video" playsinline autoplay muted></video>
      <div class="cam-frame" id="camFrame"></div>
      <p class="cam-hint" id="camHint">Line the card up inside the frame</p>
      <div class="cam-controls">
        <button type="button" class="cam-btn cam-cancel" id="camCancel">Cancel</button>
        <button type="button" class="cam-shutter" id="camShutter" aria-label="Take photo"></button>
        <span class="cam-spacer"></span>
      </div>`;
    document.body.appendChild(m);
    m.querySelector("#camCancel").addEventListener("click", close);
    m.querySelector("#camShutter").addEventListener("click", capture);
    return m;
  }

  async function open(cb) {
    onShot = cb;
    if (!supported) { fallback(); return; }
    modal = build();
    document.body.classList.add("cam-open");
    const video = modal.querySelector(".cam-video");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" },
                 width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      video.srcObject = stream;
      await video.play().catch(() => {});
      startAlignLoop();
    } catch (err) {
      // permission denied or no camera — fall back to the OS picker
      close();
      fallback();
    }
  }

  // ---- live alignment feedback ---------------------------------------------
  // Cheap per-frame check: down-sample the video and, for each of the guide's four
  // edges, compare a thin band just inside vs just outside. A card filling the guide
  // produces a strong luminance step on every side (its border edge), regardless of
  // card colour or background. All four strong → "aligned" → the frame goes green.
  const SAMP_W = 160;                 // down-sample width for the check (cheap)
  const BAND = 0.045;                 // band thickness as a fraction of guide size
  const EDGE_MIN = 14;                // min inside/outside luminance gap (0..255)
  const NEED = 4;                     // frames of agreement before flipping state
  let acanvas = null, actx = null;

  function edgeAligned(video) {
    const frame = modal && modal.querySelector("#camFrame");
    if (!frame) return false;
    const g = guideVideoRect(video, frame);
    if (!g) return false;
    if (!acanvas) { acanvas = document.createElement("canvas"); actx = acanvas.getContext("2d", { willReadFrequently: true }); }
    const sH = Math.round(SAMP_W * g.vH / g.vW);
    acanvas.width = SAMP_W; acanvas.height = sH;
    actx.drawImage(video, 0, 0, SAMP_W, sH);
    const sx = SAMP_W / g.vW, sy = sH / g.vH;
    const X0 = g.x0 * sx, X1 = g.x1 * sx, Y0 = g.y0 * sy, Y1 = g.y1 * sy;
    const gw = X1 - X0, gh = Y1 - Y0;
    const d = Math.max(2, Math.round(Math.min(gw, gh) * BAND));
    const px = actx.getImageData(0, 0, SAMP_W, sH).data;
    const lum = (x, y) => {
      x = Math.min(SAMP_W - 1, Math.max(0, x | 0)); y = Math.min(sH - 1, Math.max(0, y | 0));
      const i = (y * SAMP_W + x) * 4; return 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    };
    // mean luminance of a band; horizontal=true scans along x at rows [a,b)
    const band = (a, b, lo, hi, horizontal) => {
      let s = 0, n = 0;
      for (let t = lo; t < hi; t += 2)
        for (let u = a; u < b; u += 2) { s += horizontal ? lum(t, u) : lum(u, t); n++; }
      return n ? s / n : 0;
    };
    const top    = Math.abs(band(X0, X1, Y0, Y0 + d, true)  - band(X0, X1, Y0 - d, Y0, true));
    const bottom = Math.abs(band(X0, X1, Y1 - d, Y1, true)  - band(X0, X1, Y1, Y1 + d, true));
    const left   = Math.abs(band(Y0, Y1, X0, X0 + d, false) - band(Y0, Y1, X0 - d, X0, false));
    const right  = Math.abs(band(Y0, Y1, X1 - d, X1, false) - band(Y0, Y1, X1, X1 + d, false));
    return Math.min(top, bottom, left, right) >= EDGE_MIN;
  }

  function startAlignLoop() {
    const video = modal.querySelector(".cam-video");
    const frame = modal.querySelector("#camFrame");
    const hint = modal.querySelector("#camHint");
    let last = 0;
    const tick = (ts) => {
      if (!modal) return;
      if (ts - last > 120) {                 // ~8 checks/sec is plenty
        last = ts;
        let ok = false;
        try { ok = edgeAligned(video); } catch { ok = false; }
        alignHits = ok ? Math.min(NEED, alignHits + 1) : 0;
        const aligned = alignHits >= NEED;
        frame.classList.toggle("aligned", aligned);
        hint.textContent = aligned ? "Looks good — hold steady and snap" : "Line the card up inside the frame";
        hint.classList.toggle("ok", aligned);
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  function capture() {
    const video = modal.querySelector(".cam-video");
    const frame = modal.querySelector("#camFrame");
    const g = guideVideoRect(video, frame);
    if (!g) return;
    const vW = g.vW, vH = g.vH;

    let { x0, y0, x1, y1 } = g;
    // expand by MARGIN, clamped to the frame
    const mx = (x1 - x0) * MARGIN, my = (y1 - y0) * MARGIN;
    x0 = Math.max(0, x0 - mx); y0 = Math.max(0, y0 - my);
    x1 = Math.min(vW, x1 + mx); y1 = Math.min(vH, y1 + my);
    const cw = Math.round(x1 - x0), ch = Math.round(y1 - y0);
    if (cw < 20 || ch < 20) return;

    // De-glare by burst: grab several frames and keep the per-pixel MINIMUM. Glare is
    // bright and shifts with tiny hand/light motion, so min drops it to the non-glare
    // value; the static design and persistent edge whitening (bright in every frame)
    // survive. The single biggest lever against glare, done at capture.
    const hint = modal.querySelector("#camHint");
    if (hint) { hint.textContent = "Hold steady…"; hint.classList.remove("ok"); }
    const tmp = document.createElement("canvas");
    tmp.width = cw; tmp.height = ch;
    const tctx = tmp.getContext("2d", { willReadFrequently: true });
    let acc = null, n = 0;

    const finish = () => {
      const out = document.createElement("canvas");
      out.width = cw; out.height = ch;
      out.getContext("2d").putImageData(new ImageData(acc, cw, ch), 0, 0);
      out.toBlob((blob) => {
        if (!blob) { close(); return; }
        const file = new File([blob], `card-${Date.now()}.jpg`, { type: "image/jpeg" });
        const cb = onShot;
        close();
        if (cb) cb(file);
      }, "image/jpeg", 0.92);
    };

    const grab = () => {
      if (!modal) return;               // cancelled mid-burst
      tctx.drawImage(video, x0, y0, cw, ch, 0, 0, cw, ch);
      const d = tctx.getImageData(0, 0, cw, ch).data;
      if (!acc) {
        acc = new Uint8ClampedArray(d);
      } else {
        for (let i = 0; i < d.length; i++) if (d[i] < acc[i]) acc[i] = d[i];
      }
      if (++n < BURST_FRAMES) setTimeout(grab, BURST_MS / BURST_FRAMES);
      else finish();
    };
    grab();
  }

  function close() {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    alignHits = 0;
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    if (modal) { modal.remove(); modal = null; }
    document.body.classList.remove("cam-open");
  }

  // No camera API / blocked → trigger the existing file input (which on mobile still
  // offers the OS camera via capture="environment").
  function fallback() {
    const f = document.getElementById("file");
    if (f) f.click();
  }

  return { open, supported };
})();
