const PER_PAGE = 200;
let page = 1, total = 0, totalPages = 1, allSets = [], setCounts = {}, indexedTotal = null;

const grid        = document.getElementById("libGrid");
const countNum    = document.getElementById("libCountNum");
const countLbl    = document.getElementById("libCountLbl");
const search      = document.getElementById("libSearch");
const searchClear = document.getElementById("libSearchClear");
const emptyState  = document.getElementById("libEmpty");
const emptyReset  = document.getElementById("libEmptyReset");
const filterChips = document.getElementById("libFilterChips");

// custom set picker
const setWrap   = document.getElementById("libSetWrap");
const setBtn    = document.getElementById("libSetBtn");
const setPop    = document.getElementById("libSetPop");
const setSearch = document.getElementById("libSetSearch");
const setList   = document.getElementById("libSetList");
const setLabel  = document.getElementById("libSetLabel");
let currentSet  = "";     // value of currently picked set

const pgFirst = document.getElementById("libPgFirst");
const pgPrev  = document.getElementById("libPgPrev");
const pgNext  = document.getElementById("libPgNext");
const pgLast  = document.getElementById("libPgLast");
const pgCur   = document.getElementById("libPgCur");
const pgTotal = document.getElementById("libPgTotal");

function fetchWithTimeout(url, ms = 5000) {
  return Promise.race([
    fetch(url),
    new Promise((_, reject) => setTimeout(() => reject(new Error(`Request timed out after ${ms}ms`)), ms)),
  ]);
}

let loadReq = 0;       // guards against out-of-order responses overwriting newer ones

async function load() {
  const token = ++loadReq;
  const q = search.value.trim();
  const s = currentSet;
  const url = `/api/cards?q=${encodeURIComponent(q)}&set=${encodeURIComponent(s)}&page=${page}&per_page=${PER_PAGE}`;

  let res;
  try {
    res = await fetchWithTimeout(url, 5000);
  } catch (err) {
    if (token !== loadReq) return;
    showError(`Couldn't reach the server: ${err.message || err}. Is the backend running on port 8000?`);
    return;
  }
  if (token !== loadReq) return;     // a newer load() started — drop this stale result

  if (!res.ok) {
    showError(`Server returned ${res.status} ${res.statusText}.`);
    return;
  }

  let data;
  try {
    data = await res.json();
  } catch {
    showError(`Response wasn't valid JSON. The API endpoint may be down.`);
    return;
  }
  if (token !== loadReq) return;     // re-check after the JSON await

  total = data.total || 0;
  totalPages = data.pages || Math.max(1, Math.ceil(total / PER_PAGE));
  page = data.page || page;     // server clamps page into range
  setCounts = data.set_counts || {};
  paintCount(total);
  render(data.cards || []);
  renderPages();
  populateSets(data.sets || []);
  renderFilterChips();
}

function showError(msg) {
  grid.innerHTML = `
    <div class="lib-empty">
      <p style="color:var(--accent);max-width:480px">${escapeHtml(msg)}</p>
      <button class="btn ghost sm" type="button" onclick="load()">Retry</button>
    </div>`;
}

async function refreshIndexed() {
  try {
    const h = await (await fetchWithTimeout("/api/health", 3000)).json();
    indexedTotal = Number(h.indexed ?? h.index_size ?? 0) || 0;
  } catch { indexedTotal = null; }
  paintCount(total);
}

function paintCount(totalCount) {
  countNum.textContent = totalCount.toLocaleString();
  countLbl.textContent = totalCount === 1 ? "card" : "cards";
  if (indexedTotal != null && totalCount !== indexedTotal) {
    countLbl.textContent += ` (${indexedTotal.toLocaleString()} indexed)`;
  }
}

function render(cards) {
  grid.innerHTML = "";
  if (!cards.length) {
    emptyState.hidden = false;
    return;
  }
  emptyState.hidden = true;

  const frag = document.createDocumentFragment();
  for (const c of cards) {
    frag.appendChild(buildCard(c));
  }
  grid.appendChild(frag);
}

// Build one card tile with safe DOM APIs (no innerHTML) so card names/sets
// containing quotes, apostrophes, &, < etc. can never break markup or inject HTML.
function buildCard(c) {
  const el = document.createElement("div");
  el.className = "card-item";
  el.tabIndex = 0;
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", `${c.name} — ${c.set} #${c.number}`);

  const wrap = document.createElement("div");
  wrap.className = "card-img-wrap";

  const img = document.createElement("img");
  img.alt = c.name || "";
  img.loading = "lazy";
  img.decoding = "async";
  const markLoaded = () => img.classList.add("is-loaded");   // stops the skeleton shimmer
  img.addEventListener("load", markLoaded);
  img.addEventListener("error", () => {
    const ph = document.createElement("div");
    ph.className = "placeholder";
    ph.textContent = `${c.set} #${c.number}`;
    wrap.replaceChildren(ph);
  });
  img.src = thumbUrl(c.image);    // low-res for the grid; modal uses full-res
  // If the image was cached and finished before the listener attached, catch it.
  if (img.complete && img.naturalWidth > 0) markLoaded();

  const holo = document.createElement("div");
  holo.className = "holo";
  wrap.append(img, holo);

  const info = document.createElement("div");
  info.className = "card-info";
  const name = document.createElement("div");
  name.className = "card-name";
  name.textContent = c.name || "";
  const set = document.createElement("div");
  set.className = "card-set";
  set.textContent = `${c.set} #${c.number}`;
  info.append(name, set);

  el.append(wrap, info);
  el.addEventListener("click", () => openModal(c));
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModal(c); }
  });
  return el;
}

function renderPages() {
  if (page > totalPages) page = totalPages;
  pgCur.textContent   = page.toLocaleString();
  pgTotal.textContent = totalPages.toLocaleString();
  pgFirst.disabled = page <= 1;
  pgPrev.disabled  = page <= 1;
  pgNext.disabled  = page >= totalPages;
  pgLast.disabled  = page >= totalPages;
}

function renderFilterChips() {
  const q = search.value.trim();
  const parts = [];
  if (q) {
    parts.push(`<span class="lib-chip"><strong>Search:</strong> "${escapeHtml(q)}"
      <button type="button" data-clear="search" aria-label="Clear search">&times;</button></span>`);
  }
  if (currentSet) {
    parts.push(`<span class="lib-chip"><strong>Set:</strong> ${escapeHtml(currentSet)}
      <button type="button" data-clear="set" aria-label="Clear set filter">&times;</button></span>`);
  }
  const filters = document.getElementById("libFilters");
  if (!parts.length) {
    filters.hidden = true;          // keep the bar clean when nothing is filtered
    filterChips.innerHTML = "";
  } else {
    filters.hidden = false;
    filterChips.innerHTML = parts.join("");
  }
  // bind the chip × buttons
  filterChips.querySelectorAll("[data-clear]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const what = btn.dataset.clear;
      if (what === "search") {
        search.value = ""; searchClear.hidden = true;
      } else if (what === "set") {
        currentSet = ""; setLabel.textContent = "All sets"; setBtn.classList.remove("is-picked");
      }
      page = 1; load();
    });
  });
}

/* ── set picker ─────────────────────────────────────────── */

function populateSets(sets) {
  allSets = sets || [];
  renderSetOptions();
}

function renderSetOptions() {
  const q = setSearch.value.trim().toLowerCase();
  const matches = q
    ? allSets.filter(s => s.toLowerCase().includes(q))
    : allSets;

  const grand = Object.values(setCounts).reduce((a, b) => a + b, 0);
  let html = `<button class="lib-set-option ${currentSet === "" ? "is-active" : ""}" data-value="" type="button" role="option">
                <span>All sets</span>
                ${grand ? `<span class="opt-count">${grand.toLocaleString()}</span>` : ""}
              </button>`;
  if (!matches.length) {
    html += `<div class="lib-set-empty">No sets match "${escapeHtml(setSearch.value)}"</div>`;
  } else {
    const slice = matches.slice(0, 200);
    for (const name of slice) {
      const active = name === currentSet ? "is-active" : "";
      const n = setCounts[name];
      html += `<button class="lib-set-option ${active}" data-value="${escapeAttr(name)}" type="button" role="option">
                 <span>${escapeHtml(name)}</span>
                 ${n ? `<span class="opt-count">${n.toLocaleString()}</span>` : ""}
               </button>`;
    }
    if (matches.length > slice.length) {
      html += `<div class="lib-set-empty">${matches.length - slice.length} more — refine your search</div>`;
    }
  }
  setList.innerHTML = html;
}

function openSetPicker() {
  setPop.hidden = false;
  setBtn.setAttribute("aria-expanded", "true");
  setSearch.value = "";
  renderSetOptions();
  requestAnimationFrame(() => setSearch.focus());
}
function closeSetPicker() {
  setPop.hidden = true;
  setBtn.setAttribute("aria-expanded", "false");
}
function toggleSetPicker() { setPop.hidden ? openSetPicker() : closeSetPicker(); }

function pickSet(value) {
  currentSet = value || "";
  setLabel.textContent = currentSet || "All sets";
  setBtn.classList.toggle("is-picked", !!currentSet);
  closeSetPicker();
  page = 1; load();
}

setBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleSetPicker(); });
setSearch.addEventListener("input", renderSetOptions);
setSearch.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); closeSetPicker(); setBtn.focus(); }
  if (e.key === "Enter") {
    const first = setList.querySelector(".lib-set-option");
    if (first) { e.preventDefault(); pickSet(first.dataset.value); }
  }
});
setList.addEventListener("click", (e) => {
  const opt = e.target.closest(".lib-set-option");
  if (opt) pickSet(opt.dataset.value);
});
document.addEventListener("click", (e) => {
  if (!setWrap.contains(e.target)) closeSetPicker();
});

/* ── other controls ─────────────────────────────────────── */

let debounce;
search.addEventListener("input", () => {
  searchClear.hidden = !search.value;
  clearTimeout(debounce);
  debounce = setTimeout(() => { page = 1; load(); }, 250);
});
searchClear.addEventListener("click", () => {
  search.value = "";
  searchClear.hidden = true;
  page = 1; load();
  search.focus();
});
emptyReset.addEventListener("click", () => {
  search.value = ""; searchClear.hidden = true;
  currentSet = ""; setLabel.textContent = "All sets"; setBtn.classList.remove("is-picked");
  page = 1; load();
});

pgFirst.addEventListener("click", () => { page = 1; load(); });
pgPrev .addEventListener("click", () => { if (page > 1) { page--; load(); } });
pgNext .addEventListener("click", () => { page++; load(); });
pgLast .addEventListener("click", () => { page = totalPages; load(); });

/* ── modal ──────────────────────────────────────────────── */

const modal = document.getElementById("libModal");
const holo  = document.getElementById("libModalHolo");
const priceBox  = document.getElementById("libModalPrice");
const priceRows = document.getElementById("libPriceRows");
const priceNote = document.getElementById("libPriceNote");
let priceReq = 0;     // token to ignore prices from a previously-opened card

function openModal(c, updateUrl = true) {
  document.getElementById("libModalImg").src = c.image || "";
  document.getElementById("libModalImg").alt = c.name;
  document.getElementById("libModalName").textContent = c.name;
  document.getElementById("libModalMeta").textContent = `${c.set} · #${c.number}`;
  // The index carries no rarity, so always run the showcase holo — it's the
  // premium "inspect the card" moment and reads better than a flat image.
  holo.classList.add("holo-on");
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  // Reflect the open card in the URL so it's shareable / deep-linkable.
  if (updateUrl && c.id) {
    const u = new URL(location.href);
    u.searchParams.set("card", c.id);
    history.replaceState({}, "", u);
  }
  loadPrice(c);
}

async function loadPrice(c) {
  const token = ++priceReq;
  priceBox.hidden = true;
  priceNote.textContent = "Fetching live market price…";
  if (!c.id) { priceNote.textContent = ""; return; }
  let data;
  try {
    data = await (await fetchWithTimeout(`/api/price/${encodeURIComponent(c.id)}`, 12000)).json();
  } catch {
    data = null;
  }
  if (token !== priceReq) return;   // a newer card was opened; drop stale result
  if (data && data.ok && Array.isArray(data.currencies) && data.currencies.length) {
    priceRows.replaceChildren(...data.currencies.map(buildPriceRow));
    priceBox.hidden = false;
    priceNote.textContent = "";     // disclaimer now lives inside the price panel
  } else {
    priceBox.hidden = true;
    priceNote.textContent = "Live market price unavailable for this card.";
  }
}

function fmtMoney(symbol, n) {
  return symbol + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function buildPriceRow(v) {
  const tr = document.createElement("tr");
  const cur = document.createElement("td"); cur.className = "pc-cur"; cur.textContent = v.currency;
  const raw = document.createElement("td"); raw.className = "pc-raw"; raw.textContent = fmtMoney(v.symbol, v.raw);
  const grd = document.createElement("td"); grd.className = "pc-graded";
  grd.textContent = v.graded != null ? fmtMoney(v.symbol, v.graded) : "—";
  tr.append(cur, raw, grd);
  return tr;
}
function closeModal() {
  modal.hidden = true;
  document.body.style.overflow = "";
  const u = new URL(location.href);
  if (u.searchParams.has("card")) {
    u.searchParams.delete("card");
    history.replaceState({}, "", u);
  }
}

// Deep link: /library?card=base1-4 opens that card's modal on load.
async function openCardFromUrl() {
  const id = new URLSearchParams(location.search).get("card");
  if (!id) return;
  try {
    const res = await fetchWithTimeout(`/api/card/${encodeURIComponent(id)}`, 6000);
    if (!res.ok) return;
    const data = await res.json();
    if (data.ok && data.card) openModal(data.card, false);
  } catch { /* ignore — just don't open */ }
}
holo.addEventListener("pointermove", (e) => {
  const b = holo.getBoundingClientRect();
  const px = (e.clientX - b.left) / b.width, py = (e.clientY - b.top) / b.height;
  holo.style.setProperty("--active", "1");
  holo.style.setProperty("--mx", `${px * 100}%`); holo.style.setProperty("--my", `${py * 100}%`);
  holo.style.setProperty("--bx", `${px * 100}%`); holo.style.setProperty("--by", `${py * 100}%`);
  holo.style.setProperty("--rx", `${(px - 0.5) * 22}deg`);
  holo.style.setProperty("--ry", `${(0.5 - py) * 22}deg`);
});
holo.addEventListener("pointerleave", () => {
  holo.style.setProperty("--active", "0");
  holo.style.setProperty("--rx", "0deg"); holo.style.setProperty("--ry", "0deg");
});
document.getElementById("libModalClose").addEventListener("click", closeModal);
document.getElementById("libModalBackdrop").addEventListener("click", closeModal);
window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ── helpers ────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
// tcgdex serves /low.png (~180KB) and /high.png (~1MB). Use low for grid tiles so
// 100 cards load fast; the modal keeps the full-res image for the close-up.
function thumbUrl(u) { return (u || "").replace("/high.png", "/low.png"); }

load();
openCardFromUrl();
refreshIndexed();
setInterval(refreshIndexed, 4000);
