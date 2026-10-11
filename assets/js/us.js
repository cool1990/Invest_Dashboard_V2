// 美股页：共用的三层版式在 board.js。这里放盈利周报的口径、每周主线，以及个股对照表。
"use strict";

function sourceBanner() {
  const info = DATA.insight || {};
  const br = info.break_date || "2026-08-15";
  const title = `断点 ${br} FactSet→LSEG`;
  const body = info.note
    ? esc(info.note)
    : "两段分开画；断点前后 surprise / 超预期 / 增速等指标不可直接对比。禁止改回 FactSet 或重写旧史。";
  return `<div class="source-break"><b>${esc(title)}</b> — ${body}</div>`;
}

function weeksHTML() {
  const weeks = (DATA.insight || {}).weeks || [];
  if (!weeks.length) return "";
  const latest = weeks[0];
  const rest = weeks.slice(1).map((w) => {
    const theme = w.theme || "这篇没有写主线";
    const judge = w.judgment ? `判断：${w.judgment}` : "";
    return `<li><b>${esc(w.date)}</b> ${esc(w.source)} ${esc(w.quarter)}<br>${esc(theme)}${judge ? `<br><span class="muted">${esc(judge)}</span>` : ""}</li>`;
  }).join("");
  const theme = latest.theme || "这篇没有写主线。";
  const judge = latest.judgment || "这篇没有写综合判断。";
  return `<div class="card evid" style="padding:12px 16px">
    <p class="small muted" style="margin:0 0 6px">本周主线 · ${esc(latest.date)} · ${esc(latest.source)} · ${esc(latest.quarter)}</p>
    <p style="margin:0 0 8px">${esc(theme)}</p>
    <p style="margin:0">${esc(judge)}</p>
    ${rest ? `<details style="margin-top:8px"><summary class="small">更早的周报（${rest.split("<li>").length - 1} 篇）</summary><ul class="week-list">${rest}</ul></details>` : ""}
  </div>`;
}

function namesHTML() {
  const rows = DATA.names || [];
  if (!rows.length) return "";
  const body = rows.map((r) => `<tr>
      <td style="text-align:left">${esc(r.ticker)}${r.core ? ' <span class="tag tag-ref">大盘</span>' : ""}</td>
      <td>${esc(r.forward_pe)}</td>
      <td>${esc(r.target_pe)}</td>
      <td>${r.triggered ? "触发" : "—"}</td>
      <td>${esc(r.revision)}</td>
      <td style="text-align:left">${esc(r.signal)}</td>
      <td>${esc(r.rsi)}</td>
    </tr>`).join("");
  return `<div class="card evid"><p class="small muted" style="padding:10px 16px 0">个股盈利跟踪 ${esc(DATA.names_date || "")}，只列美股。这张表不进指数判断。标「大盘」的是对照用的 8 家，远期市盈率碰到目标市盈率时写「触发」。</p>
    <div class="table-wrap"><table class="data"><tr><th style="text-align:left">代码</th><th>远期市盈率</th><th>目标市盈率</th><th>触发</th><th>30 日修正</th><th style="text-align:left">修正信号</th><th>RSI</th></tr>${body}</table></div></div>`;
}

function renderNotes() {
  document.getElementById("notes").innerHTML = `<p>${esc(DATA.method || "")}</p>
    <p>由固定规则按经济锚点判断，仅供参考，不构成投资建议。新的一周把笔记放进 data/raw/us/insight/，或直接补 data/raw/us/earnings_insight.csv。</p>`;
}

function renderHealth() {
  const st = DATA.status || {};
  const src = st.sources || [];
  const series = st.series || {};
  const bad = src.filter((s) => !s.ok).length + (st.failed || []).length;
  const srcRows = src.map((s) => `<tr><td style="text-align:left">${esc(s.name)}</td><td>${esc(s.last_obs || "—")}</td>
      <td class="${s.ok ? "" : "bad"}">${s.ok ? (s.last_obs ? "正常" : "无数据") : "失败"}</td><td style="text-align:left" class="small">${esc(s.note || "")}</td></tr>`).join("");
  const fredRows = Object.entries(series).map(([id, v]) =>
    `<tr><td style="text-align:left">${esc(id)} ${esc(v.name)}</td><td>${esc(v.last_obs || "—")}</td><td class="${v.ok ? "" : "bad"}">${v.ok ? "正常" : "失败/缺失"}</td><td></td></tr>`).join("");
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${src.length} 个来源、${Object.keys(series).length} 条 FRED 序列，${bad ? `<span class="bad">${bad} 项这次没刷新或失败</span>` : "全部正常"}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。盈利周报不会自动下载，沿用仓库里的表。10 年实际利率和 GDP 用宏观板块已经下载的文件，不在上面重复列。</p>
      <div class="table-wrap"><table class="data"><tr><th style="text-align:left">来源</th><th>最新观测</th><th>状态</th><th style="text-align:left">说明</th></tr>${srcRows}${fredRows}</table></div>
    </details>`;
}

async function main() {
  renderNav("us.html");
  try {
    DATA = await loadJSON("data/us/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新美股数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  sectionExtra = (key) => key === "earnings" ? sourceBanner() : "";
  sectionAfter = (key) => key === "earnings" ? weeksHTML() + namesHTML() : "";
  renderVerdict();
  renderStateLog();
  renderStates();
  renderRange();
  renderSections();
  renderUpcoming();
  renderNotes();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
