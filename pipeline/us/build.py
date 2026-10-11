"""把盈利笔记、情绪序列、FRED、融资余额和持仓权重组装成 data/us/dashboard.json。"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .. import series as ts
from ..page import Chart, Line, Metric, metric_json
from ..series import Series
from . import importance
from . import interpret as I
from . import insight
from .indicators import CORE, MANUAL_PE

MIN_N = 4  # 中位数至少要有这么多家，避免只剩一两家时被单一个股带着走
UPCOMING_DAYS = 14
CORE_SET = set(CORE)


def _fl(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _ok_rev(r: dict) -> bool:
    if r.get("status") == "NEGATIVE_EPS":
        return False
    if "样本不足" in (r.get("revision_signal") or ""):
        return False
    if "LOW_SAMPLE" in (r.get("flags") or ""):
        return False
    return _fl(r.get("revision_30d")) is not None


def _ok_pe(r: dict) -> bool:
    if r.get("status") == "NEGATIVE_EPS":
        return False
    pe = _fl(r.get("forward_pe"))
    return pe is not None and pe > 0


def _q(s: Series) -> Series:
    """季度序列统一记到季初，方便和差 4 期的同比对齐。"""
    return ts.clean((ts.quarter_start(d), v) for d, v in s)


@dataclass
class Sources:
    fred: dict[str, Series] = field(default_factory=dict)
    earnings: list[dict] = field(default_factory=list)
    sentiment: list[dict] = field(default_factory=list)
    calendar: dict = field(default_factory=dict)
    margin: list[dict] = field(default_factory=list)
    concentration: list[dict] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    insight: list[dict] = field(default_factory=list)


class UsBuilder:
    def __init__(self, src: Sources, asof: date | None = None, now: datetime | None = None):
        self.src = src
        self.asof = asof or date.today()
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}

    def fred(self, sid: str) -> Series:
        return self.src.fred.get(sid) or []

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    def manual(self, key: str) -> Series:
        return ts.clean((_d(r["date"]), _fl(r["value"])) for r in self.src.manual
                        if r.get("key") == key and _fl(r.get("value")) is not None)

    def sent(self, sid: str) -> Series:
        pts = []
        for r in self.src.sentiment:
            if r.get("series_id") != sid:
                continue
            v = _fl(r.get("value"))
            if v is None:
                continue
            when = (r.get("obs_date") or r.get("date") or "")[:10]
            if not when:
                continue
            pts.append((_d(when), v, r.get("date") or ""))
        pts.sort(key=lambda x: (x[0], x[2]))
        return ts.clean((d, v) for d, v, _ in pts)

    def by_date(self) -> list[tuple[str, list[dict]]]:
        buckets: dict[str, list[dict]] = {}
        for r in self.src.earnings:
            if r.get("date") and r.get("ticker"):
                buckets.setdefault(r["date"], []).append(r)
        return sorted(buckets.items())

    def _split(self, key: str, *, pct_only: bool = False) -> tuple[list, list]:
        fact, lseg = [], []
        for r in self.src.insight:
            if pct_only and key == "reported" and r.get("reported_kind") != "pct":
                continue
            v = r.get(key)
            if v is None:
                continue
            (lseg if r.get("source") == "LSEG" else fact).append((_d(r["date"]), float(v)))
        return fact, lseg

    def _both(self, key: str, *, pct_only: bool = False):
        fact, lseg = self._split(key, pct_only=pct_only)
        return fact + lseg

    def _earn_view(self) -> dict:
        rows = [r for r in self.src.insight if r.get("date")]
        if not rows:
            return {}
        last = rows[-1]
        prev = rows[-2] if len(rows) > 1 else None
        same = prev if prev and prev.get("source") == last.get("source") else None
        reported_pct = last.get("reported") if last.get("reported_kind") == "pct" else None
        reported_n = last.get("reported") if last.get("reported_kind") == "count" else None
        return {
            "source": last.get("source"),
            "quarter": last.get("quarter"),
            "reported_pct": reported_pct,
            "reported_n": reported_n,
            "eps_above": last.get("eps_above"),
            "eps_surprise": last.get("eps_surprise"),
            "q_net": last.get("q_net"),
            "q_pos": last.get("q_pos"),
            "y_pos": last.get("y_pos"),
            "q_growth": last.get("q_growth"),
            "y_growth": last.get("y_growth"),
            "y_prev": None if same is None else same.get("y_growth"),
            "rev_up": last.get("rev_up") if last.get("rev_up") is not None else insight.revision_counts(last.get("theme") or "")[0],
            "rev_down": last.get("rev_down") if last.get("rev_down") is not None else insight.revision_counts(last.get("theme") or "")[1],
            "fwd_eps_chg": last.get("fwd_eps_chg"),
            "source_break": bool(prev and prev.get("source") != last.get("source")),
        }

    # -- 盈利 -----------------------------------------------------------------
    def earnings(self):
        days = []
        watch: Series = []
        pe_days = []
        for d, rows in self.by_date():
            core = [r for r in rows if r["ticker"] in CORE_SET and _ok_rev(r)]
            if len(core) >= MIN_N:
                days.append({"date": _d(d), "rev": statistics.median(_fl(r["revision_30d"]) for r in core),
                             "n": len(core),
                             "n_up": sum(r["revision_signal"] == "强上修" for r in core),
                             "n_down": sum(r["revision_signal"] == "强下修" for r in core)})
            us = [r for r in rows if _ok_rev(r)]
            if len(us) >= MIN_N:
                watch.append((_d(d), statistics.median(_fl(r["revision_30d"]) for r in us)))
            core_pe = [r for r in rows if r["ticker"] in CORE_SET and _ok_pe(r)]
            if len(core_pe) >= MIN_N:
                pe_days.append((_d(d), statistics.median(_fl(r["forward_pe"]) for r in core_pe)))
        self.pe_basket: Series = [(d, v) for d, v in pe_days]
        rev: Series = [(x["date"], x["rev"]) for x in days]
        view = self._earn_view()
        st = I.earnings_state(view)
        self.insight_view = self._weeks()
        gap = "断点 2026-08-15 FactSet→LSEG。两段分开画；断口不是真实跳变，surprise 等指标断点前后不可直接对比。"
        q_f, q_l = self._split("q_growth")
        y_f, y_l = self._split("y_growth")
        pos_f, pos_l = self._split("q_pos")
        above_f, above_l = self._split("eps_above")
        sur_f, sur_l = self._split("eps_surprise")
        net_f, net_l = self._split("q_net")
        rep_f, rep_l = self._split("reported", pct_only=True)
        guide = self._both("q_guide")
        y_pos = self._both("y_pos")
        fwd = self._both("fwd_eps_chg")
        spx_chg = self._both("spx_chg")
        rev_net = [(_d(r["date"]), r["rev_up"] - r["rev_down"])
                   for r in self.src.insight if r.get("rev_up") is not None and r.get("rev_down") is not None]
        profits = ts.pct_change(_q(self.fred("CPATAX")), 4, "Q")
        charts = self.add(
            Chart("q_growth", "标普 500：季度 EPS 增速", "%",
                  [Line("FactSet", q_f), Line("LSEG", q_l)], core=True,
                  note=gap + " 跟踪的季度一换，增速会整个跳一档，那是新季度，不是同一季被上修。"),
            Chart("y_growth", "标普 500：年度 EPS 增速", "%",
                  [Line("FactSet", y_f), Line("LSEG", y_l)], core=True,
                  note=gap + " 同一来源内，较上一篇超过 ±0.5 个百分点才算上修或下修。"),
            Chart("q_pos", "季度正面指引占比", "%",
                  [Line("FactSet", pos_f), Line("LSEG", pos_l)], core=True,
                  note="正面家数占已给指引的比例。净家数大于 0 为指引偏多。"),
            Chart("eps_above", "EPS 超预期比例", "%",
                  [Line("FactSet", above_f), Line("LSEG", above_l)], core=True,
                  note=gap + " 披露比例不到 50%，或早期笔记只写了家数时，不据此判断兑现。达到 80% 为兑现强。"),
            Chart("eps_surprise", "EPS Surprise", "%",
                  [Line("FactSet", sur_f), Line("LSEG", sur_l)], core=True,
                  note=gap + " FactSet 常见百分之十几到三十，LSEG 同一季大约 +8%；这是口径切换，不是惊喜消失。"),
            Chart("q_net", "季度净指引", "家",
                  [Line("FactSet", net_f, "bar"), Line("LSEG", net_l, "bar")],
                  note="正面家数减负面家数。大于 0 为偏多。"),
            Chart("reported", "实际披露比例", "%",
                  [Line("FactSet", rep_f), Line("LSEG", rep_l)],
                  note="只画百分比。新财报季开头会从接近 100% 掉到个位数，那是新季度开始报。早期的 19、33、65 是家数，不画在这张图上。"),
            Chart("q_guide", "季度指引家数", "家", [Line("已给指引", guide, "bar")],
                  note="新季度的预告会重新从很少几家累计，家数变少不是指引变差。"),
            Chart("y_pos", "年度正面指引占比", "%", [Line("FactSet", y_pos)],
                  note="LSEG 的周报这列是空的，所以图只到 2026-08-07。"),
            Chart("rev_net", "FY1 上修次数减下修次数", "次", [Line("净次数", rev_net, "bar")],
                  note="只在主线写了「上修次数/下修次数」的那一周才有。净占比不到 ±10 个百分点，只记小幅，不改成上修或下修。"),
            Chart("fwd_eps", "Forward EPS 变化", "%", [Line("周报", fwd, "bar")],
                  note="FactSet 周报里的远期 EPS 变化。LSEG 这列多半是空的。不进判断。"),
            Chart("spx_chg", "周报里的标普涨幅", "%", [Line("周报", spx_chg, "bar")],
                  note="周报原文的指数涨幅，不是本站自己算的。LSEG 期间多半是空的。不进判断。"),
            Chart("rev", "个股：下财年 EPS 30 日修正中位数", "%",
                  [Line("核心篮子", rev), Line("观察名单", watch, dash=True)],
                  note="8 家大盘股的中位数，只用来对照指数周报，不改指数的盈利标签。样本不足或 EPS 为负的不计入。"),
            Chart("profits", "税后企业利润同比", "%", [Line("同比", profits)],
                  start=date(1990, 1, 1), note="FRED 税后企业利润（CPATAX），全经济口径，不是标普每股盈利。不改盈利标签。"),
        )
        metrics = [
            Metric("q_growth", "季度 EPS 增速", self._both("q_growth"), "%", "{:.1f}", "O", "q_growth",
                   note=view.get("quarter") or ""),
            Metric("y_growth", "年度 EPS 增速", self._both("y_growth"), "%", "{:.1f}", "O", "y_growth"),
            Metric("q_net", "季度净指引", self._both("q_net"), "家", "{:+.0f}", "O", "q_net"),
            Metric("q_pos", "季度正面指引", self._both("q_pos"), "%", "{:.1f}", "O", "q_pos"),
            Metric("q_guide", "季度指引家数", guide, "家", "{:.0f}", "O", "q_guide"),
            Metric("reported", "实际披露", rep_f + rep_l, "%", "{:.1f}", "O", "reported"),
            Metric("eps_above", "EPS 超预期", self._both("eps_above"), "%", "{:.1f}", "O", "eps_above"),
            Metric("eps_surprise", "EPS Surprise", self._both("eps_surprise"), "%", "{:+.1f}", "O", "eps_surprise",
                   note=f"{view.get('source') or ''} 口径，不和另一种来源比".strip()),
            Metric("rev_net", "上修减下修", rev_net, "次", "{:+.0f}", "O", "rev_net"),
            Metric("rev", "个股篮子修正中位数", rev, "%", "{:+.2f}", "D", "rev", ref=True),
            Metric("profits_yoy", "税后企业利润同比", profits, "%", "{:+.1f}", "Q", "profits", ref=True),
        ]
        if view.get("y_pos") is not None:
            metrics.append(Metric("y_pos", "年度正面指引", y_pos, "%", "{:.1f}", "O", "y_pos"))
        core_ids = ("q_growth", "y_growth", "q_pos", "eps_above", "eps_surprise")
        groups = [("指数盈利", [c for c in charts if c in core_ids]),
                  ("更多", [c for c in charts if c not in core_ids])]
        self._names()
        return groups, metrics, st

    def _weeks(self) -> dict:
        rows = list(reversed(self.src.insight))
        weeks = [{
            "date": r["date"], "source": r.get("source") or "", "quarter": r.get("quarter") or "",
            "theme": r.get("theme") or "", "judgment": r.get("judgment") or "",
        } for r in rows]
        return {
            "break_date": insight.BREAK.isoformat(),
            "note": "断点 2026-08-15 FactSet→LSEG。2026-08-07 及之前 FactSet《Earnings Insight》，此后 LSEG I/B/E/S"
                    "《This Week in Earnings》。EPS Surprise 约从 +29% 落到 +8.5% 是口径切换不是惊喜消失；"
                    "断点前后 surprise/超预期/增速不可直接对比，禁止改回 FactSet 或重写旧史。",
            "weeks": weeks,
        }

    def _names(self) -> None:
        days = self.by_date()
        if not days:
            self.names, self.names_date = [], ""
            return
        d, rows = days[-1]
        order = {t: i for i, t in enumerate(CORE)}

        def cell_num(r, key, digits=2):
            x = _fl(r.get(key))
            return f"{x:.{digits}f}" if x and x > 0 else "—"

        def cell_rev(r):
            x = _fl(r.get("revision_30d"))
            return f"{x:+.2f}%" if x is not None else "—"

        def cell_rsi(r):
            x = _fl(r.get("rsi"))
            return f"{x:.0f}" if x is not None else "—"

        rows = sorted(rows, key=lambda r: (0 if r["ticker"] in CORE_SET else 1, order.get(r["ticker"], 99), r["ticker"]))
        self.names_date = d
        self.names = [{"ticker": r["ticker"], "core": r["ticker"] in CORE_SET,
                       "forward_pe": cell_num(r, "forward_pe"), "target_pe": cell_num(r, "target_pe"),
                       "triggered": str(r.get("triggered") or "").strip() in {"1", "true", "True"},
                       "revision": cell_rev(r), "signal": r.get("revision_signal") or "—", "rsi": cell_rsi(r)}
                      for r in rows]

    # -- 估值 -----------------------------------------------------------------
    def valuation(self):
        basket: Series = getattr(self, "pe_basket", [])
        manual = self.manual(MANUAL_PE)
        fact_pe, lseg_pe = self._split("forward_pe")
        weekly = fact_pe + lseg_pe
        if manual:
            use, source = manual, "manual"
        elif weekly:
            use, source = weekly, "insight"
        else:
            use, source = basket, "basket"
        real = self.fred("DFII10")
        erp: Series = []
        real_at: Series = []
        for d, v in use:
            ry = ts.asof(real, d, max_gap_days=10)
            if ry is not None and v:
                erp.append((d, 100.0 / v - ry))
                real_at.append((d, ry))
        ey = [(d, 100.0 / v) for d, v in use]
        vstate = {"pe": use[-1][1] if use else None, "source": source, "manual": source == "manual",
                  "erp": erp[-1][1] if erp else None, "real": real_at[-1][1] if real_at else None}
        st = I.valuation_state(vstate)
        real_w = ts.to_weekly(real)
        buffett = ts.combine(lambda m, g: m / 1000.0 / g * 100, _q(self.fred("NCBEILQ027S")), _q(self.fred("GDP")))
        if source == "manual":
            lines = [Line("手工标普", manual), Line("盈利周报", weekly, dash=True), Line("个股中位数", basket, dash=True)]
            pe_note = "判断用 data/us/manual.csv 里的 spx_fwd_pe。周报和个股中位数只作参考。"
        elif source == "insight":
            lines = [Line("FactSet", fact_pe), Line("LSEG", lseg_pe), Line("个股中位数", basket, dash=True)]
            pe_note = "标普 500 未来四季市盈率，来自盈利周报。断点 2026-08-15 FactSet→LSEG，两段分开画。个股中位数不进判断。"
        else:
            lines = [Line("个股中位数", basket)]
            pe_note = "还没有标普周报的远期市盈率，暂用 8 家大盘股的中位数。"
        charts = self.add(
            Chart("pe", "标普 500 远期市盈率", "倍", lines, core=True, note=pe_note),
            Chart("erp", "股权风险溢价", "百分点", [Line("盈利收益率 − 实际利率", erp)], core=True,
                  note="盈利收益率 = 100 / 远期市盈率。低于 2 为贵，高于 4 为便宜。实际利率用 FRED 10 年期 TIPS（DFII10）。"
                       "断点 2026-08-15 FactSet→LSEG：市盈率来源不同，断口处的变化不要单独解读。"),
            Chart("ey", "盈利收益率与 10 年实际利率", "%",
                  [Line("盈利收益率", ey), Line("10 年实际利率", ts.to_weekly(real) if len(real) > 30 else real, dash=True)],
                  note="两条线的差就是上面的风险溢价。"),
            Chart("spx", "标普 500", "指数", [Line("收盘", ts.to_weekly(self.fred("SP500")))],
                  start=date(2016, 1, 1), note="FRED SP500，日频压成周频。不进判断。"),
            Chart("buffett", "非金融企业股权市值 / GDP", "%", [Line("市值 / GDP", buffett)],
                  start=date(1990, 1, 1), note="FRED 股权负债（NCBEILQ027S）除以名义 GDP。这是市值相对经济体量，不是远期市盈率，不进判断。"),
            Chart("real", "10 年期实际利率", "%", [Line("TIPS", real_w)], start=date(2003, 1, 1)),
        )
        freq = "D" if source == "basket" else "O"
        name = {"manual": "远期市盈率（手工）", "insight": "标普远期市盈率", "basket": "远期市盈率"}[source]
        metrics = [
            Metric("pe", name, use, "倍", "{:.1f}", freq, "pe"),
            Metric("erp", "股权风险溢价", erp, "百分点", "{:.2f}", freq, "erp"),
            Metric("real", "10 年期实际利率", real_at, "%", "{:.2f}", "D", "real"),
        ]
        if source != "basket":
            metrics.append(Metric("basket_pe", "个股篮子远期市盈率中位数", basket, "倍", "{:.1f}", "D", "pe", ref=True))
        groups = [("估值", [c for c in charts if c in ("pe", "erp", "ey")]),
                  ("更多", [c for c in charts if c in ("spx", "buffett", "real")])]
        return groups, metrics, st

    # -- 情绪 -----------------------------------------------------------------
    def sentiment(self):
        fred_vix = self.fred("VIXCLS")
        note_vix = self.sent("vix")
        if fred_vix and note_vix:
            vix = fred_vix + [p for p in note_vix if p[0] > fred_vix[-1][0]]
        else:
            vix = fred_vix or note_vix
        cnn, aaii = self.sent("cnn_fg"), self.sent("aaii")
        breadth = self.sent("spx_breadth_200")
        margin = ts.clean((_d(r["date"]), _fl(r["debit"])) for r in self.src.margin if _fl(r.get("debit")) is not None)
        margin_yoy = ts.pct_change(margin, 12, "M")
        spx_yoy = ts.pct_change(ts.to_monthly(self.fred("SP500"), "last"), 12, "M")
        gap = ts.combine(lambda m, s: m - s, margin_yoy, spx_yoy)
        conc = ts.clean((_d(r["date"]), _fl(r["top10"])) for r in self.src.concentration if _fl(r.get("top10")) is not None)
        latest_conc = self.src.concentration[-1] if self.src.concentration else {}
        st = I.sentiment_state({
            "vix": vix[-1][1] if vix else None,
            "cnn": cnn[-1][1] if cnn else None,
            "aaii": aaii[-1][1] if aaii else None,
            "breadth": breadth[-1][1] if breadth else None,
            "margin_yoy": margin_yoy[-1][1] if margin_yoy else None,
            "margin_gap": gap[-1][1] if gap and margin_yoy and gap[-1][0] == margin_yoy[-1][0] else None,
            "top10": conc[-1][1] if conc else None,
        })
        who = {"IVV": "iShares IVV", "SPY": "SPDR SPY"}.get(latest_conc.get("source", ""), "ETF")
        names = (latest_conc.get("names") or "").replace(",", "、")
        charts = self.add(
            Chart("vix", "VIX", "指数", [Line("VIX", ts.to_weekly(vix))], core=True, start=date(1990, 1, 1),
                  note="FRED VIXCLS，日频压成周频。低于 15 平静，高于 25 紧张。笔记里更新的日子会接到末尾。"),
            Chart("breadth", "标普 500：站上 200 日均线的比例", "%",
                  [Line("200 日", breadth), Line("50 日", self.sent("spx_breadth_50"), dash=True),
                   Line("20 日", self.sent("spx_breadth_20"), dash=True)], core=True,
                  note="来自每日笔记，历史很短。低于 40% 为面窄。20 日和 50 日只作参考。"),
            Chart("margin", "客户融资余额同比", "%",
                  [Line("融资余额", margin_yoy), Line("标普 500", spx_yoy, dash=True)], core=True,
                  start=date(1997, 1, 1),
                  note="FINRA 保证金账户借方余额的同比，对照标普指数同比。融资高过指数 10 个百分点以上，视为比指数涨得更快。"),
            Chart("top10", "标普 500 前十大权重", "%", [Line("前十大合计", conc)], core=True,
                  note=f"来自 {who} 日持仓，前 10 只权相加。达到 30% 为集中。历史从第一次成功下载开始。"
                       + (f" 当前：{names}。" if names else "")),
            Chart("margin_level", "客户融资余额", "十亿美元", [Line("借方余额", ts.scale(margin, 0.001), "bar")],
                  start=date(1997, 1, 1), note="FINRA，月频，原表单位是百万美元。"),
            Chart("cnn", "CNN 恐贪与 AAII 牛熊差", "点",
                  [Line("CNN 恐贪", cnn), Line("AAII 牛熊差", aaii, dash=True)],
                  note="CNN 是 0–100。AAII 是牛市比例减熊市比例（百分点），和 CNN 不是同一个刻度，放在一起只为对照方向。"),
            Chart("rsi", "指数 RSI(14)", "点",
                  [Line("标普 500", self.sent("spx_rsi")), Line("纳斯达克", self.sent("nasdaq_rsi"), dash=True)],
                  note="每日笔记里的 Wilder RSI。不进判断。"),
            Chart("ndx_breadth", "纳斯达克 100 参与度", "%",
                  [Line("200 日", self.sent("ndx_breadth_200")), Line("50 日", self.sent("ndx_breadth_50"), dash=True)],
                  note="参考，不进判断。"),
        )
        metrics = [
            Metric("vix", "VIX", vix, "", "{:.1f}", "D", "vix"),
            Metric("cnn", "CNN 恐贪", cnn, "", "{:.0f}", "D", "cnn"),
            Metric("aaii", "AAII 牛熊差", aaii, "百分点", "{:+.1f}", "O", "cnn"),
            Metric("breadth", "标普站上 200 日均线", breadth, "%", "{:.0f}", "D", "breadth"),
            Metric("margin_yoy", "融资余额同比", margin_yoy, "%", "{:+.1f}", "M", "margin"),
            Metric("margin_gap", "融资同比 − 标普同比", gap, "百分点", "{:+.1f}", "M", "margin"),
            Metric("top10", "前十大权重", conc, "%", "{:.1f}", "D", "top10",
                   note=(who + ("：" + names if names else ""))),
        ]
        groups = [("情绪", [c for c in charts if c in ("vix", "breadth", "margin", "top10")]),
                  ("更多", [c for c in charts if c in ("margin_level", "cnn", "rsi", "ndx_breadth")])]
        return groups, metrics, st

    # -- 日历 -----------------------------------------------------------------
    def releases(self) -> dict:
        watch = {r["ticker"] for r in self.src.earnings}
        today_bj = (self.now + timedelta(hours=8)).date()
        end = today_bj + timedelta(days=UPCOMING_DAYS)
        up = []
        for e in (self.src.calendar or {}).get("events", []):
            imp = importance.rate(e.get("title", ""), watch)
            if not imp:
                continue
            try:
                d = _d(e["date"])
            except (KeyError, ValueError):
                continue
            if not (today_bj <= d <= end):
                continue
            cons = e.get("consensus") or ""
            up.append({"bj_date": d.isoformat(), "bj": f"{d.isoformat()} {e.get('time_bj') or ''}".strip(),
                       "title": e["title"], "ref": "", "forecast_text": f"预期 {cons}" if cons else "",
                       "previous_text": e.get("previous") or "", "importance": imp})
        up.sort(key=lambda x: (x["bj_date"], x["bj"]))
        return {"upcoming": up, "recent": [], "importance_rule": importance.RULE}

    def build(self) -> dict:
        dims_in = [("earnings", "盈利", self.earnings()), ("valuation", "估值", self.valuation()),
                   ("sentiment", "情绪", self.sentiment())]
        st = {k: s for k, _, (_, _, s) in dims_in}
        env = I.environment(st["earnings"], st["valuation"], st["sentiment"])
        out_dims, sections = [], []
        for key, name, (groups, metrics, state) in dims_in:
            out_dims.append({"key": key, "name": name, "label": state["label"], "why": state.get("why", []),
                             "head": state["head"],
                             "metrics": [x for x in (metric_json(m, state.get("anchors", {}), self.charts) for m in metrics) if x]})
            sections.append({"key": key, "name": name, "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})
        core = "、".join(CORE)
        return {
            "asof": self.asof.isoformat(),
            "method": "盈利看标普 500 周报，不看个股中位数。兑现、指引、修正分开写：披露过半后，EPS 超预期达到 80% 为兑现强、低于 70% 为偏弱；"
                      "季度净指引大于 0 为偏多。修正优先用主线里的上修/下修次数，净占比达到 ±10 个百分点才改成上修或下修；"
                      "没有次数时，看同一数据源的年度 EPS 增速较上一篇是否超过 ±0.5 个百分点。"
                      "断点 2026-08-15 FactSet→LSEG：两段不互相比较，surprise 等指标断点前后不可直接对比。"
                      "估值用周报里的标普远期市盈率（手工表 spx_fwd_pe 优先），换成盈利收益率后减去 10 年实际利率，"
                      "低于 2 个百分点为贵、高于 4 个百分点为便宜。"
                      "个股表（" + core + " 等）只作对照。"
                      "情绪四块分开写：VIX（低于 15 平静、高于 25 紧张）、标普站上 200 日均线的比例（低于 40% 为面窄）、"
                      "FINRA 融资余额同比（高于 +20% 为扩张）、标普 500 前十大权重（达到 30% 为集中）。"
                      "环境名只由盈利修正和估值决定，兑现、指引和情绪不参与起名。阈值都写在 pipeline/us/interpret.py。",
            "insight": getattr(self, "insight_view", {"break_date": insight.BREAK.isoformat(), "note": "", "weeks": []}),
            "verdict": {"name": env["name"], "headline": env["head"], "lines": env["lines"], "label": "整体判断"},
            "dimensions": out_dims,
            "names": getattr(self, "names", []),
            "names_date": getattr(self, "names_date", ""),
            "releases": self.releases(),
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(src: Sources, asof: date | None = None, now: datetime | None = None) -> dict:
    return UsBuilder(src, asof, now).build()
