// 各板块页面共用的三层版式：结论（整体 + 各维度的标签与理由）→ 依据（每维几个数 + 对应的图）→ 时间（即将发布）。
// 页面脚本给 DATA 赋值后调用这些函数；sectionExtra(key) 可以在某一块的依据下面插入额外内容。
"use strict";

let DATA = null;
let sectionExtra = () => "";
let sectionAfter = () => "";

const RANGES = [
  { key: "2y", label: "2 年", years: 2 },
  { key: "5y", label: "5 年", years: 5 },
  { key: "10y", label: "10 年", years: 10 },
  { key: "all", label: "全部", years: null },
];
const RANGE_KEY = (document.body.dataset.board || "macro") + ".range";
let rangeKey = "5y";
try { rangeKey = localStorage.getItem(RANGE_KEY) || rangeKey; } catch (_) {}
if (!RANGES.some((r) => r.key === rangeKey)) rangeKey = "5y";

const charts = new Map(); // id -> Chart

function rangeStartMs() {
  const r = RANGES.find((x) => x.key === rangeKey);
  if (!r || r.years === null) return null;
  const d = new Date(DATA.asof + "T00:00:00Z");
  d.setUTCFullYear(d.getUTCFullYear() - r.years);
  return d.getTime();
}

function renderRange() {
  const el = document.getElementById("range");
  el.innerHTML = RANGES.map((r) => `<button type="button" data-k="${r.key}" aria-pressed="${r.key === rangeKey}">${r.label}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    rangeKey = b.dataset.k;
    try { localStorage.setItem(RANGE_KEY, rangeKey); } catch (_) {}
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.k === rangeKey)));
    const s = rangeStartMs();
    charts.forEach((c) => applyRange(c, s));
  };
}

// ---- 第一层：结论 ----
function renderVerdict() {
  const v = DATA.verdict || {};
  const lines = (v.lines || []).map((x) => `<li><span class="v-k">${esc(x.k)}</span><span>${esc(x.t)}</span></li>`).join("");
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">${esc(v.label || "整体环境")}</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    ${lines ? `<ul class="v-lines">${lines}</ul>` : ""}`;
}

// 判断变化日志：放在整体环境下面，默认折叠；每条写什么时候、因为哪条数据、哪一块从什么变成什么、理由
function renderStateLog() {
  const log = DATA.state_log || [];
  const changes = log.filter((r) => r.from);
  const start = log.length ? log[log.length - 1].date : "";
  const summary = changes.length ? `近 30 天 ${changes.length} 次变化`
    : start ? `自 ${start} 开始记录，还没有变化` : "还没有记录";
  const body = changes.length ? `<ul class="log">${changes.map((r) => `<li>
      <div><span class="muted">${esc(r.date)}</span> <b>${esc(r.name)}</b>：${esc(r.from)} → <b>${esc(r.to)}</b></div>
      <div class="small"><span class="muted">因为</span> ${esc(r.trigger || "当天没有匹配到发布，可能是数据修订或市场变量变化")}</div>
      ${r.head ? `<div class="small"><span class="muted">理由</span> ${esc(r.head)}</div>` : ""}
    </li>`).join("")}</ul>`
    : `<p class="small muted">任一维度或整体判断的标签变了，会记在这里：什么时候、因为哪条数据、哪一块从什么变成什么、理由。</p>`;
  document.getElementById("statelog").innerHTML = `<details><summary><span class="v-label">判断变化日志</span>
    <span class="small muted">${esc(summary)}</span></summary>${body}</details>`;
}

// 五个维度一行一个：名字 | 标签 + 理由（每条理由：小结论 + 用哪几个数、对照什么锚点）
function renderStates(dims) {
  document.getElementById("states").innerHTML = (dims || DATA.dimensions).map((d) => {
    const why = d.why || [];
    return `<a class="st-row" href="#${d.key}">
      <span class="st-name">${esc(d.name)}</span>
      <span class="st-body">
        <span class="st-label tag-state">${esc(d.label)}</span>
        ${why.length ? `<ul class="st-why">${why.map((w) => `<li><b>${esc(w.k)}</b>：${esc(w.t)}</li>`).join("")}</ul>`
          : `<span class="st-head muted">${esc(d.head || "")}</span>`}
      </span>
    </a>`;
  }).join("");
}

// ---- 第二层：每维的几个数 ----
function chgHTML(c) {
  if (!c) return `<span class="muted">—</span>`;
  const arrow = c.dir === "up" ? "↑" : c.dir === "down" ? "↓" : "→";
  return `<span>${arrow} ${esc(c.text)}</span><div class="small muted">${esc(c.label)} ${esc(c.base)}</div>`;
}

const MOVES = [
  { key: "all", label: "全部" },
  { key: "good2", label: "明显转好" },
  { key: "good1", label: "略转好" },
  { key: "hawk2", label: "明显转鹰" },
  { key: "hawk1", label: "略转鹰" },
  { key: "flat", label: "持平" },
  { key: "dove1", label: "略转鸽" },
  { key: "dove2", label: "明显转鸽" },
  { key: "bad1", label: "略转差" },
  { key: "bad2", label: "明显转差" },
  { key: "tight2", label: "大幅收窄" },
  { key: "tight1", label: "明显收窄" },
  { key: "calm", label: "平稳" },
  { key: "wide1", label: "明显走阔" },
  { key: "wide2", label: "大幅走阔" },
];
let moveKey = "all";

function moveHTML(mv) {
  if (!mv) return `<span class="muted">—</span>`;
  return `<b class="mv mv-${esc(mv.key)}">${esc(mv.label)}</b>`;
}

function statusHTML(st) {
  if (!st) return `<span class="muted">—</span>`;
  return `<b class="lv lv-${esc(st.key)}">${esc(st.label)}</b>`;
}

function verdictHTML(v) {
  if (!v || !v.label) return `<span class="muted">—</span>`;
  return `<b class="vd vd-${esc(v.key)}">${esc(v.label)}</b>`;
}

function expectHTML(m) {
  if (m.expect == null || m.expect === "") return `<span class="muted">—</span>`;
  return `<b>${esc(m.expect)}</b><small>${esc(m.unit)}</small>`;
}

function surpriseMetricsHTML(d) {
  const rows = (d.metrics || []).map((m) => `
    <div class="ev has-surprise">
      <span class="ev-name">${m.chart ? `<a href="#c-${esc(m.chart)}">${esc(m.name)}</a>` : esc(m.name)}${m.note ? `<div class="small muted">${esc(m.note)}</div>` : ""}</span>
      <span class="ev-val"><i class="m-label">最新</i><b>${esc(m.text)}</b><small>${esc(m.unit)}</small><div class="small muted">${esc(m.date)}</div></span>
      <span class="ev-expect"><i class="m-label">预期</i>${expectHTML(m)}</span>
      <span class="ev-vd"><i class="m-label">判断</i>${verdictHTML(m.verdict)}</span>
      <span class="ev-chg"><i class="m-label">较上期</i>${chgHTML(m.chg)}</span>
      <span class="ev-st"><i class="m-label">状态</i>${statusHTML(m.status)}</span>
      <span class="ev-anchor"><i class="m-label">对照</i>${esc(m.anchor || "—")}</span>
    </div>`).join("");
  if (!rows) return "";
  return `<div class="card evid"><div class="ev ev-head has-surprise"><span>指标</span><span>最新</span><span>预期</span><span>判断</span><span>较上期</span><span>状态</span><span>对照</span></div>${rows}</div>`;
}

function metricsHTML(d) {
  if (d.metric_layout === "surprise") return surpriseMetricsHTML(d);
  const hasMv = (d.metrics || []).some((m) => m.move);
  const hasSt = (d.metrics || []).some((m) => m.status);
  const cols = `has-mv${hasSt ? " has-st" : ""}`;
  const rows = (d.metrics || []).map((m) => `
    <div class="ev ${cols}" ${m.move ? `data-mv="${esc(m.move.key)}"` : ""}>
      <span class="ev-name">${m.chart ? `<a href="#c-${esc(m.chart)}">${esc(m.name)}</a>` : esc(m.name)}${m.model ? ' <span class="tag">预测</span>' : ""}${m.ref ? ' <span class="tag tag-ref">参考</span>' : ""}${m.note ? `<div class="small muted">${esc(m.note)}</div>` : ""}</span>
      <span class="ev-val"><i class="m-label">最新</i><b>${esc(m.text)}</b><small>${esc(m.unit)}</small><div class="small muted">${esc(m.date)}</div></span>
      <span class="ev-chg"><i class="m-label">较上期</i>${chgHTML(m.chg)}</span>
      ${hasMv ? `<span class="ev-mv"><i class="m-label">变动</i>${moveHTML(m.move)}</span>` : ""}
      ${hasSt ? `<span class="ev-st"><i class="m-label">状态</i>${statusHTML(m.status)}</span>` : ""}
      <span class="ev-anchor"><i class="m-label">对照</i>${esc(m.anchor || "—")}</span>
      ${m.about ? `<p class="ev-about">${esc(m.about)}</p>` : ""}
    </div>`).join("");
  if (!rows) return "";
  return `<div class="card evid"><div class="ev ev-head ${cols}"><span>指标</span><span>最新</span><span>较上期</span>${hasMv ? "<span>变动</span>" : ""}${hasSt ? "<span>状态</span>" : ""}<span>对照</span></div>${rows}</div>`;
}

function applyMoveFilter() {
  document.querySelectorAll(".ev[data-mv]").forEach((el) => {
    el.hidden = moveKey !== "all" && el.dataset.mv !== moveKey;
  });
}

function renderStatusFilter() {
  const el = document.getElementById("st-filter");
  if (!el) return;
  const any = (DATA.dimensions || []).some((d) => (d.metrics || []).some((m) => m.move));
  if (!any) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = MOVES.map((s) =>
    `<button type="button" data-mv="${s.key}" aria-pressed="${s.key === moveKey}">${s.label}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    moveKey = b.dataset.mv;
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mv === moveKey)));
    applyMoveFilter();
  };
  applyMoveFilter();
}

// ---- 第三层：时间 ----
// 昨日发布 + 今日发布 + 即将发布：放在第一屏右边，按访问时的北京日期分开；每条带重要程度，理由折叠
const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
const bjDay = (offset = 0) => new Date(Date.now() + 8 * 3600e3 + offset * 86400e3).toISOString().slice(0, 10);
const bjToday = () => bjDay(0);
const stars = (n) => `<span class="stars" aria-label="重要程度 ${n} 星">${"★".repeat(n)}<i>${"☆".repeat(5 - n)}</i></span>`;

function calRowHTML(x) {
  const [, hm] = (x.bj || "").split(" ");
  const imp = x.importance;
  const sc = x.scenario && x.scenario.affects && x.scenario.affects.length ? x.scenario : null;
  const why = imp ? `<details class="why"><summary>${esc(imp.timing)}指标 · 为什么${imp.stars >= 3 ? "重要" : "不太重要"}</summary>
      <p>${esc(imp.why)}</p>${sc ? `<p class="muted">对本站判断：${esc(sc.text)}</p>` : ""}</details>` : "";
  return `<li class="${imp && imp.stars >= 4 ? "hi" : ""}">
    <span class="up-time">${esc(hm || "")}</span>
    <span class="up-title">${esc(x.title)}${x.ref ? `<small>${esc(x.ref)}</small>` : ""}${imp ? stars(imp.stars) : ""}</span>
    <span class="up-num"><b>${esc(x.forecast_text || "")}</b><small>${x.previous_text ? `前值 ${esc(x.previous_text)}` : ""}</small></span>
    ${why}
  </li>`;
}

function ydayRowHTML(x) {
  const [, hm] = (x.bj || "").split(" ");
  const imp = x.importance;
  const actual = x.level_text || x.actual_text || "—";
  const bits = [x.forecast_text ? `预期 ${x.forecast_text}` : "", x.previous_text ? `前值 ${x.previous_text}` : ""].filter(Boolean);
  const move = x.move ? `<p class="read-why"><b>变动</b>${esc(x.move)}</p>` : "";
  const meaning = x.meaning ? `<p class="read-why"><b>意义</b>${esc(x.meaning)}</p>` : (x.status ? `<p class="read-why">${esc(x.status)}</p>` : "");
  return `<li class="yd ${imp && imp.stars >= 4 ? "hi" : ""}">
    <div class="yd-head">
      <span class="up-time">${esc(hm || "")}</span>
      <span class="up-title">${esc(x.title)}${imp ? stars(imp.stars) : ""}${x.ref ? `<small>${esc(x.ref)}</small>` : ""}</span>
      <span class="up-num"><b>${esc(actual)}</b>${bits.length ? `<small>${esc(bits.join(" · "))}</small>` : ""}</span>
    </div>
    ${move}${meaning}
  </li>`;
}

function renderYesterday() {
  const el = document.getElementById("yesterday");
  if (!el) return;
  const recent = (DATA.releases || {}).recent || [];
  const rows = recent.filter((x) => x.bj_date === bjDay(-1));
  el.innerHTML = rows.length ? `<ul class="up-list">${rows.map(ydayRowHTML).join("")}</ul>` : `<p class="small muted">无</p>`;
}

function renderUpcoming() {
  const r = DATA.releases || { upcoming: [] };
  const st = DATA.status || {};
  renderYesterday();
  const today = bjToday();
  const rows = r.upcoming.filter((x) => x.bj_date >= today);
  const todays = rows.filter((x) => x.bj_date === today);
  document.getElementById("today").innerHTML = todays.length
    ? `<ul class="up-list">${todays.map(calRowHTML).join("")}</ul>` : `<p class="small muted">无</p>`;
  const later = rows.filter((x) => x.bj_date > today);
  const el = document.getElementById("upcoming");
  if (!later.length) {
    el.innerHTML = `<p class="small muted">${esc(st.calendar_error ? `日历这次没取到：${st.calendar_error}` : "未来几天没有重要发布。")}</p>`;
    return;
  }
  const days = new Map();
  for (const x of later) {
    if (!days.has(x.bj_date)) days.set(x.bj_date, []);
    days.get(x.bj_date).push(x);
  }
  el.innerHTML = [...days.entries()].map(([d, xs]) => {
    const wd = new Date(`${d}T00:00:00Z`).getUTCDay();
    return `<div class="up-day">${esc(d.slice(5))}<span>周${WEEK[wd] ?? ""}</span></div>
      <ul class="up-list">${xs.map(calRowHTML).join("")}</ul>`;
  }).join("") + (r.importance_rule ? `<p class="small muted rule">${esc(r.importance_rule)}</p>` : "");
}

// ---- 第二层：依据 ----
function chartCardHTML(spec) {
  const sub = [spec.unit, spec.last_date ? `最新 ${spec.last_date}` : ""].filter(Boolean).join(" · ");
  return `<article class="card chart" id="c-${spec.id}">
    <h3>${esc(spec.title)}</h3>
    <div class="c-sub">${esc(sub)}</div>
    ${spec.series.length > 1 ? `<div class="legend">${legendHTML(spec)}</div>` : ""}
    <div class="c-body"><canvas role="img" aria-label="${esc(spec.title)}"></canvas></div>
    ${spec.note ? `<div class="c-note">${esc(spec.note)}</div>` : ""}
    <div class="c-foot"><span></span><button type="button" data-table="${spec.id}">看数据表</button></div>
    <div class="c-table" hidden></div>
  </article>`;
}

function renderSections() {
  const root = document.getElementById("sections");
  // 带 side 的板块（半导体：需求端 / 供给端）按边分组，每组前面加一个小标题
  document.getElementById("chips").innerHTML = DATA.sections.map((s, i) =>
    `${s.side && s.side !== DATA.sections[i - 1]?.side ? `<span class="chip-side">${esc(s.side)}</span>` : ""}<a href="#${s.key}">${esc(s.name)}</a>`).join("");
  renderStatusFilter();
  root.innerHTML = DATA.sections.map((s, i) => {
    const dim = DATA.dimensions.find((d) => d.key === s.key) || {};
    const all = s.groups.flatMap((g) => g.charts);
    const core = all.filter((id) => DATA.charts[id].core);
    const more = s.groups.map((g) => ({ name: g.name, charts: g.charts.filter((id) => !DATA.charts[id].core) }))
      .filter((g) => g.charts.length);
    const nMore = more.reduce((a, g) => a + g.charts.length, 0);
    const side = s.side && s.side !== DATA.sections[i - 1]?.side ? `<h2 class="side-title">${esc(s.side)}</h2>` : "";
    return `${side}<section class="dim" id="${s.key}">
      <h2>${esc(s.name)}<span class="tag-state">${esc(dim.label || "")}</span></h2>
      ${dim.vote ? `<p class="dim-vote small">投<b>${esc(dim.vote)}</b>：${esc(dim.vote_why || "")}</p>` : ""}
      ${metricsHTML(dim)}
      ${sectionExtra(s.key)}
      <div class="charts">${core.map((id) => chartCardHTML(DATA.charts[id])).join("")}</div>
      ${nMore ? `<details class="more"><summary>更多图表（${nMore} 张）</summary>
        ${more.map((g) => `<h3 class="group-title">${esc(g.name)}</h3><div class="charts">${g.charts.map((id) => chartCardHTML(DATA.charts[id])).join("")}</div>`).join("")}
      </details>` : ""}
      ${sectionAfter(s.key)}
    </section>`;
  }).join("");

  root.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-table]");
    if (!b) return;
    const box = b.closest(".chart").querySelector(".c-table");
    if (box.hidden) {
      box.innerHTML = tableHTML(DATA.charts[b.dataset.table]);
      box.hidden = false;
      b.textContent = "收起数据表";
    } else {
      box.hidden = true;
      b.textContent = "看数据表";
    }
  });

  // 滚到附近（或展开折叠区）再画，减轻首屏负担
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      io.unobserve(en.target);
      const id = en.target.id.slice(2);
      if (!charts.has(id)) charts.set(id, drawChart(en.target.querySelector("canvas"), DATA.charts[id], { startMs: rangeStartMs() }));
    }
  }, { rootMargin: "400px 0px" });
  root.querySelectorAll(".chart").forEach((el) => io.observe(el));
}

// 从指标名跳到折叠区里的图时，先展开
function openTarget() {
  const id = decodeURIComponent(location.hash.slice(1));
  const el = id && document.getElementById(id);
  if (!el) return;
  const det = el.closest("details");
  if (det && !det.open) det.open = true;
  el.scrollIntoView();
}

function redrawAll() {
  const s = rangeStartMs();
  charts.forEach((c, id) => {
    const cv = c.canvas;
    c.destroy();
    charts.set(id, drawChart(cv, DATA.charts[id], { startMs: s }));
  });
  document.querySelectorAll(".chart").forEach((el) => {
    const spec = DATA.charts[el.id.slice(2)];
    const lg = el.querySelector(".legend");
    if (lg) lg.innerHTML = legendHTML(spec);
  });
}

