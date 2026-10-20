// 宏观页：共用的三层版式在 board.js，这里只放宏观独有的部分（Nowcast 对照、最近发布、数据状态）。
"use strict";

sectionExtra = (key) => (key === "inflation" ? nowcastHTML() : "");

// 克利夫兰联储 Nowcast 与实际的对照，只放在通胀一块
function nowcastHTML() {
  const rows = ((DATA.releases || {}).nowcast || []).map((x) => `<tr><td>${esc(x.measure)}</td><td>${esc(x.period)}</td>
      <td>${x.nowcast === "" ? "—" : fmtNum(+x.nowcast)}</td><td>${x.actual === "" ? "—" : fmtNum(+x.actual)}</td></tr>`).join("");
  if (!rows) return "";
  return `<details class="more"><summary>克利夫兰联储 Nowcast 与实际对照（同比 %）</summary>
    <div class="card rel"><div class="table-wrap"><table class="data"><tr><th>口径</th><th>期间</th><th>Nowcast</th><th>实际</th></tr>${rows}</table></div></div>
  </details>`;
}

// 最近发布：还没有累积数据时整段不显示
function renderRecent() {
  const recent = (DATA.releases || {}).recent || [];
  if (!recent.length) return;
  document.getElementById("recent-wrap").hidden = false;
  const rows = recent.map((x) => `<tr>
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}<div class="small muted">${esc(x.ref || "")}</div></td>
      <td><b>${esc(x.level_text || x.actual_text || "—")}</b></td><td>${esc(x.forecast_text || "—")}</td>
      <td>${x.verdict ? `${x.dir === "pos" ? "↑" : x.dir === "neg" ? "↓" : "="} ${esc(x.verdict)}<div class="small muted">${esc(x.surprise_text)}</div>` : `<span class="muted">${esc(x.status || "无预期")}</span>`}</td>
      <td>${esc(x.previous_text || "—")}</td>
      <td style="text-align:left" class="small">${x.impact ? (x.impact.changed ? "<b>" + esc(x.impact.text) + "</b>" : esc(x.impact.text)) : '<span class="muted">—</span>'}</td>
      <td class="small">${esc(x.market || "—")}</td></tr>`).join("");
  document.getElementById("recent").innerHTML = `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>实际</th><th>预期</th><th>意外</th><th>前值</th><th style="text-align:left">对判断的影响</th><th>市场反应</th></tr>${rows}</table></div>`;
}

// ---- 页脚：方法与说明 ----
function renderNotes() {
  const bits = [`<p>${esc(DATA.method || "")}</p>`];
  bits.push("<p>由固定规则按经济锚点判断，仅供参考，不构成投资建议。</p>");
  document.getElementById("notes").innerHTML = bits.join("");
}

// ---- 数据状态 ----
function renderHealth() {
  const st = DATA.status || {};
  const series = st.series || {};
  const failed = st.failed || [];
  const rows = Object.entries(series).map(([id, v]) =>
    `<tr><td>${esc(id)}</td><td style="text-align:left">${esc(v.name)}</td><td>${esc(v.freq)}</td><td>${esc(v.last_obs || "—")}</td><td class="${v.ok ? "" : "bad"}">${v.ok ? "正常" : "失败/缺失"}</td></tr>`
  ).join("");
  const extra = [
    st.effr_expect_error && `市场隐含 EFFR：${st.effr_expect_error}`,
    st.calendar_error && `经济日历预期：${st.calendar_error}`,
    st.nowcast_error && `克利夫兰联储 Nowcast：${st.nowcast_error}`,
    st.zori_error && `Zillow ZORI：${st.zori_error}`,
    st.dxy_error && `美元指数 DXY：${st.dxy_error}`,
    st.treasury_error && `财政部收益率曲线：${st.treasury_error}`,
  ].filter(Boolean);
  const dxyLine = st.dxy_last
    ? `美元指数 DXY 最新观测 ${esc(st.dxy_last)}（${esc(st.dxy_source || "Yahoo Finance DX-Y.NYB")}）。`
    : "";
  const filled = st.treasury_filled || {};
  const treasuryLine = Object.keys(filled).length
    ? `财政部曲线补了 FRED 尚未发布的交易日：${esc(Object.entries(filled).map(([k, v]) => `${k} ${v}`).join("，"))}。`
    : "";
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${Object.keys(series).length} 条 FRED 序列，${failed.length ? `<span class="bad">${failed.length} 条这次没刷新</span>` : "全部正常"}${extra.length ? `，<span class="bad">${extra.length} 个外部来源失败</span>` : ""}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的序列沿用上次成功下载的数据。${dxyLine}${treasuryLine}${extra.map(esc).join("；")}</p>
      <div class="table-wrap"><table class="data"><tr><th>FRED ID</th><th style="text-align:left">名称</th><th>频率</th><th>最新观测</th><th>状态</th></tr>${rows}</table></div>
    </details>`;
}

async function main() {
  renderNav("macro.html");
  try {
    DATA = await loadJSON("data/macro/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新宏观数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  renderVerdict();
  renderStateLog();
  renderStates();
  renderRange();
  renderSections();
  renderUpcoming();
  renderRecent();
  renderNotes();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
