// 总览「值得关注」：前端读四个板块的 dashboard.json，不改各板判断规则。
// 每栏最多 5 条，优先级：近 7 天标签变化 → 极端/告警 → 板块内关注 → 数据失败或过期。
"use strict";

const BOARDS = [
  { key: "macro", title: "宏观", href: "macro.html" },
  { key: "semis", title: "半导体与 AI", href: "semis.html" },
  { key: "us", title: "美股", href: "us.html" },
  { key: "crypto", title: "加密", href: "crypto.html" },
];

const LIMIT = 5;
const WINDOW_DAYS = 7;

// 对照 site.css 里 --st-alert / --st-watch 的 status、move 色。
const ALERT_STATUS = new Set([
  "stress", "crisis", "alert", "hot2", "sticky", "worse", "stall", "high",
  "restrictive", "up2", "upshift", "high2", "contract",
]);
const ALERT_MOVE = new Set(["bad2", "hawk2", "wide2"]);
const WATCH_STATUS = new Set(["watch", "elevated", "weak", "tight", "high1", "below", "hot1"]);
const ALERT_PHRASES = [
  "危机", "滞胀风险", "衰退风险", "双杀风险", "见顶风险", "过热",
  "明显转差", "明显走阔", "大幅走阔", "明显转鹰",
  "明显高于目标", "粘性强", "紧缩", "恶化", "停滞或收缩",
];
// 名字里出现这些才单独占一条；上行后期/下行早期是半导体的见顶、转差阶段。
const EXTREME_NAMES = [
  "见顶风险", "滞胀风险", "衰退风险", "双杀风险",
  "上行后期", "下行早期", "过热", "出清", "危机",
];
const HARD_NAMES = ["见顶风险", "滞胀风险", "衰退风险", "双杀风险", "过热", "出清", "危机"];

const WATCH_SERIES = {
  capex_yoy: ["capex_yoy", 0],
  equip_yoy: ["equip", 0],
  analog_yoy: ["analog", 0],
  inv_gap: ["inv_gap", 0],
};

const KIND = { 1: "刚变", 2: "告警", 3: "关注", 4: "数据" };

function parseISO(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
  if (!m) return null;
  return Date.UTC(+m[1], +m[2] - 1, +m[3]);
}

function ageDays(asof, day) {
  const a = parseISO(asof);
  const b = parseISO(day);
  if (a == null || b == null) return null;
  return Math.round((a - b) / 86400000);
}

function boardAsOf(data) {
  if (data && data.asof) return String(data.asof).slice(0, 10);
  const u = data && data.status && data.status.updated_at;
  if (u) return String(u).slice(0, 10);
  return new Date().toISOString().slice(0, 10);
}

function clip(s, n) {
  const t = String(s ?? "").replace(/\s+/g, " ").trim();
  if (t.length <= n) return t;
  return t.slice(0, n - 1) + "…";
}

function fmtVal(text, unit) {
  if (text == null || text === "") return "";
  const t = String(text);
  if (!unit) return t;
  if (String(unit).startsWith("%")) return t + unit;
  return `${t} ${unit}`;
}

function isOverall(row) {
  return row.dim === "overall" || String(row.name || "").startsWith("整体") || row.name === "比特币周期";
}

function sectionKeys(data) {
  const keys = new Set();
  (data.dimensions || []).forEach((d) => keys.add(d.key));
  (data.sections || []).forEach((s) => keys.add(s.key));
  return keys;
}

function anchorFor(data, dim) {
  if (!dim || dim === "overall" || !sectionKeys(data).has(dim)) return "verdict";
  return dim;
}

function statusSev(key) {
  if (key === "crisis" || key === "stress" || key === "alert") return 5;
  if (["hot2", "sticky", "restrictive", "stall", "worse", "contract"].includes(key)) return 4;
  if (["high", "high2", "up2", "upshift"].includes(key)) return 3;
  return 2;
}

function skipMetric(m) {
  if (!m || m.model || m.ref) return true;
  const blob = `${m.anchor || ""} ${m.note || ""}`;
  return blob.includes("不进判断") || blob.includes("模型预测");
}

function phraseHit(text) {
  return ALERT_PHRASES.find((p) => String(text || "").includes(p)) || "";
}

function extremeName(name) {
  return EXTREME_NAMES.find((k) => String(name || "").includes(k)) || "";
}

function curFrom(now) {
  const s = String(now || "").replace(/^\s*\d+(?:\.\d+)?\s*天/, "");
  const m = s.match(/[+-]?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

function prevFrom(now) {
  const m = String(now || "").match(/[（(][^）)]*?([+-]\d+(?:\.\d+)?)/);
  return m ? parseFloat(m[1]) : null;
}

function historyFor(data, id) {
  const spec = WATCH_SERIES[id];
  if (!spec || !data.charts || !data.charts[spec[0]]) return [];
  const ser = (data.charts[spec[0]].series || [])[spec[1]];
  if (!ser || !ser.data) return [];
  return ser.data.map((p) => p[1]).filter((v) => typeof v === "number" && isFinite(v));
}

function atom(p, cur, prev, hist) {
  if (p.includes("连续两季回落") || p.includes("连续两季下降")) {
    if (hist.length < 3) return false;
    const a = hist.slice(-3);
    return a[2] < a[1] && a[1] < a[0];
  }
  if (p.includes("由负转正")) return prev != null && cur != null && prev < 0 && cur > 0;
  if (p.includes("转负")) return cur != null && cur < 0;
  if (p.includes("转正")) return cur != null && cur > 0;
  const fall = p.match(/跌超\s*(\d+(?:\.\d+)?)/);
  if (fall) return cur != null && cur <= -parseFloat(fall[1]);
  const rise = p.match(/涨超\s*(\d+(?:\.\d+)?)/);
  if (rise) return cur != null && cur >= parseFloat(rise[1]);
  let ok = null;
  const lt = p.match(/<\s*(\d+(?:\.\d+)?)/);
  if (lt) ok = cur != null && cur < parseFloat(lt[1]);
  const gt = p.match(/>\s*(\d+(?:\.\d+)?)/);
  if (gt) {
    const g = cur != null && cur > parseFloat(gt[1]);
    ok = ok == null ? g : (ok && g);
  }
  if (p.includes("回落") && !p.includes("连续")) {
    const r = cur != null && prev != null && cur < prev;
    ok = ok == null ? r : (ok && r);
  }
  if (p.includes("继续抬升") || (p.includes("回升") && !p.includes("回升至"))) {
    const r = cur != null && prev != null && cur > prev;
    ok = ok == null ? r : (ok && r);
  }
  return !!ok;
}

function condMet(cond, cur, prev, hist) {
  const parts = String(cond || "").split("或").map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return false;
  return parts.some((p) => {
    if (!p.includes("且")) return atom(p, cur, prev, hist || []);
    return p.split("且").map((s) => s.trim()).filter(Boolean).every((a) => atom(a, cur, prev, hist || []));
  });
}

function metricHit(m) {
  if (skipMetric(m)) return null;
  const sk = m.status && m.status.key;
  const mk = m.move && m.move.key;
  const sl = (m.status && m.status.label) || "";
  const ml = (m.move && m.move.label) || "";
  const labels = [];
  let pri = 0;
  let sev = 0;
  if (sk && ALERT_STATUS.has(sk)) {
    pri = 2;
    sev = Math.max(sev, statusSev(sk));
    labels.push(sl);
  }
  if (mk && ALERT_MOVE.has(mk)) {
    pri = 2;
    sev = Math.max(sev, mk === "wide2" ? 3 : 4);
    labels.push(ml);
  } else if (mk === "wide1") {
    pri = pri || 2;
    sev = Math.max(sev, 2);
    labels.push(ml);
  }
  if (!pri) {
    const ph = phraseHit(sl) || phraseHit(ml);
    if (ph) {
      pri = 2;
      sev = 3;
      labels.push(sl || ml);
    }
  }
  if (!pri && sk && WATCH_STATUS.has(sk)) {
    pri = 3;
    sev = 1;
    labels.push(sl);
  }
  if (!pri) return null;
  return { pri, sev, label: [...new Set(labels.filter(Boolean))].join("、"), m };
}

function groupTitle(dim, alerts) {
  const labels = [...new Set(alerts.flatMap((h) => h.label.split("、")).filter(Boolean))];
  if (labels.length && labels.every((l) => /走阔|收窄/.test(l))) return `${dim.name}：利差走阔`;
  const shown = labels.slice(0, 2).join("、");
  return shown ? `${dim.name}：${shown}` : `${dim.name}告警`;
}

function metricItem(dim, h, di) {
  const m = h.m;
  const anchor = String(m.anchor || "").split("；")[0];
  return {
    pri: h.pri,
    sev: h.sev,
    order: di,
    key: `met:${m.id}`,
    dim: dim.key,
    title: m.name,
    reason: `当前「${h.label}」${anchor ? "。" + anchor : ""}`,
    value: fmtVal(m.text, m.unit),
    date: m.date || "",
    href: m.chart ? `#c-${m.chart}` : `#${dim.key}`,
    hard: !!(m.status && ["crisis", "stress", "alert"].includes(m.status.key)),
  };
}

function pushChanges(data, asof, items) {
  const latest = new Map();
  (data.state_log || []).forEach((row) => {
    if (!row || !row.from || !row.date) return;
    const age = ageDays(asof, row.date);
    if (age == null || age < 0 || age > WINDOW_DAYS) return;
    const k = row.dim || row.name;
    const prev = latest.get(k);
    if (!prev || age < prev.age) latest.set(k, { row, age });
  });
  latest.forEach(({ row, age }) => {
    items.push({
      pri: 1,
      sev: isOverall(row) ? 3 : 1,
      order: isOverall(row) ? 0 : age,
      key: `chg:${row.dim || row.name}`,
      dim: row.dim,
      title: `${row.name}判断变化`,
      reason: `${row.date} 由「${row.from}」变为「${row.to}」`,
      date: row.date,
      href: `#${anchorFor(data, isOverall(row) ? "overall" : row.dim)}`,
    });
  });
}

function pushVerdict(data, items) {
  const name = (data.verdict && data.verdict.name) || (data.position && data.position.name) || "";
  const hit = extremeName(name);
  if (!hit) return;
  const meaning = (data.position && data.position.meaning)
    || (data.verdict && data.verdict.headline)
    || "";
  items.push({
    pri: 2,
    sev: HARD_NAMES.includes(hit) ? 5 : 2,
    order: -1,
    key: "verdict",
    title: name,
    reason: meaning,
    href: "#verdict",
    hard: HARD_NAMES.includes(hit),
  });
}

function pushMetrics(data, items) {
  (data.dimensions || []).forEach((dim, di) => {
    const hits = (dim.metrics || []).map(metricHit).filter(Boolean);
    const alerts = hits.filter((h) => h.pri === 2);
    const watches = hits.filter((h) => h.pri === 3);
    if (alerts.length === 1) items.push(metricItem(dim, alerts[0], di));
    else if (alerts.length > 1) {
      items.push({
        pri: 2,
        sev: Math.max(...alerts.map((h) => h.sev)),
        order: di,
        key: `dim:${dim.key}`,
        dim: dim.key,
        title: groupTitle(dim, alerts),
        reason: alerts.map((h) => `${h.m.name} ${fmtVal(h.m.text, h.m.unit)}，${h.label}`).join("；"),
        date: alerts.map((h) => h.m.date).filter(Boolean).sort().slice(-1)[0] || "",
        href: `#${dim.key}`,
        hard: alerts.some((h) => h.m.status && ["crisis", "stress", "alert"].includes(h.m.status.key)),
      });
    }
    watches.forEach((h) => {
      if (alerts.some((a) => a.m.id === h.m.id)) return;
      items.push(metricItem(dim, h, di + 20));
    });
    const lab = phraseHit(dim.label);
    if (!lab) return;
    if (items.some((it) => it.dim === dim.key && it.pri <= 2)) return;
    items.push({
      pri: 2,
      sev: 3,
      order: di,
      key: `dlabel:${dim.key}`,
      dim: dim.key,
      title: `${dim.name}：${dim.label}`,
      reason: dim.head || "",
      href: `#${dim.key}`,
      hard: HARD_NAMES.some((k) => String(dim.label).includes(k)),
    });
  });
}

function pushMacroUpcoming(data, items) {
  ((data.releases || {}).upcoming || []).forEach((u) => {
    if (!u || u.done) return;
    const stars = u.importance && u.importance.stars;
    const sc = u.scenario;
    if (!(stars >= 4) || !sc || !sc.affects || !sc.affects.length) return;
    items.push({
      pri: 3,
      sev: 3,
      order: 0,
      key: `up:${u.release_at || u.title}`,
      title: u.title,
      reason: `${stars} 星，情景可能改标签。${sc.text || ""}`,
      value: u.forecast_text ? `预期 ${u.forecast_text}` : "",
      date: u.bj_date || "",
      href: "#up-title",
    });
  });
}

function pushSemisWatch(data, items) {
  const list = (data.position && data.position.watch) || [];
  list.forEach((w) => {
    const cur = curFrom(w.now);
    const prev = prevFrom(w.now);
    if (!condMet(w.cond, cur, prev, historyFor(data, w.id))) return;
    items.push({
      pri: 3,
      sev: 3,
      order: 1,
      key: `sw:${w.id}`,
      title: w.name,
      reason: `转向信号已出现（${w.cond}）：${w.meaning}。现在 ${w.now}`,
      value: w.now || "",
      href: "#watch",
    });
  });
}

function usHits(dim) {
  const parts = String(dim.label || "").split("·").map((s) => s.trim()).filter(Boolean);
  if (dim.key === "earnings") {
    return parts.filter((p) => !p.includes("小幅") && /偏弱|转弱|偏空|下修/.test(p));
  }
  if (dim.key === "valuation") {
    return parts.filter((p) => /偏贵|^贵$|双杀/.test(p));
  }
  if (dim.key === "sentiment") {
    return parts.filter((p) => {
      if (/未过线/.test(p)) return false;
      return /紧张|面窄|恐惧|悲观|杠杆扩张|杠杆去化|去杠杆|集中/.test(p);
    });
  }
  return [];
}

function pushUsWatch(data, items) {
  (data.dimensions || []).forEach((dim, di) => {
    const hits = usHits(dim);
    if (!hits.length) return;
    if (items.some((it) => it.key === `chg:${dim.key}`)) return;
    const nameHit = extremeName(dim.label);
    items.push({
      pri: nameHit && dim.key !== "sentiment" ? 2 : 3,
      sev: nameHit ? 4 : 2,
      order: di,
      key: `us:${dim.key}`,
      dim: dim.key,
      title: `${dim.name}：${hits.join("、")}`,
      reason: dim.head || hits.join("、"),
      href: `#${dim.key}`,
      hard: !!(nameHit && HARD_NAMES.includes(nameHit)),
    });
  });
  const vhit = extremeName((data.verdict || {}).name);
  if (vhit && !items.some((it) => it.key === "verdict")) {
    items.push({
      pri: 2,
      sev: 5,
      order: -1,
      key: "verdict",
      title: data.verdict.name,
      reason: data.verdict.headline || "",
      href: "#verdict",
      hard: HARD_NAMES.includes(vhit),
    });
  }
}

function pushCryptoWatch(data, items) {
  (data.dimensions || []).forEach((dim, di) => {
    (dim.metrics || []).forEach((m) => {
      const w = m.watch;
      if (!w || (w.mark || "注意") !== "注意") return;
      const why = w.why || "";
      items.push({
        pri: 3,
        sev: w.hi ? 3 : 1,
        order: di,
        key: `cw:${m.id}`,
        dim: dim.key,
        title: m.name,
        reason: why,
        value: fmtVal(m.text, m.unit),
        date: m.date || "",
        href: m.chart ? `#c-${m.chart}` : `#${dim.key}`,
        hard: !!w.hi && HARD_NAMES.some((k) => why.includes(k) || String(dim.label || "").includes(k)),
      });
    });
  });
}

function pushFreshness(data, items) {
  const bits = [];
  const st = data.status || {};
  (st.failed || []).forEach((f) => {
    if (typeof f === "string") bits.push(f);
    else if (f) bits.push(f.name || f.id || f.key || "序列");
  });
  (st.sources || []).forEach((s) => {
    if (!s) return;
    if (s.ok === false) bits.push(s.name || s.key || "来源");
    else if (/失败|过期/.test(s.note || "")) bits.push(s.name || s.key || "来源");
  });
  Object.entries(st.series || {}).forEach(([id, v]) => {
    if (v && v.ok === false) bits.push(v.name || id);
  });
  ["calendar_error", "nowcast_error", "effr_expect_error", "zori_error", "dxy_error"].forEach((k) => {
    if (st[k]) bits.push(k.replace(/_error$/, "") + " 读取失败");
  });
  const uniq = [...new Set(bits.filter(Boolean))];
  if (!uniq.length) return;
  const shown = uniq.slice(0, 3);
  items.push({
    pri: 4,
    sev: 1,
    order: 0,
    key: "fresh",
    title: uniq.length === 1 ? `${shown[0]}未能更新` : `${uniq.length} 项关键数据未能更新`,
    reason: uniq.length === 1
      ? "状态标明失败或过期，页面上的数可能还是上一次的。"
      : `${shown.join("、")}${uniq.length > shown.length ? `等 ${uniq.length} 项` : ""}标明失败或过期。`,
    href: "#health",
  });
}

function rank(items) {
  const sorted = items.slice().sort((a, b) =>
    a.pri - b.pri || (b.sev || 0) - (a.sev || 0) || (a.order ?? 50) - (b.order ?? 50));
  const seen = new Set();
  const out = [];
  sorted.forEach((it) => {
    if (seen.has(it.key) || out.length >= LIMIT) return;
    seen.add(it.key);
    out.push(it);
  });
  return out;
}

function collectBoard(board, data) {
  const asof = boardAsOf(data);
  const items = [];
  pushChanges(data, asof, items);
  pushVerdict(data, items);
  pushMetrics(data, items);
  if (board.key === "macro") pushMacroUpcoming(data, items);
  if (board.key === "semis") pushSemisWatch(data, items);
  if (board.key === "us") pushUsWatch(data, items);
  if (board.key === "crypto") pushCryptoWatch(data, items);
  pushFreshness(data, items);
  return rank(items);
}

function envView(data) {
  const v = data.verdict || {};
  const name = v.name || "—";
  let sub = "";
  if (data.position && data.position.meaning) sub = data.position.meaning;
  else if (v.headline && v.headline !== name) {
    sub = v.headline.startsWith(name) ? v.headline.slice(name.length).replace(/^[：:\s]+/, "") : v.headline;
  } else if (v.lines && v.lines[0]) {
    sub = `${v.lines[0].k}：${v.lines[0].t}`;
  }
  return { name, sub: clip(sub, 48) };
}

function itemTone(board, it) {
  if (it.pri === 4) return "watch";
  if (board.key === "macro") return "alert";
  if (it.hard) return "alert";
  return "watch";
}

function itemHTML(board, it) {
  const tone = itemTone(board, it);
  const metaBits = [];
  if (it.value) metaBits.push(it.value);
  if (it.date && it.pri !== 1) metaBits.push(it.date);
  const href = board.href + (it.href || "");
  return `<li><a class="attn-item ${tone}" href="${esc(href)}">
    <div class="attn-kind">${KIND[it.pri] || "关注"}</div>
    <div class="attn-title">${esc(it.title)}</div>
    <div class="attn-why">${esc(clip(it.reason, 140))}</div>
    ${metaBits.length ? `<div class="attn-meta">${esc(metaBits.join(" · "))}</div>` : ""}
  </a></li>`;
}

function renderEnv(rows) {
  const el = document.getElementById("env");
  el.innerHTML = rows.map(({ board, data, error }) => {
    if (error || !data) {
      return `<a href="${board.href}"><div class="env-k">${esc(board.title)}</div><div class="env-name">—</div><div class="env-sub">数据还没生成</div></a>`;
    }
    const v = envView(data);
    return `<a href="${board.href}#verdict"><div class="env-k">${esc(board.title)}</div><div class="env-name">${esc(v.name)}</div>${v.sub ? `<div class="env-sub">${esc(v.sub)}</div>` : ""}</a>`;
  }).join("");
}

function renderAttn(rows) {
  const el = document.getElementById("attn");
  el.innerHTML = rows.map(({ board, data, error }) => {
    let body;
    if (error || !data) body = `<p class="attn-empty">数据还没生成</p>`;
    else {
      const items = collectBoard(board, data);
      body = items.length
        ? `<ul class="attn-list">${items.map((it) => itemHTML(board, it)).join("")}</ul>`
        : `<p class="attn-empty">暂无值得关注</p>`;
    }
    const n = (!error && data) ? collectBoard(board, data).length : 0;
    return `<section class="card attn-col" aria-labelledby="attn-${board.key}">
      <h2 id="attn-${board.key}"><a href="${board.href}">${esc(board.title)}</a>${n ? `<span class="muted small">${n} 条</span>` : ""}</h2>
      ${body}
    </section>`;
  }).join("");
}

async function main() {
  renderNav("index.html");
  const rows = await Promise.all(BOARDS.map(async (board) => {
    try {
      return { board, data: await loadJSON(`data/${board.key}/dashboard.json`) };
    } catch (error) {
      return { board, error };
    }
  }));
  renderEnv(rows);
  renderAttn(rows);
}

const OverviewAPI = { collectBoard, condMet, curFrom, prevFrom, BOARDS, envView };
if (typeof window !== "undefined") window.Overview = OverviewAPI;
if (typeof document !== "undefined") main();
