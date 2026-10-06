// 各页面共用：导航、取数、配色、图表。
"use strict";

const NAV = [
  { href: "index.html", label: "总览" },
  { href: "macro.html", label: "宏观" },
  { href: "semis.html", label: "半导体与 AI" },
  { href: "us.html", label: "美股" },
  { href: "crypto.html", label: "加密货币" },
  { label: "个股", soon: true },
];

function renderNav(active) {
  const el = document.getElementById("nav");
  if (!el) return;
  el.innerHTML = NAV.map((n) =>
    n.soon
      ? `<span class="soon">${n.label}</span>`
      : `<a href="${n.href}"${n.href === active ? ' class="active" aria-current="page"' : ""}>${n.label}</a>`
  ).join("");
}

async function loadJSON(rel) {
  const res = await fetch(rel, { cache: "no-cache" });
  if (!res.ok) throw new Error(`读不到 ${rel}（${res.status}）`);
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 100 ? 1 : digits;
  return v.toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtSigned(v, digits = 2) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + fmtNum(Math.abs(v), digits);
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function palette() {
  return {
    series: ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"].map(css),
    ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"),
    grid: css("--grid"), axis: css("--axis"), surface: css("--surface"),
  };
}

// 每条序列各自找离鼠标最近的点（按 x 像素），这样日期不完全对齐的序列也能一起显示。
function registerInteraction() {
  if (!window.Chart || Chart.Interaction.modes.perSeries) return;
  Chart.Interaction.modes.perSeries = function (chart, e, options, useFinal) {
    const pos = Chart.helpers.getRelativePosition(e, chart);
    const items = [];
    let best = Infinity;
    const found = [];
    chart.data.datasets.forEach((ds, di) => {
      const meta = chart.getDatasetMeta(di);
      if (!chart.isDatasetVisible(di) || !meta.data.length) return;
      const els = meta.data;
      let lo = 0, hi = els.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (els[mid].x < pos.x) lo = mid + 1; else hi = mid;
      }
      let idx = lo;
      if (idx > 0 && Math.abs(els[idx - 1].x - pos.x) < Math.abs(els[idx].x - pos.x)) idx -= 1;
      const dx = Math.abs(els[idx].x - pos.x);
      found.push({ element: els[idx], datasetIndex: di, index: idx, dx });
      best = Math.min(best, dx);
    });
    const tol = Math.max(best + 6, 10);
    for (const f of found) if (f.dx <= tol && f.dx < 40) items.push(f);
    return items;
  };
}

function isoToMs(s) {
  return Date.parse(s + "T00:00:00Z");
}

// 画一张图。spec 来自 dashboard.json 的 charts[id]。
function drawChart(canvas, spec, opts = {}) {
  registerInteraction();
  const P = palette();
  const stackedBar = spec.stacked === "bar";
  const stackedArea = spec.stacked === "area";
  let slot = 0;
  const datasets = spec.series.map((s, i) => {
    const isBar = s.type === "bar";
    // 参考线（区间刻度）用灰色，不占数据色
    const isRef = !!s.ref;
    const color = isRef ? P.muted : (stackedBar && !isBar ? P.ink : P.series[slot++ % P.series.length]);
    const ds = {
      label: s.name,
      _type: s.type,
      data: [],
      _full: spec.kind === "path" ? s.data : s.data.map(([d, v]) => ({ x: isoToMs(d), y: v })),
      borderColor: color,
      backgroundColor: color,
      _color: color,
    };
    if (isBar) {
      Object.assign(ds, {
        type: "bar", borderWidth: 0, borderRadius: 2, borderSkipped: false,
        categoryPercentage: 1, barPercentage: stackedBar ? 0.92 : 0.8,
        stack: stackedBar ? "s" : `b${i}`,
      });
    } else {
      Object.assign(ds, {
        type: "line", borderWidth: 2, pointRadius: spec.kind === "path" ? 5 : 0, pointHoverRadius: 4,
        pointBackgroundColor: color, pointBorderColor: P.surface, pointBorderWidth: 2,
        tension: 0, spanGaps: spec.kind === "path", borderDash: s.dash || isRef ? [5, 4] : [],
        stepped: s.step ? "after" : false,
        _step: !!s.step, _ref: isRef,
        stack: stackedArea ? "a" : `l${i}`, order: isRef ? 1 : -1,
      });
      if (stackedArea) {
        ds.fill = i === 0 ? "origin" : "-1";
        ds.backgroundColor = color + "cc";
        ds.borderWidth = 1;
        ds.borderColor = P.surface;
      }
    }
    return ds;
  });

  const unit = spec.unit || "";
  const tooltip = {
    backgroundColor: P.surface, titleColor: P.ink, bodyColor: P.ink2, footerColor: P.ink,
    borderColor: P.axis, borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
    callbacks: {
      title(items) {
        if (!items.length) return "";
        if (spec.kind === "path") return items[0].label;
        const d = new Date(items[0].parsed.x);
        return d.toISOString().slice(0, 10);
      },
      label(item) {
        return ` ${item.dataset.label}：${fmtNum(item.parsed.y)} ${unit}`;
      },
      labelColor(item) {
        return { borderColor: item.dataset._color, backgroundColor: item.dataset._color };
      },
      footer(items) {
        if (!(stackedArea || stackedBar) || items.length < 2) return "";
        const sum = items.filter((it) => (stackedArea || it.dataset._type === "bar")).reduce((a, it) => a + it.parsed.y, 0);
        return `合计：${fmtNum(sum)} ${unit}`;
      },
    },
  };

  const x = spec.kind === "path"
    ? { type: "category", labels: spec.categories, grid: { display: false }, ticks: { color: P.muted }, border: { color: P.axis } }
    : {
        type: "time",
        time: { tooltipFormat: "yyyy-MM-dd", displayFormats: { day: "MM-dd", week: "yyyy-MM-dd", month: "yyyy-MM", quarter: "yyyy-MM", year: "yyyy" } },
        grid: { display: false }, border: { color: P.axis },
        ticks: { color: P.muted, maxRotation: 0, autoSkipPadding: 18 },
      };

  const chart = new Chart(canvas, {
    data: { datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false, normalized: true,
      interaction: spec.kind === "path" ? { mode: "index", intersect: false } : { mode: "perSeries", intersect: false },
      plugins: { legend: { display: false }, tooltip, decimation: { enabled: false } },
      scales: {
        x,
        y: {
          stacked: stackedArea || stackedBar,
          beginAtZero: !!spec.zero || stackedArea || datasets.some((d) => d.type === "bar"),
          grid: { color: P.grid, drawTicks: false }, border: { display: false },
          ticks: { color: P.muted, padding: 6, maxTicksLimit: 6 },
        },
      },
    },
  });
  chart._spec = spec;
  applyRange(chart, opts.startMs ?? null);
  return chart;
}

// 按起始日期过滤数据后重绘（y 轴随之按可见数据缩放）。
function applyRange(chart, startMs) {
  for (const ds of chart.data.datasets) {
    if (ds._ref && ds._full.length) {
      const y = ds._full[0].y;
      const end = ds._full[ds._full.length - 1].x;
      const start = startMs == null ? ds._full[0].x : startMs;
      ds.data = [{ x: start, y }, { x: end, y }];
      continue;
    }
    if (ds._step && startMs != null) {
      const visible = ds._full.filter((p) => p.x >= startMs);
      let prior = null;
      for (const p of ds._full) {
        if (p.x < startMs) prior = p;
        else break;
      }
      ds.data = prior && visible.length ? [{ x: startMs, y: prior.y }, ...visible] : visible;
      continue;
    }
    ds.data = chart._spec.kind === "path" || startMs === null ? ds._full : ds._full.filter((p) => p.x >= startMs);
  }
  if (chart._spec.kind !== "path") {
    // 数据比所选范围短（例如只有几周的笔记数据）时，从第一个数据点开始画，不留大片空白
    const first = Math.min(...chart.data.datasets.map((ds) => (ds._full[0] ? ds._full[0].x : Infinity)));
    chart.options.scales.x.min = startMs !== null && first < startMs ? startMs : undefined;
  }
  chart.update("none");
}

function legendHTML(spec) {
  const P = palette();
  let slot = 0;
  return spec.series.map((s) => {
    if (s.ref) return "";
    const isBar = s.type === "bar";
    const color = spec.stacked === "bar" && !isBar ? P.ink : P.series[slot++ % P.series.length];
    const cls = isBar || spec.stacked === "area" ? "bar" : s.dash ? "dash" : "";
    const style = s.dash && !isBar ? `color:${color}` : `background:${color}`;
    return `<span><i class="${cls}" style="${style}"></i>${esc(s.name)}</span>`;
  }).join("");
}

// 最近 12 期的数据表，给不方便看图的情况。
function tableHTML(spec) {
  if (spec.kind === "path") {
    const head = `<tr><th></th>${spec.categories.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
    const rows = spec.series.map((s) => `<tr><td>${esc(s.name)}</td>${s.data.map((v) => `<td>${fmtNum(v)}</td>`).join("")}</tr>`).join("");
    return `<div class="table-wrap"><table class="data">${head}${rows}</table></div>`;
  }
  const dates = new Set();
  const maps = spec.series.map((s) => new Map(s.data));
  spec.series.forEach((s) => s.data.slice(-12).forEach(([d]) => dates.add(d)));
  const ds = [...dates].sort().slice(-12).reverse();
  const head = `<tr><th>日期</th>${spec.series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr>`;
  const rows = ds.map((d) => `<tr><td>${d}</td>${maps.map((m) => `<td>${m.has(d) ? fmtNum(m.get(d)) : ""}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table class="data">${head}${rows}</table></div>`;
}

// 系统深浅色切换时刷新页面上的图。
function onSchemeChange(fn) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener?.("change", fn);
}
