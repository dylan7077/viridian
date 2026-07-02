const feed     = document.getElementById("actFeed");
const loading  = document.getElementById("actLoading");
const emptyMsg = document.getElementById("actEmpty");

const GRADE_NAMES = {
  10: "GEM MINT", 9: "MINT", 8: "NM-MT", 7: "NEAR MINT", 6: "EX-MT",
  5: "EX", 4: "VG-EX", 3: "VG", 2: "GOOD", 1: "POOR",
};

function relTime(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function gradeClass(g) {
  if (g == null) return "g-none";
  if (g >= 9) return "g-gem";
  if (g >= 7) return "g-high";
  return "g-low";
}

function money(n) {
  return "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// Use tcgdex's lightweight /low.png for the small feed thumbnails.
function thumbUrl(u) { return (u || "").replace("/high.png", "/low.png"); }

async function load() {
  let items;
  try {
    const res = await fetch("/api/activity?limit=50");
    items = (await res.json()).items || [];
  } catch {
    loading.innerHTML = `<p style="color:var(--accent)">Couldn't reach the server.</p>`;
    return;
  }
  render(items);
  // Let the stats board refresh itself only when the feed actually changed
  // (a new card was scanned/graded) rather than polling on its own.
  if (window.onActivityUpdate) window.onActivityUpdate(items);
}

function render(items) {
  loading.hidden = true;
  if (!items.length) {
    feed.innerHTML = "";
    emptyMsg.hidden = false;
    return;
  }
  emptyMsg.hidden = true;
  renderSlideshow(items);
  renderTop(items);
  feed.replaceChildren(...items.map(buildRow));
}

// Continuously-sliding strip of every identified card. Rebuilt only when the set
// of cards changes, so the marquee animation doesn't restart on every 6s poll.
let slideKey = "";
function renderSlideshow(items) {
  const wrap = document.getElementById("actSlideshow");
  const track = document.getElementById("actSlideTrack");
  const cards = items.filter((it) => it.image && it.id);
  if (!cards.length) { wrap.hidden = true; return; }

  const key = cards.map((c) => c.id + ":" + c.ts).join("|");
  if (key === slideKey) { wrap.hidden = false; return; }   // no change — keep sliding
  slideKey = key;

  // Repeat the cards enough to overflow the viewport, then duplicate that block so
  // a -50% translate loops seamlessly even with only a few cards.
  const perCopy = Math.max(cards.length,
                           Math.ceil((window.innerWidth || 1200) / 150) + 2);
  const block = [];
  for (let i = 0; i < perCopy; i++) block.push(cards[i % cards.length]);
  track.replaceChildren(...block.map(buildSlide), ...block.map(buildSlide));
  track.style.animationDuration = Math.max(20, block.length * 3.5) + "s";
  wrap.hidden = false;
}

function buildSlide(it) {
  const a = document.createElement("a");
  a.className = "act-slide";
  a.href = `/library?card=${encodeURIComponent(it.id)}`;
  a.title = `${it.name}${it.grade != null ? " · PSA " + it.grade : ""}`;
  const img = document.createElement("img");
  img.src = thumbUrl(it.image); img.alt = it.name || ""; img.loading = "lazy";
  a.appendChild(img);
  if (it.grade != null) {
    const g = document.createElement("span");
    g.className = `act-slide-grade ${gradeClass(it.grade)}`;
    g.textContent = `PSA ${it.grade}`;
    a.appendChild(g);
  }
  return a;
}

function val(it) { return it.graded != null ? it.graded : (it.raw != null ? it.raw : 0); }

// Top 3 highest-value identified cards graded in the last 7 days.
function renderTop(items) {
  const wrap = document.getElementById("actTop5");
  const grid = document.getElementById("actTop5Grid");
  const weekAgo = Math.floor(Date.now() / 1000) - 7 * 86400;
  const candidates = items.filter((it) => it.id && it.ts >= weekAgo && val(it) > 0);
  if (!candidates.length) { wrap.hidden = true; return; }
  // Keep the highest-value instance per card, then take the top 3 by value.
  const best = new Map();
  for (const it of candidates) {
    const prev = best.get(it.id);
    if (!prev || val(it) > val(prev)) best.set(it.id, it);
  }
  const top = [...best.values()].sort((a, b) => val(b) - val(a)).slice(0, 5);
  grid.replaceChildren(...top.map(buildTopCard));
  wrap.hidden = false;
}

function buildTopCard(it) {
  const a = document.createElement("a");
  a.className = "act-top5-card";
  a.href = `/library?card=${encodeURIComponent(it.id)}`;
  a.title = `${it.name}${it.grade != null ? " · PSA " + it.grade : ""}`;

  const thumb = document.createElement("div");
  thumb.className = "act-top5-thumb";
  if (it.image) {
    const img = document.createElement("img");
    img.src = thumbUrl(it.image); img.alt = it.name || ""; img.loading = "lazy"; img.decoding = "async";
    img.addEventListener("error", () => { thumb.classList.add("act-thumb-empty"); img.remove(); });
    thumb.appendChild(img);
  } else { thumb.classList.add("act-thumb-empty"); }
  if (it.grade != null) {
    const g = document.createElement("span");
    g.className = `act-top5-grade ${gradeClass(it.grade)}`;
    g.textContent = `PSA ${it.grade}`;
    thumb.appendChild(g);
  }

  const name = document.createElement("div");
  name.className = "act-top5-name";
  name.textContent = it.name || "Card";
  const price = document.createElement("div");
  price.className = "act-top5-price";
  price.textContent = it.graded != null ? money(it.graded) : money(it.raw);

  a.append(thumb, name, price);
  return a;
}

// Built with safe DOM APIs (no innerHTML on card data) so any name/set is inert.
function buildRow(it) {
  // Identified cards become a deep link into the library modal; others stay static.
  const row = document.createElement(it.id ? "a" : "article");
  row.className = it.id ? "act-row act-row-link" : "act-row";
  if (it.id) row.href = `/library?card=${encodeURIComponent(it.id)}`;

  const thumb = document.createElement("div");
  thumb.className = "act-thumb";
  if (it.image) {
    const img = document.createElement("img");
    img.src = thumbUrl(it.image); img.alt = it.name || ""; img.loading = "lazy"; img.decoding = "async";
    img.addEventListener("error", () => { thumb.classList.add("act-thumb-empty"); img.remove(); });
    thumb.appendChild(img);
  } else {
    thumb.classList.add("act-thumb-empty");
  }

  const main = document.createElement("div");
  main.className = "act-main";
  const name = document.createElement("div");
  name.className = "act-name";
  name.textContent = it.name || "Unidentified card";
  const meta = document.createElement("div");
  meta.className = "act-meta";
  const setbits = [it.set, it.number ? `#${it.number}` : null].filter(Boolean).join(" · ");
  meta.textContent = setbits || "Not identified";
  main.append(name, meta);

  const right = document.createElement("div");
  right.className = "act-right";

  const grade = document.createElement("div");
  grade.className = `act-grade ${gradeClass(it.grade)}`;
  grade.textContent = it.grade != null ? `PSA ${it.grade}` : "—";
  if (it.grade != null) grade.title = GRADE_NAMES[it.grade] || "";
  right.appendChild(grade);

  if (it.raw != null) {
    const val = document.createElement("div");
    val.className = "act-val";
    val.textContent = it.graded != null ? `${money(it.graded)} graded` : money(it.raw);
    right.appendChild(val);
  }

  const foot = document.createElement("div");
  foot.className = "act-foot";
  const src = document.createElement("span");
  src.className = `act-src act-src-${it.source === "bot" ? "bot" : "web"}`;
  src.textContent = it.source === "bot" ? "● Bot" : "● Web";
  const time = document.createElement("span");
  time.className = "act-time";
  time.textContent = relTime(it.ts);
  foot.append(src, time);
  right.appendChild(foot);

  row.append(thumb, main, right);
  return row;
}

load();
setInterval(load, 6000);
