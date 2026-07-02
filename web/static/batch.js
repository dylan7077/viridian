// Collection grading — drop N card photos, grade each (front-only) via /api/grade,
// build a live table + running total value. source="batch" so grades skip the public
// feed + Discord webhook. Grades run one at a time (the server serialises anyway).
(function () {
  const $ = (s) => document.querySelector(s);
  const input = $("#bfile"), drop = $("#bdrop"), tbody = $("#btbody"), table = $("#btable");
  const totals = $("#btotals"), progress = $("#bprogress"), progressText = $("#bprogressText");

  let count = 0, totalGbp = 0, pricedN = 0, gradeSum = 0, gradeN = 0, running = false;

  const gradeClass = (g) => g == null ? "g-none" : g >= 9 ? "g-gem" : g >= 7 ? "g-high" : "g-low";
  const money = (sym, n) => sym + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function gbpOf(v) {
    if (!v || !v.ok || !v.values) return null;
    const g = v.values.find((x) => x.currency === "GBP") || v.values[0];
    if (!g) return null;
    const val = g.graded != null ? g.graded : g.raw;
    return val != null ? { sym: g.symbol || "£", val } : null;
  }

  function updateTotals() {
    totals.hidden = false;
    $("#btValue").textContent = pricedN ? money("£", totalGbp) : "—";
    $("#btCount").textContent = count;
    $("#btAvg").textContent = gradeN ? (gradeSum / gradeN).toFixed(1) : "—";
  }

  async function gradeOne(file) {
    const url = URL.createObjectURL(file);
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><img class="bt-thumb" src="${url}" alt="" /></td>
      <td class="bt-name"><span class="bt-muted">Identifying…</span></td>
      <td class="bt-grade"><span class="bt-spin"></span></td>
      <td class="bt-val bt-muted">—</td>`;
    tbody.appendChild(tr);
    table.hidden = false;

    const fd = new FormData();
    fd.append("file", file);
    fd.append("source", "batch");
    let res = null;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const r = await fetch("/api/grade", { method: "POST", body: fd });
        res = await r.json();
        if (r.status === 503 && res && res.busy) { await new Promise((x) => setTimeout(x, 1500)); continue; }
      } catch (e) { res = null; }
      break;
    }

    if (!res || !res.ok) {
      tr.querySelector(".bt-name").innerHTML =
        `<span class="bt-muted">${(res && res.message) || "Couldn't grade this photo"}</span>`;
      tr.querySelector(".bt-grade").innerHTML = "—";
      return;
    }
    const card = (res.match && res.match.card) || {};
    const grade = res.grade ? res.grade.overall : null;
    const gbp = gbpOf(res.value);

    tr.querySelector(".bt-name").innerHTML = card.name
      ? `<b>${card.name}</b><span class="bt-set">${card.set || ""}${card.number ? " · #" + card.number : ""}</span>`
      : `<span class="bt-muted">Unidentified card</span>`;
    tr.querySelector(".bt-grade").innerHTML = grade != null
      ? `<span class="bt-chip ${gradeClass(grade)}">${grade}</span>` : "—";
    tr.querySelector(".bt-val").innerHTML = gbp
      ? `<b>${money(gbp.sym, gbp.val)}</b>` : `<span class="bt-muted">—</span>`;
    tr.querySelector(".bt-val").classList.remove("bt-muted");

    count++;
    if (gbp) { totalGbp += gbp.val; pricedN += 1; }
    if (grade != null) { gradeSum += grade; gradeN += 1; }
    updateTotals();
  }

  async function handle(files) {
    if (running) return;
    const list = Array.from(files).filter((f) => f.type.startsWith("image/")).slice(0, 24);
    if (!list.length) return;
    running = true;
    drop.classList.add("busy");
    progress.hidden = false;
    for (let i = 0; i < list.length; i++) {
      progressText.textContent = `Grading ${i + 1} of ${list.length}…`;
      await gradeOne(list[i]);
    }
    progress.hidden = true;
    drop.classList.remove("busy");
    running = false;
  }

  input.addEventListener("change", (e) => handle(e.target.files));
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => handle(e.dataTransfer.files));
})();
