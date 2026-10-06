// 加密货币页。结论和四步状态在 dashboard.json 里已经算好。大图按需加载。
"use strict";

let DATA = null;
const charts = new Map();
const specs = new Map();

const RANGES = [
  { key: "1y", label: "1 年", years: 1 },
  { key: "3y", label: "3 年", years: 3 },
  { key: "all", label: "全部", years: null },
];
const RANGE_KEY = "crypto.btc.range";
let rangeKey = "1y";
try { rangeKey = localStorage.getItem(RANGE_KEY) || rangeKey; } catch (_) {}
if (!RANGES.some((r) => r.key === rangeKey)) rangeKey = "1y";

function rangeStartMs() {
  const r = RANGES.find((x) => x.key === rangeKey);
  if (!r || r.years === null || !DATA?.asof) return null;
  const d = new Date(DATA.asof + "T00:00:00Z");
  d.setUTCFullYear(d.getUTCFullYear() - r.years);
  return d.getTime();
}

function badgeClass(label) {
  if (["贵", "过热", "高", "派发"].includes(label)) return "b-hot";
  if (["便宜", "低", "吸筹"].includes(label)) return "b-cool";
  if (label === "亏损") return "b-hot";
  return "";
}

function renderVerdict() {
  const v = DATA.verdict || {};
  const dims = DATA.dimensions || [];
  const badges = dims.map((d) =>
    `<a class="badge ${badgeClass(d.label)}" href="#s-${esc(d.key)}"><span>${esc(d.name)}</span><b>${esc(d.label)}</b></a>`
  ).join("");
  const lines = (v.lines || []).map((x) => `<li><span class="v-k">${esc(x.k)}</span><span>${esc(x.t)}</span></li>`).join("");
  const rules = (DATA.rules || []).map((r) =>
    `<p><b>${esc(r.name)}</b> ${esc(r.text)}</p>`
  ).join("");
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">${esc(v.label || "比特币")}</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    <div class="badges">${badges}</div>
    <ul class="v-lines">${lines}</ul>
    <p class="v-meaning">${esc(v.synthesis || "")}</p>
    <details class="rule">
      <summary>状态怎么定的</summary>
      <div class="rule-body">${rules}</div>
    </details>`;
}

function kpiHTML(items) {
  return `<div class="kpis">${(items || []).map((k) => `<div class="kpi">
      <div class="kpi-name">${esc(k.name)}</div>
      <div class="kpi-val"><b>${esc(k.text)}</b>${k.unit ? `<small>${esc(k.unit)}</small>` : ""}</div>
      <div class="kpi-meta">${k.date ? esc(k.date) : ""}${k.chg ? `<span>${esc(k.chg_label || "较 7 天前")} ${esc(k.chg)}</span>` : ""}</div>
      ${k.hint ? `<div class="kpi-hint">${esc(k.hint)}</div>` : ""}
    </div>`).join("")}</div>`;
}

function chartCard(id) {
  const meta = (DATA.charts || []).find((c) => c.id === id);
  const title = meta?.title || id;
  return `<article class="card chart" id="c-${esc(id)}" data-file="${esc(meta?.file || "")}">
    <h3>${esc(title)}</h3>
    <div class="c-body"><canvas></canvas></div>
    <div class="legend"></div>
    <p class="c-note"></p>
  </article>`;
}

function tableHTML(rows) {
  if (!rows || !rows.length) return "";
  const body = rows.map((r) => `<tr>
      <td>${esc(r.name)}</td>
      <td>${esc(r.text)}</td>
      <td>${esc(r.gap || "")}</td>
      <td>${esc(r.date || "")}</td>
      <td>${esc(r.note || "")}</td>
    </tr>`).join("");
  return `<div class="table-wrap"><table class="data cost">
    <tr><th>指标</th><th>数值</th><th>现价偏离</th><th>日期</th><th>说明</th></tr>
    ${body}</table></div>`;
}

function groupHTML(g) {
  const charts = (g.charts || []).map(chartCard).join("");
  return `<div class="group">
    <h3 class="group-title">${esc(g.name)}</h3>
    ${kpiHTML(g.kpis)}
    ${charts ? `<div class="charts">${charts}</div>` : ""}
  </div>`;
}

function renderSections() {
  const html = (DATA.sections || []).map((s) => {
    const groups = (s.groups || []).map(groupHTML).join("");
    const charts = (s.charts || []).map(chartCard).join("");
    return `<section class="dim" id="s-${esc(s.id)}">
      <h2>${esc(s.name)}</h2>
      <p class="dim-vote">${esc(s.head || "")}</p>
      ${kpiHTML(s.kpis)}
      ${s.table ? tableHTML(s.table) : ""}
      ${charts ? `<div class="charts">${charts}</div>` : ""}
      ${groups}
    </section>`;
  }).join("");
  document.getElementById("sections").innerHTML = html;
  document.getElementById("chips").innerHTML = (DATA.sections || [])
    .map((s) => `<a href="#s-${esc(s.id)}">${esc(s.name)}</a>`).join("");
}

function renderRange() {
  const el = document.getElementById("range");
  el.innerHTML = RANGES.map((r) =>
    `<button type="button" data-k="${r.key}" aria-pressed="${r.key === rangeKey}">${r.label}</button>`
  ).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    rangeKey = b.dataset.k;
    try { localStorage.setItem(RANGE_KEY, rangeKey); } catch (_) {}
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.k === rangeKey)));
    const start = rangeStartMs();
    charts.forEach((c) => applyRange(c, start));
  };
}

function drawOne(el, spec) {
  const canvas = el.querySelector("canvas");
  if (!canvas) return;
  const prev = charts.get(spec.id);
  if (prev) prev.destroy();
  const chart = drawChart(canvas, spec, { startMs: rangeStartMs() });
  charts.set(spec.id, chart);
  specs.set(spec.id, spec);
  const lg = el.querySelector(".legend");
  if (lg) lg.innerHTML = legendHTML(spec);
  const note = el.querySelector(".c-note");
  if (note) {
    const tail = spec.last_date ? `数据日至 ${spec.last_date}。` : "";
    note.textContent = `${spec.note || ""} ${tail}`.trim();
  }
}

async function loadChart(el) {
  if (el.dataset.loaded) return;
  el.dataset.loaded = "1";
  const file = el.dataset.file;
  if (!file) {
    el.dataset.loaded = "";
    return;
  }
  try {
    const spec = await loadJSON(file);
    drawOne(el, spec);
  } catch (err) {
    el.dataset.loaded = "";
    const note = el.querySelector(".c-note");
    if (note) note.textContent = "这张图没有取到。";
  }
}

function observeCharts() {
  const nodes = [...document.querySelectorAll(".chart")];
  if (!("IntersectionObserver" in window)) {
    nodes.forEach(loadChart);
    return;
  }
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) {
        io.unobserve(en.target);
        loadChart(en.target);
      }
    });
  }, { rootMargin: "400px" });
  nodes.forEach((n) => io.observe(n));
}

function renderNotes() {
  const lines = (DATA.catalog || []).map((c) =>
    `<li>${esc(c.name)}：${c.ok ? `${esc(c.source)}，${esc(c.start || "—")} 起，${esc(c.freq)}，最新 ${esc(c.latest || "—")}` : "未取到"}</li>`
  ).join("");
  document.getElementById("notes").innerHTML = `<p>${esc(DATA.method || "")}</p>
    <details><summary>每个指标的来源和起点</summary><ul class="src-list">${lines}</ul></details>`;
}

function renderHealth() {
  const st = DATA.status || {};
  const src = st.sources || [];
  const bad = src.filter((s) => !s.ok).length;
  const rows = src.map((s) => `<tr><td style="text-align:left">${esc(s.name)}</td><td>${esc(s.last_obs || "—")}</td>
      <td class="${s.ok ? "" : "bad"}">${s.ok ? (s.last_obs ? "正常" : "无数据") : "失败"}</td>
      <td style="text-align:left" class="small">${esc(s.note || "")}</td></tr>`).join("");
  const log = DATA.state_log || [];
  const logHTML = log.length ? `<details class="rule"><summary>判断变化</summary><ul class="log">${
    log.map((r) => `<li><b>${esc(r.date)}</b> ${esc(r.name)} ${esc(r.from || "—")} → ${esc(r.to)}</li>`).join("")
  }</ul></details>` : "";
  document.getElementById("health").innerHTML = `
    ${logHTML}
    <details>
      <summary>数据状态：${src.length} 个来源，${bad ? `<span class="bad">${bad} 项这次没刷新或失败</span>` : "全部正常"}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。某个来源失败时，沿用它上次写成的文件，其他指标照常显示。</p>
      <div class="table-wrap"><table class="data"><tr><th style="text-align:left">来源</th><th>最新</th><th>状态</th><th style="text-align:left">说明</th></tr>${rows}</table></div>
    </details>`;
}

function redrawAll() {
  const start = rangeStartMs();
  charts.forEach((c, id) => {
    const spec = specs.get(id);
    const cv = c.canvas;
    c.destroy();
    if (!spec) return;
    charts.set(id, drawChart(cv, spec, { startMs: start }));
    const lg = cv.closest(".chart")?.querySelector(".legend");
    if (lg) lg.innerHTML = legendHTML(spec);
  });
}

async function main() {
  renderNav("crypto.html");
  try {
    DATA = await loadJSON("data/crypto/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>运行一次「更新加密货币数据」即可。</div>`;
    return;
  }
  const stamp = (DATA.status?.updated_at || DATA.asof || "").replace("T", " ").replace("Z", " UTC");
  document.getElementById("asof").textContent = stamp ? `数据更新于 ${stamp}` : "";
  renderVerdict();
  renderRange();
  renderSections();
  renderNotes();
  renderHealth();
  observeCharts();
  onSchemeChange(redrawAll);
}

main();
