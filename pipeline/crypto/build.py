"""把各来源收成页面用的 dashboard.json，以及按图拆开的序列文件。

大段历史不进 dashboard。每个指标用自己的日期，当天未收盘的点在更早的加载步骤里已经丢掉。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from .. import series as ts
from ..series import Series
from . import interpret as I
from .binance import funding_annualized

SERIES_DIR = "data/crypto/series"


def iround(v: float) -> int:
    return int(Decimal(format(v, "f")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def closed(s: Series, today: date) -> Series:
    return [(d, v) for d, v in s if d < today]


def exact(s: Series, day: date) -> float | None:
    return ts.asof(s, day, max_gap_days=0)


def last(s: Series) -> tuple[date, float] | None:
    return s[-1] if s else None


def delta(s: Series, days: int = 7) -> float | None:
    """和恰好 days 天前的同一个日子比。那天没有点就返回 None，不拿邻近的日子凑。"""
    if not s:
        return None
    d, v = s[-1]
    base = exact(s, d - timedelta(days=days))
    if base is None:
        return None
    return v - base


def prev_level(s: Series, days: int = 7) -> float | None:
    if not s:
        return None
    return exact(s, s[-1][0] - timedelta(days=days))


def splice_price(coinbase: Series, bitview: Series) -> tuple[Series, str]:
    """重叠的日子只用 Coinbase。Coinbase 上市前用 bitview，中间缺的日子不补。"""
    if not coinbase:
        return bitview, "bitview"
    start = coinbase[0][0]
    early = [(d, v) for d, v in bitview if d < start]
    return early + list(coinbase), "coinbase"


def net_flow(inn: Series, out: Series) -> Series:
    other = dict(out)
    return ts.clean((d, v - other[d]) for d, v in inn if d in other)


def ma_calendar(s: Series, n: int = 7) -> Series:
    """连续 n 个日历日都在，才写这一天的均值。缺一天就不写。"""
    by = dict(s)
    out = []
    for d, _ in s:
        vals = []
        ok = True
        for k in range(n):
            v = by.get(d - timedelta(days=k))
            if v is None:
                ok = False
                break
            vals.append(v)
        if ok:
            out.append((d, sum(vals) / n))
    return out


def rolling_obs(s: Series, n: int) -> Series:
    out = []
    for i in range(n - 1, len(s)):
        window = s[i - n + 1:i + 1]
        out.append((window[-1][0], sum(v for _, v in window)))
    return out


def quarter_end(token: str) -> date:
    year = int(token[:4])
    q = int(token[-1])
    month = q * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def hold_to(s: Series, end: date | None) -> Series:
    """阶梯线收到右端：右端的值和最后一个观测相同，不是新的成交。"""
    if not s or end is None or s[-1][0] >= end:
        return s
    return [*s, (end, s[-1][1])]


def pack(s: Series, nd: int = 4) -> list[list]:
    out = []
    for d, v in s:
        if abs(v) >= 1000:
            out.append([d.isoformat(), round(v, 2)])
        elif abs(v) >= 1:
            out.append([d.isoformat(), round(v, nd)])
        else:
            out.append([d.isoformat(), round(v, 6)])
    return out


def ln(name: str, s: Series, kind: str = "line", dash: bool = False, step: bool = False,
       ref: bool = False, nd: int = 4) -> dict | None:
    if not s:
        return None
    return {"name": name, "type": kind, "dash": dash, "step": step, "ref": ref, "data": pack(s, nd)}


def ref_band(name: str, s: Series, y: float) -> dict | None:
    if len(s) < 2:
        return None
    return {"name": name, "type": "line", "dash": True, "step": False, "ref": True,
            "data": [[s[0][0].isoformat(), y], [s[-1][0].isoformat(), y]]}


def chart(cid: str, title: str, unit: str, series: list, note: str = "", zero: bool = False) -> dict | None:
    series = [s for s in series if s and s.get("data")]
    if not series:
        return None
    last_dates = [s["data"][-1][0] for s in series if not s.get("ref") and s.get("data")]
    return {"id": cid, "title": title, "unit": unit, "note": note, "zero": zero, "series": series,
            "last_date": max(last_dates) if last_dates else series[-1]["data"][-1][0]}


def _sign(v: float, text: str) -> str:
    if v > 0:
        return "+" + text
    if v < 0:
        return "−" + text
    return text


def wan_btc(v: float, digits: int = 2, signed: bool = False) -> str:
    n = abs(v) / 10000
    body = f"{n:.{digits}f} 万"
    return _sign(v, body) if signed else ("−" + body if v < 0 else body)


def usd_from_mn(v: float) -> str:
    """百万美元换成亿或万。"""
    if abs(v) >= 100:
        body = f"{abs(v) / 100:.2f} 亿"
    else:
        body = f"{abs(v) * 100:.0f} 万"
    return _sign(v, body) + "美元"


def pct(v: float, digits: int = 1) -> str:
    return _sign(v, f"{abs(v):.{digits}f}%")


def kpi(name: str, text: str, unit: str = "", when: str = "", chg: str | None = None,
        chg_label: str = "较 7 天前", hint: str = "") -> dict:
    return {"name": name, "text": text, "unit": unit, "date": when, "chg": chg,
            "chg_label": chg_label, "hint": hint}


def _iso(d: date | None) -> str:
    return d.isoformat() if d else ""


def dist_text(price_i: int, line_i: int) -> tuple[float, str]:
    p = (price_i / line_i - 1) * 100
    if p >= 0:
        return p, f"现价高于{p:.1f}%"
    return p, f"现价低于{abs(p):.1f}%"


def urpd_status(price_i: int, lo: int, hi: int) -> str:
    if lo <= price_i < hi:
        return "现价位于区间内"
    if price_i >= hi:
        p = (price_i / hi - 1) * 100
        return f"现价高于上沿{p:.1f}%"
    p = (1 - price_i / lo) * 100
    return f"现价低于下沿{p:.1f}%"


def cluster_text(price_i: int, lines: list[tuple[str, int]]) -> str | None:
    below = [v for _, v in lines if price_i * 0.8 <= v < price_i]
    if len(below) < 3:
        return None
    lo, hi = min(below), max(below)
    if hi - lo > price_i * 0.12:
        return None
    return f"{lo / 10000:.1f}–{hi / 10000:.1f} 万"


def miner_steps(doc: dict, end: date | None) -> dict[str, dict]:
    out = {}
    for key, item in (doc or {}).items():
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        if item.get("enabled") is False:
            continue
        pts: Series = []
        prev = item.get("prev") or {}
        if prev.get("value") is not None and prev.get("data_quarter"):
            pts.append((quarter_end(str(prev["data_quarter"])), float(prev["value"])))
        if item.get("data_quarter"):
            pts.append((quarter_end(str(item["data_quarter"])), float(item["value"])))
        pts = ts.clean(pts)
        out[key] = {
            "label": item.get("label") or key,
            "value": float(item["value"]),
            "quarter": item.get("data_quarter") or "",
            "note": item.get("note") or "",
            "series": hold_to(pts, end),
        }
    return out


def mstr_series(records: list[dict], field: str, end: date | None) -> Series:
    pts = []
    for row in records:
        raw = row.get(field)
        if raw in (None, ""):
            continue
        try:
            pts.append((date.fromisoformat(str(row["asof"])[:10]), float(raw)))
        except (KeyError, ValueError):
            continue
    return hold_to(ts.clean(pts), end)


def issuer_sums(rows: list[dict], issuers: list[str], n: int) -> dict[str, Series]:
    """每个发行商近 n 个交易日合计。没有这一列的日子当 0 会把缺数当成 0，所以缺了就跳过该发行商这一天。"""
    out: dict[str, Series] = {}
    for name in issuers:
        pts = []
        for row in rows:
            raw = row.get(name)
            if raw in (None, ""):
                continue
            try:
                pts.append((date.fromisoformat(row["date"][:10]), float(raw)))
            except (KeyError, ValueError):
                continue
        pts = ts.clean(pts)
        if len(pts) >= n:
            out[name] = rolling_obs(pts, n)
    return out


def build_dashboard(src: dict, today: date | None = None, now: datetime | None = None) -> tuple[dict, dict]:
    today = today or datetime.now(timezone.utc).date()
    now = now or datetime.now(timezone.utc)
    bv = {k: closed(v, today) for k, v in (src.get("bitview") or {}).items()}
    cm = {k: closed(v, today) for k, v in (src.get("cm") or {}).items()}
    price, price_src = splice_price(closed(src.get("coinbase") or [], today), bv.get("price_close") or [])
    lth_true = closed(src.get("lth_true") or [], today)
    etf_rows = [r for r in (src.get("etf") or []) if r.get("date", "")[:10] < today.isoformat()]
    fund_rows = []
    for row in src.get("funding") or []:
        try:
            if row["time"][:10] < today.isoformat():
                fund_rows.append(row)
        except (KeyError, TypeError):
            continue
    metrics = src.get("metrics") or {}
    oi = closed(metrics.get("oi_usd") or [], today)
    ls_acc = closed(metrics.get("ls_ratio") or [], today)
    ls_top = closed(metrics.get("ls_top_position") or [], today)
    basis = closed(src.get("basis") or [], today)
    miner = miner_steps(src.get("miner") or {}, price[-1][0] if price else None)
    mstr = src.get("mstr") or []
    urpd = src.get("urpd") or None
    oi_net = src.get("oi_network") or []

    px = last(price)
    price_i = iround(px[1]) if px else None
    price_day = px[0] if px else None

    def line_last(s: Series) -> tuple[date, int] | None:
        got = last(s)
        if not got:
            return None
        return got[0], iround(got[1])

    sth = line_last(bv.get("sth_realized_price") or [])
    lth = line_last(bv.get("lth_realized_price") or [])
    tmmp = line_last(bv.get("true_market_mean") or [])
    rp = line_last(bv.get("realized_price") or [])
    u10 = line_last(bv.get("utxos_under_10y_old_realized_price") or [])
    true_lth = line_last(lth_true)
    mstr_cost = line_last(mstr_series(mstr, "avg_cost", None))
    mstr_hold = line_last(mstr_series(mstr, "holdings", None))

    def prem(item: tuple[date, int] | None) -> float | None:
        if price_i is None or item is None or item[1] <= 0:
            return None
        return (price_i / item[1] - 1) * 100

    premia = [prem(sth), prem(true_lth), prem(tmmp)]

    mvrv_s = bv.get("mvrv") or []
    sth_m = bv.get("sth_mvrv") or []
    lth_m = bv.get("lth_mvrv") or []
    nupl_s = bv.get("nupl") or []
    share_s = bv.get("supply_in_profit_share") or []
    lth_sup = bv.get("lth_supply") or []
    sth_sup = bv.get("sth_supply") or []
    lth_sopr = bv.get("lth_sopr_24h") or []
    sth_sopr = bv.get("sth_sopr_24h") or []
    cdd = bv.get("coindays_destroyed_sum_24h") or []
    dorm = bv.get("dormancy_24h") or []
    hash7 = [(d, v / 1e18) for d, v in (bv.get("hash_rate_sma_1w") or [])]
    puell = bv.get("puell_multiple") or []

    flows = net_flow(cm.get("FlowInExNtv") or [], cm.get("FlowOutExNtv") or [])
    flow_ma = ma_calendar(flows, 7)
    ex_sup = cm.get("SplyExNtv") or []

    etf_s = ts.clean(
        (date.fromisoformat(r["date"][:10]), float(r["total_usd_mn"]))
        for r in etf_rows if r.get("total_usd_mn") not in (None, "")
    )
    etf7 = rolling_obs(etf_s, 7)
    etf30 = rolling_obs(etf_s, 30)
    fund_d, fund_w = funding_annualized(fund_rows)

    oi_pct = None
    oi_when = ""
    oi_b = None
    if oi_net:
        last_oi = oi_net[-1]
        try:
            oi_b = float(last_oi["oi_usd_b"])
            oi_pct = float(last_oi["oi_mcap_pct"])
            oi_when = last_oi.get("fetched_at") or last_oi.get("date") or ""
        except (KeyError, TypeError, ValueError):
            oi_b = None

    fund_last = last(fund_w)
    facts = {
        "price": price_i,
        "premia": premia,
        "mvrv": None if not mvrv_s else mvrv_s[-1][1],
        "nupl": None if not nupl_s else nupl_s[-1][1],
        "profit_share": None if not share_s else share_s[-1][1],
        "ex_flow": None if not flow_ma else flow_ma[-1][1],
        "etf_7d": None if not etf7 else etf7[-1][1],
        "lth_7d": delta(lth_sup, 7),
        "ex_supply_7d": delta(ex_sup, 7),
        "oi_pct": oi_pct,
        "funding_7d": None if fund_last is None else fund_last[1],
        "urpd": None,
        "cluster": None,
    }
    if urpd and price_i is not None and urpd.get("lo") is not None:
        lo, hi = int(urpd["lo"]), int(urpd["hi"])
        u_date = str(urpd.get("date") or "")[:10]
        if not price_day or not u_date or u_date <= price_day.isoformat():
            facts["urpd"] = {"lo": lo, "hi": hi, "inside": lo <= price_i < hi}

    cost_for_cluster = []
    for item in (sth, true_lth, tmmp, mstr_cost):
        if item:
            cost_for_cluster.append(("", item[1]))
    cash = miner.get("miner_cash_cost")
    if cash:
        cost_for_cluster.append(("", iround(cash["value"])))
    if price_i is not None:
        facts["cluster"] = cluster_text(price_i, cost_for_cluster)

    judged = I.compose(facts)

    # ---- 成本表，按价格从高到低。没取到的放最后。 ----
    table_rows = []

    def add_line(name: str, item: tuple[date, int] | None, note: str, quarter: str = "") -> None:
        if item is None or price_i is None:
            table_rows.append({"name": name, "value": None, "text": "未取到", "gap": "", "date": "", "note": note})
            return
        p, gap = dist_text(price_i, item[1])
        table_rows.append({
            "name": name, "value": item[1], "text": str(item[1]), "gap": gap, "gap_value": round(p, 2),
            "date": quarter or item[0].isoformat(), "note": note, "sort": item[1],
        })

    add_line("STH 成本", sth, "近约 5 个月内买入的币的平均成本")
    add_line("LTH 真实成本", true_lth, "长线持有者里还在流动的那部分币的成本")
    add_line("TMMP", tmmp, "还在流动的币的平均成本，长期不动的老币少算")
    add_line("10 年内币成本", u10, "去掉 10 年以上没动过的老币之后，其余币的平均成本")
    add_line("RP", rp, "全网所有币最后一次移动时的平均成本")
    add_line("LTH 成本", lth, "持有约 5 个月以上的币的平均成本")
    add_line("MSTR 均价", mstr_cost, "Strategy 在 8-K 里披露的买入均价")
    for key in ("miner_all_in_est", "miner_cash_cost", "miner_self_reported_energy"):
        item = miner.get(key)
        if not item:
            add_line(key, None, "")
            continue
        add_line(item["label"], (quarter_end(item["quarter"]) if item["quarter"] else price_day, iround(item["value"]))
                 if item["quarter"] or price_day else None, item["note"], quarter=item["quarter"])
    if urpd and urpd.get("lo") is not None and price_i is not None:
        lo, hi = int(urpd["lo"]), int(urpd["hi"])
        supply = urpd.get("supply")
        supply_txt = f"带内 {supply / 10000:.1f} 万 BTC" if isinstance(supply, (int, float)) else ""
        table_rows.append({
            "name": "URPD 最密带", "value": (lo + hi) / 2, "text": f"{lo}–{hi}",
            "gap": urpd_status(price_i, lo, hi), "date": str(urpd.get("date") or "")[:10],
            "note": "筹码成本最厚的 2000 美元一档。" + supply_txt, "sort": (lo + hi) / 2,
        })
    else:
        table_rows.append({"name": "URPD 最密带", "value": None, "text": "未取到", "gap": "", "date": "",
                           "note": "筹码成本最厚的一档", "sort": None})

    known = [r for r in table_rows if r.get("sort") is not None]
    missing = [r for r in table_rows if r.get("sort") is None]
    known.sort(key=lambda r: r["sort"], reverse=True)
    table_rows = known + missing
    for r in table_rows:
        r.pop("sort", None)

    # ---- 图 ----
    files: dict[str, dict] = {}

    def add_chart(spec: dict | None) -> str | None:
        if not spec:
            return None
        files[spec["id"]] = spec
        return spec["id"]

    end = price_day
    urpd_lines = []
    if facts.get("urpd") and end:
        lo, hi = facts["urpd"]["lo"], facts["urpd"]["hi"]
        start = max(price[0][0], end - timedelta(days=180)) if price else end
        urpd_lines = [
            ln(f"URPD {lo}", [(start, lo), (end, lo)], dash=True, nd=0),
            ln(f"URPD {hi}", [(start, hi), (end, hi)], dash=True, nd=0),
        ]
    price_id = add_chart(chart(
        "price", "价格与成本", "美元",
        [
            ln("价格", price, nd=2),
            ln("矿企全成本", (miner.get("miner_all_in_est") or {}).get("series") or [], step=True, nd=0),
            *urpd_lines,
            ln("LTH 真实成本", lth_true, nd=2),
            ln("TMMP", bv.get("true_market_mean") or [], nd=2),
            ln("矿企现金成本", (miner.get("miner_cash_cost") or {}).get("series") or [], step=True, nd=0),
            ln("MSTR 均价", mstr_series(mstr, "avg_cost", end), step=True, nd=2),
            ln("STH 成本", bv.get("sth_realized_price") or [], nd=2),
            ln("10 年内币成本", bv.get("utxos_under_10y_old_realized_price") or [], nd=2),
            ln("RP", bv.get("realized_price") or [], nd=2),
            ln("LTH 成本", bv.get("lth_realized_price") or [], nd=2),
            ln("矿企电费成本", (miner.get("miner_self_reported_energy") or {}).get("series") or [], step=True, nd=0),
        ],
        note=("价格是 Coinbase BTC-USD 日线收盘；Coinbase 没有的早期用 bitview。"
              if price_src == "coinbase" else "价格是 bitview 日线，Coinbase 这次没取到。")
             + " 矿企成本和 MSTR 均价是阶梯，延伸到最新收盘日只是把上一档保持住。URPD 只画最新一期。",
    ))

    mvrv_id = add_chart(chart(
        "mvrv", "MVRV", "倍",
        [ln("MVRV", mvrv_s), ln("STH-MVRV", sth_m), ln("LTH-MVRV", lth_m),
         ref_band("1", mvrv_s, 1), ref_band("2.4 过热", mvrv_s, I.MVRV_HOT)],
        note="1 是成本附近。2.4 以上这套页面算过热。",
    ))
    nupl_id = add_chart(chart(
        "nupl", "NUPL", "",
        [ln("NUPL", nupl_s), ref_band("0", nupl_s, 0), ref_band("0.5", nupl_s, 0.5), ref_band("0.75", nupl_s, 0.75)],
        note="低于 0 全网平均还亏着。0.75 以上是常见的极热区，页面过热不单看它。",
    ))
    share_id = add_chart(chart(
        "profit_share", "浮盈供给占比", "%",
        [ln("浮盈供给", share_s, nd=2), ref_band("90% 极热", share_s, 90)],
        note="现价之上还有多少比例的币在赚钱。达到 90% 这一步算过热。",
        zero=True,
    ))

    beh_supply = add_chart(chart(
        "supply", "LTH / STH 供给", "BTC",
        [ln("LTH 供给", lth_sup, nd=2), ln("STH 供给", sth_sup, nd=2)],
        note="bitview 按约 150 天划分。增加是更多币留在这一边。",
    ))
    beh_sopr = add_chart(chart(
        "sopr", "SOPR", "倍",
        [ln("LTH-SOPR", lth_sopr), ln("STH-SOPR", sth_sopr), ref_band("1 盈亏线", lth_sopr or sth_sopr, 1)],
        note="高于 1 是在赚钱卖，低于 1 是在亏钱卖。",
    ))
    beh_cdd = add_chart(chart("cdd", "CDD", "币天", [ln("CDD", cdd, nd=0)], note="当天老币动了多少。"))
    beh_dorm = add_chart(chart("dormancy", "Dormancy", "天", [ln("Dormancy", dorm)], note="当天被花掉的币平均睡了多久。"))

    etf_daily = add_chart(chart(
        "etf_daily", "现货 ETF 日净流入", "百万美元",
        [ln("合计", etf_s, kind="bar", nd=2)],
        note="Farside，单位百万美元。只有美股交易日。",
        zero=True,
    ))
    etf_cum = add_chart(chart(
        "etf_cum", "ETF 累计净流入", "百万美元",
        [ln("7 个交易日", etf7, nd=2), ln("30 个交易日", etf30, nd=2)],
        note="按交易日滚动相加，不是日历日。",
    ))
    issuers = []
    for row in etf_rows:
        for key in row:
            if key not in {"date", "total_usd_mn"} and key not in issuers:
                issuers.append(key)
    # 近 30 个交易日绝对额最大的 4 家，其余合成「其他」
    rank = []
    for name in issuers:
        pts = []
        for row in etf_rows:
            raw = row.get(name)
            if raw in (None, ""):
                continue
            try:
                pts.append(abs(float(raw)))
            except ValueError:
                continue
        if pts:
            rank.append((sum(pts[-30:]), name))
    rank.sort(reverse=True)
    top = [name for _, name in rank[:4]]
    issuer_series = []
    sums = issuer_sums(etf_rows, top, 7)
    for name in top:
        if name in sums:
            issuer_series.append(ln(name, sums[name], nd=2))
    if len(rank) > 4:
        other_rows = []
        for row in etf_rows:
            try:
                day = date.fromisoformat(row["date"][:10])
            except ValueError:
                continue
            acc = 0.0
            seen = False
            for _, name in rank[4:]:
                raw = row.get(name)
                if raw in (None, ""):
                    continue
                try:
                    acc += float(raw)
                    seen = True
                except ValueError:
                    continue
            if seen:
                other_rows.append((day, acc))
        other = rolling_obs(ts.clean(other_rows), 7)
        issuer_series.append(ln("其他", other, nd=2))
    etf_iss = add_chart(chart(
        "etf_issuer", "主要发行商 7 个交易日净流入", "百万美元", issuer_series,
        note="每条是该发行商自己近 7 个交易日的合计。",
    ))

    hash_id = add_chart(chart(
        "hashrate", "算力 7 日均值", "EH/s",
        [ln("7 日均值", hash7, nd=2)],
        note="bitview 的 7 日均值，单位从 H/s 换成 EH/s。",
    ))
    puell_id = add_chart(chart(
        "puell", "Puell", "倍",
        [ln("Puell", puell), ref_band("1 一年均值附近", puell, 1)],
        note="矿工收入相对过去一年的位置。接近 1 是收入正常。",
    ))
    flow_id = add_chart(chart(
        "ex_flow", "交易所净流", "BTC",
        [ln("当日", flows, kind="bar", nd=2), ln("MA7", flow_ma, nd=2)],
        note="CoinMetrics：流入减流出。MA7 要连续 7 个日历日都有数才画。负数是币在离开交易所。",
        zero=True,
    ))
    ex_id = add_chart(chart(
        "ex_supply", "交易所储量", "BTC",
        [ln("储量", ex_sup, nd=2)],
        note="CoinMetrics 交易所地址里的币。",
    ))
    mstr_id = add_chart(chart(
        "mstr", "MSTR 持仓", "BTC",
        [ln("持仓", mstr_series(mstr, "holdings", end), step=True, nd=2)],
        note="8-K 披露的持仓，阶梯。右端延伸不是新的增持。",
    ))

    oi_pts = ts.clean(
        (date.fromisoformat(r["date"][:10]), float(r["oi_usd_b"]))
        for r in oi_net if r.get("date") and r.get("oi_usd_b") not in (None, "")
    )
    oi_pct_pts = ts.clean(
        (date.fromisoformat(r["date"][:10]), float(r["oi_mcap_pct"]))
        for r in oi_net if r.get("date") and r.get("oi_mcap_pct") not in (None, "")
    )
    oi_id = add_chart(chart(
        "oi_net", "全网未平仓", "十亿美元",
        [ln("未平仓", oi_pts, nd=2)],
        note=("Coinfuty 实时页，每个 UTC 日存一笔。这是快照，不是收盘。"
              + (f"自 {oi_pts[0][0].isoformat()} 起积累，不回补。" if oi_pts else "还没有快照。")),
    ))
    oi_ratio_id = add_chart(chart(
        "oi_ratio", "全网未平仓 / 市值", "%",
        [ln("占比", oi_pct_pts, nd=2), ref_band("4% 拥挤", oi_pct_pts, I.OI_HIGH)],
        note="和左图同一次快照。4% 以上这一步算杠杆高。",
    ))
    binance_oi = add_chart(chart(
        "oi_binance", "币安永续未平仓", "美元",
        [ln("名义金额", oi, nd=0)],
        note="币安 BTCUSDT 永续，日包最后一笔。这是币安一家，不是全网。",
    ))
    fund_id = add_chart(chart(
        "funding", "资金费率年化", "%",
        [ln("当日结算平均", fund_d, nd=2), ln("7 日平均", fund_w, nd=2),
         ref_band("约 11% 中性", fund_w or fund_d, I.FUND_NEUTRAL)],
        note="币安 BTCUSDT 永续。8 小时费率按一年 3×365 次换算。当天没结算完的日子不放进来。",
    ))
    basis_id = add_chart(chart(
        "basis", "季度合约基差年化", "%",
        [ln("年化", basis, nd=2)],
        note="币安当季合约收盘相对币安现货收盘，按剩余天数年化。每天只用剩余天数最短的那张。",
    ))
    ls_id = add_chart(chart(
        "ls", "多空比", "倍",
        [ln("账户", ls_acc), ln("大户持仓", ls_top), ref_band("1", ls_acc or ls_top, 1)],
        note="币安 BTCUSDT。日包当天最后一笔的时间写在数字旁边，不是 00:00 的另一份快照。",
    ))

    # ---- 关键数字 ----
    def when_of(s: Series) -> str:
        return _iso(s[-1][0]) if s else ""

    def lvl(s: Series, digits: int) -> tuple[str, str, str | None]:
        if not s:
            return "未取到", "", None
        prev = prev_level(s)
        text = f"{s[-1][1]:.{digits}f}"
        chg = None if prev is None else f"{prev:.{digits}f}"
        return text, when_of(s), chg

    price_kpis = []
    if px:
        chg7 = None
        base = exact(price, px[0] - timedelta(days=7))
        if base:
            chg7 = pct((px[1] / base - 1) * 100)
        price_kpis.append(kpi("价格", str(price_i), "美元", _iso(px[0]), chg7, hint="Coinbase 收盘" if price_src == "coinbase" else "bitview"))
    else:
        price_kpis.append(kpi("价格", "未取到"))
    for name, item in (("距 STH", sth), ("距 LTH 真实成本", true_lth), ("距 TMMP", tmmp)):
        if item and price_i:
            _, gap = dist_text(price_i, item[1])
            price_kpis.append(kpi(name, gap, when=item[0].isoformat(), hint=str(item[1])))
        else:
            price_kpis.append(kpi(name, "未取到"))
    if facts.get("urpd"):
        u = facts["urpd"]
        price_kpis.append(kpi("URPD", f"{u['lo']}–{u['hi']}", when=str((urpd or {}).get("date") or "")[:10],
                              hint=urpd_status(price_i, u["lo"], u["hi"]) if price_i else ""))
    else:
        price_kpis.append(kpi("URPD", "未取到"))

    def ratio_kpi(name, s, digits):
        text, when, chg = lvl(s, digits)
        return kpi(name, text, "倍" if name != "NUPL" else "", when, chg)

    profit_kpis = [
        ratio_kpi("MVRV", mvrv_s, 2),
        ratio_kpi("STH-MVRV", sth_m, 2),
        ratio_kpi("LTH-MVRV", lth_m, 2),
        ratio_kpi("NUPL", nupl_s, 3),
    ]
    if share_s:
        prev = prev_level(share_s)
        chg = None if prev is None else pct(share_s[-1][1] - prev, 1).replace("%", " 个百分点")
        # pct() adds %; difference of ratios already in percentage points
        if prev is not None:
            diff = share_s[-1][1] - prev
            chg = _sign(diff, f"{abs(diff):.1f} 个百分点")
        profit_kpis.append(kpi("浮盈供给", f"{share_s[-1][1]:.1f}", "%", when_of(share_s), chg))
    else:
        profit_kpis.append(kpi("浮盈供给", "未取到", "%"))

    def supply_kpi(name, s):
        if not s:
            return kpi(name, "未取到")
        d7 = delta(s)
        chg = None if d7 is None else wan_btc(d7, signed=True)
        return kpi(name, wan_btc(s[-1][1], 2), "BTC", when_of(s), chg)

    flow_kpi = kpi("交易所净流 MA7", "未取到")
    if flow_ma:
        flow_kpi = kpi("交易所净流 MA7", f"{flow_ma[-1][1]:,.0f}".replace(",", ""), "BTC/日", when_of(flow_ma),
                       hint="负数是币在离所")
    etf_kpi_day = kpi("ETF 当日", "未取到")
    etf_kpi_7 = kpi("ETF 7 个交易日", "未取到")
    etf_kpi_30 = kpi("ETF 30 个交易日", "未取到")
    if etf_s:
        etf_kpi_day = kpi("ETF 当日", usd_from_mn(etf_s[-1][1]), when=when_of(etf_s), chg_label="")
    if etf7:
        etf_kpi_7 = kpi("ETF 7 个交易日", usd_from_mn(etf7[-1][1]), when=when_of(etf7), chg_label="")
    if etf30:
        etf_kpi_30 = kpi("ETF 30 个交易日", usd_from_mn(etf30[-1][1]), when=when_of(etf30), chg_label="")

    # 发行商当日，只列有数的
    issuer_kpis = []
    if etf_rows:
        last_row = etf_rows[-1]
        for name in top:
            raw = last_row.get(name)
            if raw in (None, ""):
                continue
            try:
                issuer_kpis.append(kpi(name, usd_from_mn(float(raw)), when=last_row["date"][:10], chg_label=""))
            except ValueError:
                continue

    hash_kpi = kpi("算力 7 日", "未取到")
    if hash7:
        base = exact(hash7, hash7[-1][0] - timedelta(days=7))
        chg = None if not base else pct((hash7[-1][1] / base - 1) * 100)
        hash_kpi = kpi("算力 7 日", f"{hash7[-1][1]:.0f}", "EH/s", when_of(hash7), chg)
    puell_text, puell_when, puell_prev = lvl(puell, 2)
    puell_kpi = kpi("Puell", puell_text, "倍", puell_when, puell_prev)

    ex_kpi = kpi("交易所储量", "未取到")
    if ex_sup:
        d7 = delta(ex_sup)
        ex_kpi = kpi("交易所储量", wan_btc(ex_sup[-1][1], 2), "BTC", when_of(ex_sup),
                    None if d7 is None else wan_btc(d7, signed=True))
    mstr_kpi = kpi("MSTR 持仓", "未取到")
    mstr_buy = kpi("MSTR 增持", "未取到", hint="最近一份写了买入数量的 8-K")
    if mstr:
        latest = max(mstr, key=lambda r: r.get("asof") or "")
        if latest.get("holdings") is not None:
            mstr_kpi = kpi("MSTR 持仓", wan_btc(float(latest["holdings"]), 2), "BTC", str(latest.get("asof") or ""),
                           hint=f"8-K {latest.get('filed') or ''}".strip())
        bought = [r for r in mstr if r.get("acquired") not in (None, "", 0)]
        if bought:
            b = bought[-1]
            span = ""
            if b.get("acquired_from") and b.get("acquired_to"):
                span = f"{b['acquired_from'][5:]} 至 {b['acquired_to'][5:]}"
            mstr_buy = kpi("MSTR 增持", f"{float(b['acquired']):.0f}", "BTC", span or str(b.get("asof") or ""), chg_label="")
    mnav_kpi = kpi("mNAV", "未取到", hint="仓库里没有这条序列，也没有不需登录的免费日频")

    sopr_l = lvl(lth_sopr, 3)
    sopr_s = lvl(sth_sopr, 3)
    cdd_kpi = kpi("CDD", "未取到")
    if cdd:
        prev = prev_level(cdd)
        cdd_kpi = kpi("CDD", wan_btc(cdd[-1][1], 0).replace(" 万", ""), "万币天", when_of(cdd),
                      None if prev is None else wan_btc(prev, 0).replace(" 万", "") + " 万币天")
    dorm_text, dorm_when, dorm_prev = lvl(dorm, 1)
    dorm_kpi = kpi("Dormancy", dorm_text, "天", dorm_when, None if dorm_prev is None else f"{dorm_prev} 天")

    oi_kpi = kpi("全网 OI", "未取到", hint="Coinfuty 实时页这次没解析到")
    oi_ratio_kpi = kpi("OI / 市值", "未取到")
    if oi_b is not None:
        oi_kpi = kpi("全网 OI", f"{oi_b:.1f}", "十亿美元", oi_when.replace("T", " ").replace("Z", " UTC"),
                     chg_label="", hint="实时快照，不是收盘")
    if oi_pct is not None:
        oi_ratio_kpi = kpi("OI / 市值", f"{oi_pct:.1f}", "%", oi_when.replace("T", " ").replace("Z", " UTC"), chg_label="")
    b_oi_kpi = kpi("币安永续 OI", "未取到")
    if oi:
        chg = None
        if len(oi) >= 2 and oi[-2][1]:
            chg = pct((oi[-1][1] / oi[-2][1] - 1) * 100)
        stamp = (metrics.get("time") or {}).get(oi[-1][0].isoformat(), oi[-1][0].isoformat())
        b_oi_kpi = kpi("币安永续 OI", f"{oi[-1][1] / 1e9:.2f}", "十亿美元", stamp, chg, "较前一日",
                       hint="日包最后一笔，不是全网")
    fund_day_kpi = kpi("资金费率 当日", "未取到")
    fund_week_kpi = kpi("资金费率 7 日", "未取到")
    if fund_d:
        fund_day_kpi = kpi("资金费率 当日", pct(fund_d[-1][1]), "年化", when_of(fund_d), chg_label="")
    if fund_w:
        fund_week_kpi = kpi("资金费率 7 日", pct(fund_w[-1][1]), "年化", when_of(fund_w), chg_label="")
    basis_kpi = kpi("季度基差", "未取到")
    if basis:
        basis_kpi = kpi("季度基差", pct(basis[-1][1]), "年化", when_of(basis), chg_label="")
    def ls_kpi(name, s):
        if not s:
            return kpi(name, "未取到")
        prev = prev_level(s)
        stamp = (metrics.get("time") or {}).get(s[-1][0].isoformat(), when_of(s))
        return kpi(name, f"{s[-1][1]:.2f}", "倍", stamp, None if prev is None else f"{prev:.2f}")

    behavior_head = next(x["t"] for x in judged["lines"] if x["k"] == "行为")
    sections = [
        {"id": "price", "name": "价格 vs 成本", "head": next(x["t"] for x in judged["lines"] if x["k"] == "价格"),
         "kpis": price_kpis, "charts": [c for c in [price_id] if c], "table": table_rows},
        {"id": "profit", "name": "盈利", "head": next(x["t"] for x in judged["lines"] if x["k"] == "盈利"),
         "kpis": profit_kpis, "charts": [c for c in [mvrv_id, nupl_id, share_id] if c]},
        {"id": "behavior", "name": "行为", "head": behavior_head, "groups": [
            {"name": "长短线", "kpis": [
                supply_kpi("LTH 供给", lth_sup), supply_kpi("STH 供给", sth_sup),
                kpi("LTH-SOPR", sopr_l[0], "倍", sopr_l[1], sopr_l[2]),
                kpi("STH-SOPR", sopr_s[0], "倍", sopr_s[1], sopr_s[2]),
                cdd_kpi, dorm_kpi,
            ], "charts": [c for c in [beh_supply, beh_sopr, beh_cdd, beh_dorm] if c]},
            {"name": "ETF", "kpis": [etf_kpi_day, etf_kpi_7, etf_kpi_30, *issuer_kpis],
             "charts": [c for c in [etf_daily, etf_cum, etf_iss] if c]},
            {"name": "矿工", "kpis": [hash_kpi, puell_kpi],
             "charts": [c for c in [hash_id, puell_id] if c]},
            {"name": "交易所与企业", "kpis": [flow_kpi, ex_kpi, mstr_kpi, mstr_buy, mnav_kpi],
             "charts": [c for c in [flow_id, ex_id, mstr_id] if c]},
        ]},
        {"id": "leverage", "name": "杠杆", "head": next(x["t"] for x in judged["lines"] if x["k"] == "杠杆"),
         "kpis": [oi_kpi, oi_ratio_kpi, b_oi_kpi, fund_day_kpi, fund_week_kpi, basis_kpi,
                  ls_kpi("账户多空比", ls_acc), ls_kpi("大户持仓多空比", ls_top)],
         "charts": [c for c in [oi_id, oi_ratio_id, binance_oi, fund_id, basis_id, ls_id] if c]},
    ]

    def start_of(s: Series) -> str | None:
        return s[0][0].isoformat() if s else None

    oi_start = oi_pts[0][0].isoformat() if oi_pts else None
    catalog = [
        {"name": "BTC 价格", "source": "Coinbase Exchange BTC-USD 日线收盘；更早用 bitview price_close",
         "start": start_of(price), "latest": _iso(price_day), "freq": "日", "ok": bool(price)},
        {"name": "STH / LTH 成本、TMMP、RP、10 年内币成本", "source": "bitview.space 日线",
         "start": start_of(bv.get("realized_price") or []), "latest": when_of(bv.get("realized_price") or []),
         "freq": "日", "ok": bool(bv.get("realized_price"))},
        {"name": "LTH 真实成本", "source": "CheckOnChain LTH True Realised Price（丢掉生成日当天的盘中点）",
         "start": start_of(lth_true), "latest": when_of(lth_true), "freq": "日", "ok": bool(lth_true)},
        {"name": "URPD", "source": "bitview /api/urpd/all", "start": (urpd or {}).get("date"),
         "latest": (urpd or {}).get("date"), "freq": "日，页面只留最新一期", "ok": bool(urpd)},
        {"name": "矿企三条成本", "source": "常量 data/crypto/miner_costs.json（CoinShares Q2 2026 与公司自报）",
         "start": "2025Q4" if miner.get("miner_cash_cost") else None,
         "latest": (miner.get("miner_cash_cost") or {}).get("quarter"), "freq": "季度，有新报告才改",
         "ok": bool(miner)},
        {"name": "MSTR 均价与持仓", "source": "SEC EDGAR 8-K（CIK 0001050446）",
         "start": mstr[0]["asof"] if mstr else None, "latest": mstr[-1]["asof"] if mstr else None,
         "freq": "事件，通常一周数次", "ok": bool(mstr)},
        {"name": "mNAV", "source": "无", "start": None, "latest": None, "freq": "—", "ok": False},
        {"name": "MVRV、STH/LTH-MVRV、NUPL、浮盈供给、SOPR、CDD、Dormancy、供给、算力、Puell",
         "source": "bitview.space 日线", "start": start_of(mvrv_s), "latest": when_of(mvrv_s),
         "freq": "日", "ok": bool(mvrv_s)},
        {"name": "现货 ETF", "source": "Farside bitcoin-etf-flow-all-data",
         "start": start_of(etf_s), "latest": when_of(etf_s), "freq": "美股交易日", "ok": bool(etf_s)},
        {"name": "交易所净流与储量", "source": "Coin Metrics Community（FlowInExNtv、FlowOutExNtv、SplyExNtv）",
         "start": start_of(ex_sup), "latest": when_of(ex_sup), "freq": "日", "ok": bool(ex_sup)},
        {"name": "资金费率", "source": "币安 BTCUSDT 永续，data.binance.vision 月度资金费率（fapi 不可用时）",
         "start": start_of(fund_d), "latest": when_of(fund_d), "freq": "每 8 小时，页面做成日与 7 日",
         "ok": bool(fund_d)},
        {"name": "季度基差", "source": "币安 USD-M 季度合约与 BTCUSDT 现货日线（data.binance.vision）",
         "start": start_of(basis), "latest": when_of(basis), "freq": "日", "ok": bool(basis)},
        {"name": "币安未平仓与多空比", "source": "data.binance.vision BTCUSDT 永续 metrics 日包最后一笔",
         "start": start_of(oi), "latest": when_of(oi), "freq": "日，日包能回溯到上线日，之后每日追加",
         "ok": bool(oi)},
        {"name": "全网 OI、OI/市值", "source": "Coinfuty 公开页实时值",
         "start": oi_start, "latest": oi_when, "freq": "每个 UTC 日快照一次，不回补",
         "ok": oi_b is not None},
    ]
    missing = [c["name"] for c in catalog if not c["ok"]]

    labels = [
        ("price", "价格", judged["price"]),
        ("profit", "盈利", judged["profit"]),
        ("behavior", "行为", judged["behavior"]),
        ("leverage", "杠杆", judged["leverage"]),
    ]
    dimensions = []
    for key, name, label in labels:
        dimensions.append({
            "key": key, "name": name, "label": label,
            "head": next(x["t"] for x in judged["lines"] if x["k"] == name),
            "why": [],
        })

    dash = {
        "asof": _iso(price_day) or today.isoformat(),
        "method": "四步都按 pipeline/crypto/interpret.py 里的阈值算，不调用模型。"
                  "价格看现价相对 STH、LTH 真实成本、TMMP 的偏离中位数；"
                  "盈利看 MVRV 和浮盈供给；行为看交易所净流、ETF 近 7 个交易日、LTH 供给和交易所储量；"
                  "杠杆看全网未平仓占市值和资金费率 7 日年化。缺的数不投票，也不补。",
        "verdict": {
            "label": "比特币",
            "name": judged["name"],
            "headline": judged["headline"],
            "lines": judged["lines"],
            "synthesis": judged["synthesis"],
        },
        "dimensions": dimensions,
        "rules": I.rules(),
        "sections": sections,
        "charts": [{"id": spec["id"], "title": spec["title"], "file": f"{SERIES_DIR}/{spec['id']}.json"}
                   for spec in files.values()],
        "catalog": catalog,
        "missing": missing,
        "price_source": price_src,
    }
    return dash, files
