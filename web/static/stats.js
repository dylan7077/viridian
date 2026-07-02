// Site-wide scan graphs (Chart.js): scans over time + grade distribution across
// every recorded grade. Reloads only when activity.js reports the feed changed
// (a new grade landed), so there's no separate always-on polling loop.

const ovStats = document.getElementById("ovStats");

// Theme colours pulled from the live CSS so charts match the site.
const CSS = getComputedStyle(document.documentElement);
const C = {
  primary: (CSS.getPropertyValue("--primary").trim() || "#2dd4a0"),
  muted: (CSS.getPropertyValue("--muted").trim() || "#8b9a94"),
  border: (CSS.getPropertyValue("--border").trim() || "rgba(255,255,255,0.10)"),
  gem: "#7a8450",
  high: "#E6C97A",
  low: "rgba(255,255,255,0.28)",
};
function barColor(grade) {                 // colour a distribution bar by PSA tier
  if (grade >= 9) return C.gem;
  if (grade >= 7) return C.high;
  return C.low;
}

// Reload the graphs only when the newest grade / feed size changes.
let lastSig = "";
window.onActivityUpdate = function (items) {
  const head = items && items[0];
  const sig = (head ? head.ts + ":" + head.id : "") + "|" + (items ? items.length : 0);
  if (sig === lastSig) return;
  lastSig = sig;
  loadOverview();
};

async function loadOverview() {
  let d;
  try {
    const res = await fetch("/api/stats/overview");
    d = await res.json();
  } catch {
    return;                                // leave whatever is already shown
  }
  if (!d || !d.ok || !d.total) { ovStats.hidden = true; return; }
  ovStats.hidden = false;
  const sub = document.getElementById("ovSub");
  if (sub) {
    const avg = d.avg_grade != null ? ` · avg grade ${d.avg_grade}` : "";
    sub.textContent = `${d.total.toLocaleString()} cards graded${avg} — scan volume over time and how grades break down.`;
  }
  renderCharts(d);
}

// ── Charts ────────────────────────────────────────────────────────────
let timelineChart = null, distChart = null;
const COMMON = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  animation: { duration: 350 },
};

function renderCharts(d) {
  if (timelineChart) timelineChart.destroy();
  if (distChart) distChart.destroy();

  // Scans over time — short M/D labels, thinned to ~10 visible ticks.
  const tl = d.timeline || [];
  const labels = tl.map((p) => {
    const [, m, day] = p.date.split("-");
    return `${Number(m)}/${Number(day)}`;
  });
  const step = Math.max(1, Math.ceil(labels.length / 10));
  timelineChart = new Chart(document.getElementById("ovTimeline"), {
    type: "bar",
    data: {
      labels, datasets: [{
        data: tl.map((p) => p.count),
        backgroundColor: C.primary, borderRadius: 4, maxBarThickness: 26
      }]
    },
    options: {
      ...COMMON,
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: C.muted, font: { size: 10 }, autoSkip: false,
            callback: (v, i) => (i % step === 0 ? labels[i] : "")
          }
        },
        y: {
          beginAtZero: true, grid: { color: C.border },
          ticks: { color: C.muted, font: { size: 10 }, precision: 0 }
        },
      },
    },
  });

  // Grade distribution — PSA 1..10, coloured by tier.
  const dist = d.distribution || [];
  distChart = new Chart(document.getElementById("ovDist"), {
    type: "bar",
    data: {
      labels: dist.map((_, i) => i + 1),
      datasets: [{
        data: dist, backgroundColor: dist.map((_, i) => barColor(i + 1)),
        borderRadius: 4, maxBarThickness: 34
      }],
    },
    options: {
      ...COMMON,
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: C.muted, font: { size: 10 } },
          title: { display: true, text: "PSA grade", color: C.muted, font: { size: 10 } }
        },
        y: {
          beginAtZero: true, grid: { color: C.border },
          ticks: { color: C.muted, font: { size: 10 }, precision: 0 }
        },
      },
    },
  });
}
