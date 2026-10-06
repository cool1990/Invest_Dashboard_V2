"""比特币四步状态。只看已经取到的数，阈值都在这个文件里。

顺序：价格相对成本 → 筹码赚了多少 → 谁在买谁在卖 → 杠杆会不会放大波动。
不调用模型。缺数的那一票不参与，不拿别的数去补。
"""

from __future__ import annotations

# 价格：现价相对 STH 成本、LTH 真实成本、TMMP 三条偏离（百分数）的中位数。
PRICE_CHEAP = 0.0
PRICE_RICH = 50.0

# 盈利。MVRV 2.4 是这套看板里 ETF 时代的过热线，不是 2017 年的 4。
MVRV_LOSS = 1.0
MVRV_HOT = 2.4
PROFIT_SHARE_HOT = 90.0

# 行为。单位：交易所净流 BTC/日，ETF 百万美元，供给变化 BTC。
FLOW_ACC = -1000.0
FLOW_DIST = 1000.0
ETF_ACC = 100.0
ETF_DIST = -100.0
LTH_DIST = -10000.0
LTH_ACC = 10000.0
EX_ACC = -5000.0
EX_DIST = 5000.0
BEHAVIOR_EDGE = 0.5

# 杠杆。资金费率是 8 小时费率按一年 3×365 次换算后的百分数。约 11% 是 0.01%/8h 的中性水平。
OI_LOW = 3.0
OI_HIGH = 4.0
FUND_QUIET = 15.0
FUND_HOT = 40.0
FUND_NEUTRAL = 0.01 * 3 * 365 * 100  # 10.95


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    ys = sorted(vals)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2


def price_label(premia: list[float | None]) -> str:
    vals = [p for p in premia if p is not None]
    med = _median(vals)
    if med is None:
        return "数据不足"
    below_sth = premia[0] is not None and premia[0] < PRICE_CHEAP if premia else False
    # premia 顺序约定为 [STH, LTH真实, TMMP]，调用方按这个顺序传。
    if med < PRICE_CHEAP or below_sth:
        return "便宜"
    if med >= PRICE_RICH:
        return "贵"
    return "中性"


def profit_label(mvrv: float | None, nupl: float | None, share: float | None) -> str:
    if mvrv is None and nupl is None and share is None:
        return "数据不足"
    loss = (mvrv is not None and mvrv < MVRV_LOSS) or (mvrv is None and nupl is not None and nupl < 0)
    hot = (mvrv is not None and mvrv >= MVRV_HOT) or (share is not None and share >= PROFIT_SHARE_HOT)
    if loss:
        return "亏损"
    if hot:
        return "过热"
    return "中等"


def _vote_low(v: float | None, lo: float) -> int | None:
    if v is None or v > lo:
        return None
    return 1


def _vote_high(v: float | None, hi: float) -> int | None:
    if v is None or v < hi:
        return None
    return -1


def behavior_votes(flow: float | None, etf7: float | None, lth7: float | None, ex7: float | None) -> list[int]:
    """+1 吸筹，-1 派发。落在阈值中间的不投票。"""
    votes: list[int] = []
    if flow is not None:
        if flow <= FLOW_ACC:
            votes.append(1)
        elif flow >= FLOW_DIST:
            votes.append(-1)
    if etf7 is not None:
        if etf7 >= ETF_ACC:
            votes.append(1)
        elif etf7 <= ETF_DIST:
            votes.append(-1)
    if lth7 is not None:
        if lth7 <= LTH_DIST:
            votes.append(-1)
        elif lth7 >= LTH_ACC:
            votes.append(1)
    if ex7 is not None:
        if ex7 <= EX_ACC:
            votes.append(1)
        elif ex7 >= EX_DIST:
            votes.append(-1)
    return votes


def behavior_label(flow: float | None, etf7: float | None, lth7: float | None, ex7: float | None) -> str:
    votes = behavior_votes(flow, etf7, lth7, ex7)
    if not votes:
        return "数据不足"
    avg = sum(votes) / len(votes)
    if avg >= BEHAVIOR_EDGE:
        return "吸筹"
    if avg <= -BEHAVIOR_EDGE:
        return "派发"
    return "中性"


def leverage_label(oi_pct: float | None, fund_abs: float | None) -> str:
    """fund_abs 是 7 日资金费率年化的绝对值。OI 是全网未平仓占市值的百分数。"""
    if oi_pct is None and fund_abs is None:
        return "数据不足"
    if (oi_pct is not None and oi_pct >= OI_HIGH) or (fund_abs is not None and fund_abs >= FUND_HOT):
        return "高"
    oi_low = oi_pct is None or oi_pct < OI_LOW
    fund_low = fund_abs is None or fund_abs < FUND_QUIET
    if oi_low and fund_low:
        return "低"
    return "中"


def _num(v: float | None, digits: int = 0) -> str | None:
    if v is None:
        return None
    return f"{v:,.{digits}f}".replace(",", "")


def _span(vals: list[float]) -> str | None:
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if abs(hi - lo) < 0.05:
        return f"{lo:.0f}%"
    return f"{lo:.0f}%–{hi:.0f}%"


def risk_sentence(lev: str, profit: str) -> str:
    if lev == "高" or profit == "过热":
        return "短期涨跌都容易被放大。"
    if lev == "低" and profit != "过热":
        return "短期被杠杆放大的风险不大。"
    if lev == "数据不足":
        return "杠杆数据不够，短期风险先不下结论。"
    return "短期方向还不明显。"


def compose(f: dict) -> dict:
    """f 里的偏离已经是百分数（16 表示高 16%）。返回标签、总句、四行和综合句。"""
    prem = f.get("premia") or [None, None, None]
    while len(prem) < 3:
        prem.append(None)
    pl = price_label(prem[:3])
    ql = profit_label(f.get("mvrv"), f.get("nupl"), f.get("profit_share"))
    bl = behavior_label(f.get("ex_flow"), f.get("etf_7d"), f.get("lth_7d"), f.get("ex_supply_7d"))
    fund = f.get("funding_7d")
    ll = leverage_label(f.get("oi_pct"), None if fund is None else abs(fund))

    pos_vals = [p for p in prem[:3] if p is not None and p > 0]
    neg_vals = [p for p in prem[:3] if p is not None and p < 0]
    span = _span([abs(p) for p in prem[:3] if p is not None]) if pl == "中性" else None

    if pl == "便宜":
        position = "已经落到主要成本线下方" if neg_vals else "靠近主要成本线下方"
    elif pl == "贵":
        position = "离主要成本线已经比较远"
    elif pl == "中性" and pos_vals and not neg_vals and span:
        position = f"站在各主要成本线上方 {span}"
    elif pl == "中性" and span:
        position = f"离主要成本线不远（偏离 {span}）"
    else:
        position = "主要成本线还没取齐"

    if ql == "亏损":
        profit_phrase = "持币人整体还亏着"
    elif ql == "过热":
        profit_phrase = "持币人浮盈过热"
    elif ql == "中等":
        profit_phrase = "持币人整体赚得不算多"
    else:
        profit_phrase = "盈利数据还没取到"

    flow = f.get("ex_flow")
    if bl == "吸筹" and flow is not None and flow < 0:
        beh_phrase = "币在慢慢离开交易所"
    elif bl == "吸筹":
        beh_phrase = "买盘还在"
    elif bl == "派发":
        beh_phrase = "卖压更明显"
    elif bl == "中性":
        beh_phrase = "买卖没有明显一边倒"
    else:
        beh_phrase = "行为数据还没取到"

    if ll == "低":
        lev_phrase = "杠杆也偏低，眼下更像健康的横盘，不像要被杠杆放大的急涨急跌"
    elif ll == "中":
        lev_phrase = "杠杆中等，波动可能被放大一些"
    elif ll == "高":
        lev_phrase = "杠杆偏高，涨跌都容易被放大"
    else:
        lev_phrase = "杠杆数据还没取到"

    px = f.get("price")
    if px is None:
        headline = f"收盘价还没取到。{profit_phrase}，{beh_phrase}，{lev_phrase}。"
    else:
        headline = f"比特币收在 {_num(px)}，{position}，{profit_phrase}，{beh_phrase}，{lev_phrase}。"

    def line_price() -> str:
        if pl == "数据不足":
            return "主要成本线没有取齐，先不判断便宜还是贵。"
        if pl == "便宜":
            return "价格到了短线成本或主要成本中枢的下面。"
        if pl == "贵":
            return f"价格高于主要成本中枢 {PRICE_RICH:.0f}% 以上。"
        if span and pos_vals and not neg_vals:
            return f"价格站在短线成本和长线真实成本上方 {span}，没有远离。"
        return "价格离主要成本线不远。"

    def line_profit() -> str:
        m = f.get("mvrv")
        if ql == "数据不足":
            return "MVRV、NUPL、浮盈供给都没有取到。"
        if ql == "亏损":
            return "全网平均还在成本下方。"
        if ql == "过热":
            extra = f"MVRV {_num(m, 2)}。" if m is not None else ""
            return f"浮盈到了过热区。{extra}".strip()
        if m is None:
            return "筹码整体赚着钱，但离过热还远。"
        return f"筹码整体赚着钱，但离过热还远（MVRV {_num(m, 2)}）。"

    def line_behavior() -> str:
        if bl == "数据不足":
            return "交易所、ETF 和长线供给都没有够得上阈值的读数。"
        if bl == "吸筹":
            return "离所、ETF 或储量这些买盘信号占多数。"
        if bl == "派发":
            return "长线供给减少、币进交易所或 ETF 净卖，这些信号占多数。"
        return "吸筹和派发的票对不上，先看成中性。"

    def line_leverage() -> str:
        if ll == "数据不足":
            return "全网未平仓和资金费率都没有取到。"
        if ll == "低":
            return "杠杆偏低，暂时没有放大波动的燃料。"
        if ll == "高":
            return "未平仓或资金费率到了拥挤区，波动容易被放大。"
        return "杠杆中等，还没到拥挤。"

    lines = [
        {"k": "价格", "t": line_price()},
        {"k": "盈利", "t": line_profit()},
        {"k": "行为", "t": line_behavior()},
        {"k": "杠杆", "t": line_leverage()},
    ]

    bits = [f"价格{pl}"]
    if span and pl == "中性" and pos_vals and not neg_vals:
        bits[0] = f"价格{pl}（高于主要成本线 {span}）"
    m = f.get("mvrv")
    bits.append(f"盈利{ql}" + (f"（MVRV {_num(m, 2)}）" if m is not None else ""))
    bits.append(f"行为{bl}")
    bits.append(f"杠杆{ll}")
    extra = ""
    urpd = f.get("urpd") or {}
    if urpd.get("inside") and urpd.get("lo") is not None:
        extra = f"现价在筹码最厚的 {int(urpd['lo'])}–{int(urpd['hi'])}。"
    cluster = f.get("cluster")
    if cluster:
        extra += f"再往下，成本挤在 {cluster}。"
    synthesis = "；".join(bits) + "。" + risk_sentence(ll, ql) + extra

    return {
        "price": pl, "profit": ql, "behavior": bl, "leverage": ll,
        "headline": headline, "lines": lines, "synthesis": synthesis,
        "name": f"价格{pl} · 盈利{ql} · 行为{bl} · 杠杆{ll}",
    }


def rules() -> list[dict]:
    """页面上展开就能看到的阈值。文字和上面的判断用同一组常数。"""
    return [
        {"name": "价格 便宜 / 中性 / 贵", "text":
            "用现价相对三条主要成本的偏离：STH 成本、LTH 真实成本、TMMP。"
            f"取这三条偏离的中位数 p。p < {PRICE_CHEAP:.0f}% ，或者现价低于 STH 成本，为便宜；"
            f"p ≥ {PRICE_RICH:.0f}% 为贵；其余为中性。某一条没取到就不参加中位数；三条都没有则这一步为数据不足。"
            "偏离 = 现价 / 该线 − 1，现价用同一行收盘价（四舍五入到整数）再算。"},
        {"name": "盈利 亏损 / 中等 / 过热", "text":
            f"MVRV < {MVRV_LOSS:g}（没有 MVRV 时看 NUPL < 0）为亏损。"
            f"MVRV ≥ {MVRV_HOT:g}，或浮盈供给占比 ≥ {PROFIT_SHARE_HOT:.0f}%，为过热。"
            "两条都没触发、且至少有一个数，为中等。MVRV 与浮盈供给都没有、NUPL 也没有，为数据不足。"},
        {"name": "行为 吸筹 / 中性 / 派发", "text":
            "四张票，落在中间带的不投票，缺数的不投票。"
            f"交易所净流 MA7 ≤ {FLOW_ACC:.0f} BTC 吸筹，≥ {FLOW_DIST:.0f} BTC 派发；"
            f"ETF 近 7 个交易日净流入 ≥ {ETF_ACC:.0f} 百万美元吸筹，≤ {ETF_DIST:.0f} 派发；"
            f"LTH 供给较 7 天前 ≤ {LTH_DIST:.0f} BTC 派发，≥ {LTH_ACC:.0f} 吸筹；"
            f"交易所储量较 7 天前 ≤ {EX_ACC:.0f} BTC 吸筹，≥ {EX_DIST:.0f} 派发。"
            f"有效票的平均值 ≥ {BEHAVIOR_EDGE:g} 为吸筹，≤ −{BEHAVIOR_EDGE:g} 为派发，其余为中性。一张票都没有则为数据不足。"},
        {"name": "杠杆 低 / 中 / 高", "text":
            f"全网未平仓 / 市值 ≥ {OI_HIGH:g}% ，或资金费率 7 日年化的绝对值 ≥ {FUND_HOT:.0f}%，为高。"
            f"未平仓占比 < {OI_LOW:g}%（或没有这个数）且资金费率 7 日年化绝对值 < {FUND_QUIET:.0f}%"
            f"（或没有这个数），同时至少有其中一个数，为低。其余为中。"
            f"资金费率按 8 小时结算、一年 3×365 次换算；每 8 小时 0.01% 约合年化 {FUND_NEUTRAL:.0f}%，当作常见中性水平，不是阈值本身。"
            "全网未平仓若只有实时快照，用快照当天的数，日期写抓取时间，不当作收盘。"},
        {"name": "综合句", "text":
            "把四步标签串成一句。杠杆为高，或盈利为过热，写短期涨跌容易被放大；"
            "杠杆为低且盈利不是过热，写短期被杠杆放大的风险不大；杠杆数据不足则不下风险结论；其余写短期方向还不明显。"
            "现价落在最新一期 URPD 区间里时补一句筹码带；其下若有至少三条成本挤在现价的 80%–100% 且彼此相差不超过现价的 12%，再补成本扎堆区间。"},
    ]
