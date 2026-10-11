"""美股三个维度的状态，以及整体判断。

和宏观页一样按经济锚点判断，不和历史平均比。阈值都写在这里。
盈利的三块（兑现、指引、修正）和情绪的四块都分开写，不合成一个分数。
只有修正决定上修 / 下修这一档。断点 2026-08-15 FactSet→LSEG：两源不互相比较，surprise 等不可跨断点对比。
"""

from __future__ import annotations

from ..macro.interpret import _f, _head, _why

# 盈利周报。超预期比例、指引、修正分开，不平均。
ABOVE_STRONG, ABOVE_WEAK = 80.0, 70.0  # EPS Above %
REPORTED_READY = 50.0  # 披露比例达到这个才看超预期
GUIDE_POS = 50.0  # 没有净家数时，正面占比达到这个算偏多
GROWTH_STEP = 0.5  # 同一来源的年度 EPS 增速，较上一篇超过这个才算上修或下修
REV_SHARE = 0.10  # (上修次数 − 下修次数) / 合计，达到这个才改档

# 估值：盈利收益率（100 / 远期市盈率）减去 10 年实际利率，单位百分点
ERP_RICH, ERP_CHEAP = 2.0, 4.0  # 低于 2 贵，高于 4 便宜，中间大致合理

# 情绪
VIX_CALM, VIX_STRESS = 15.0, 25.0
CNN_FEAR, CNN_GREED = 25.0, 75.0
AAII_FEAR, AAII_GREED = -15.0, 20.0  # 牛熊差，百分点
BREADTH_NARROW = 40.0  # 站上 200 日均线的比例 %
MARGIN_UP, MARGIN_DOWN = 20.0, 0.0  # 融资余额同比 %
MARGIN_FASTER = 10.0  # 融资同比 − 标普同比，百分点
TOP10 = 30.0  # 前十大权重 %


def _state(label: str, level: int | None, why: list[dict], summary: str, anchors: dict) -> dict:
    return {"label": label, "level": level, "why": why, "head": _head(why), "summary": summary, "anchors": anchors}


def _pct(x: float | None, digits: int = 1) -> str:
    return _f(x, "{:+." + str(digits) + "f}%")


def _join_phrases(phrases: list[str]) -> str:
    if not phrases:
        return "盈利数据不足"
    if len(phrases) == 1:
        return phrases[0]
    tail = phrases[-1]
    if any(s in tail for s in ("下修", "刚切换", "还看不到")):
        return "，".join(phrases[:-1]) + "，但" + tail
    return "，".join(phrases)


def earnings_state(v: dict) -> dict:
    """标普 500 盈利周报：兑现、指引、修正分开。只有修正决定上修或下修。"""
    source = v.get("source") or "周报"
    quarter = v.get("quarter") or "跟踪季度"
    anchors = {
        "q_growth": "跟踪季度的 EPS 同比。换季会跳，不单独决定上修或下修",
        "y_growth": f"同一数据源内，较上一篇超过 ±{GROWTH_STEP:g} 个百分点才算上修或下修；不跨 FactSet 和 LSEG",
        "q_guide": "预告家数。新季度会重新累计，方向看净指引",
        "q_net": "正面家数减负面家数，大于 0 为指引偏多，小于 0 为偏空",
        "q_pos": f"没有净家数时，正面占比达到 {GUIDE_POS:g}% 为偏多",
        "reported": f"披露比例达到 {REPORTED_READY:g}% 才看超预期。早期笔记里的家数不是百分比",
        "eps_above": f"达到 {ABOVE_STRONG:g}% 为兑现强，低于 {ABOVE_WEAK:g}% 为偏弱",
        "eps_surprise": "只在同一数据源内看。断点 2026-08-15 FactSet→LSEG，surprise 不能跨断点连着比",
        "rev_net": f"上修次数减下修次数。净占比达到 ±{REV_SHARE * 100:g} 个百分点才改成上修或下修",
    }
    useful = ("reported_pct", "reported_n", "eps_above", "q_net", "q_pos", "y_growth", "q_growth", "rev_up")
    if not any(v.get(k) is not None for k in useful):
        return _state("数据不足", None, [], "盈利数据不足", anchors)

    parts: list[str] = []
    phrases: list[str] = []
    why: list[tuple[str, str] | None] = []

    reported_pct, reported_n = v.get("reported_pct"), v.get("reported_n")
    above, surprise = v.get("eps_above"), v.get("eps_surprise")
    ready = reported_pct is not None and reported_pct >= REPORTED_READY
    if reported_n is not None and reported_pct is None:
        parts.append("披露刚开始")
        phrases.append("财报季刚开始披露")
        why.append(("披露", f"已披露 {reported_n:.0f} 家，笔记里这列还是家数不是比例，不据此判断兑现"))
    elif reported_pct is not None and not ready:
        parts.append("披露未过半")
        phrases.append("这一季披露还没过半")
        why.append(("披露", f"实际披露 {_f(reported_pct, '{:.1f}')}%，不到 {REPORTED_READY:g}%，超预期比例先不作数"))
    elif ready and above is not None:
        if above >= ABOVE_STRONG:
            tag, phrase = "兑现强", "当季业绩兑现强"
        elif above < ABOVE_WEAK:
            tag, phrase = "兑现偏弱", "当季超预期的公司偏少"
        else:
            tag, phrase = "兑现平常", "当季超预期比例平常"
        parts.append(tag)
        phrases.append(phrase)
        text = (f"{quarter} 已披露 {_f(reported_pct, '{:.1f}')}%，EPS 超预期 {_f(above, '{:.1f}')}%"
                f"（达到 {ABOVE_STRONG:g}% 为兑现强，低于 {ABOVE_WEAK:g}% 为偏弱）")
        if surprise is not None:
            text += f"；surprise {_f(surprise, '{:+.1f}')}%（{source} 口径，不和另一种来源比）"
        why.append(("兑现", text))

    q_net, q_pos, y_pos = v.get("q_net"), v.get("q_pos"), v.get("y_pos")
    if q_net is not None:
        if q_net > 0:
            gtag, gphrase = "指引偏多", "下季指引偏多"
        elif q_net < 0:
            gtag, gphrase = "指引偏空", "下季指引偏空"
        else:
            gtag, gphrase = "指引持平", "下季指引正负相抵"
        parts.append(gtag)
        phrases.append(gphrase)
        text = f"季度净指引 {_f(q_net, '{:+.0f}')} 家（大于 0 为偏多，小于 0 为偏空）"
        if q_pos is not None:
            text += f"，正面占比 {_f(q_pos, '{:.1f}')}%"
        if y_pos is not None:
            text += f"；年度正面占比 {_f(y_pos, '{:.1f}')}%"
        why.append(("指引", text))
    elif q_pos is not None:
        if q_pos >= GUIDE_POS:
            gtag, gphrase = "指引偏多", "下季指引偏多"
        else:
            gtag, gphrase = "指引偏空", "下季指引偏空"
        parts.append(gtag)
        phrases.append(gphrase)
        why.append(("指引", f"季度正面指引 {_f(q_pos, '{:.1f}')}%（达到 {GUIDE_POS:g}% 为偏多）。这周没有净家数"))

    rev_up, rev_down = v.get("rev_up"), v.get("rev_down")
    y, y_prev = v.get("y_growth"), v.get("y_prev")
    level = 0
    if rev_up is not None and rev_down is not None and rev_up + rev_down > 0:
        share = (rev_up - rev_down) / (rev_up + rev_down)
        if share >= REV_SHARE:
            rtag, rphrase, level = "上修", "分析师在上修盈利", 1
        elif share <= -REV_SHARE:
            rtag, rphrase, level = "下修", "分析师在下修盈利", -1
        elif rev_down > rev_up:
            rtag, rphrase = "小幅净下修", "分析师修正已是小幅净下修"
        elif rev_up > rev_down:
            rtag, rphrase = "小幅净上修", "分析师修正是小幅净上修"
        else:
            rtag, rphrase = "修正持平", "盈利修正接近持平"
        parts.append(rtag)
        phrases.append(rphrase)
        text = (f"主线里的 FY1 修正 {rev_up:.0f} 次上修、{rev_down:.0f} 次下修，"
                f"净占比 {_f(share * 100, '{:+.1f}')} 个百分点"
                f"（达到 ±{REV_SHARE * 100:g} 才改成上修或下修）")
        if y is not None and y_prev is not None:
            text += f"。年度增速 {_f(y, '{:.1f}')}%，较上一篇 {_f(y - y_prev, '{:+.1f}')} 个百分点，和修正次数不是同一件事"
        why.append(("修正", text))
    elif v.get("source_break"):
        parts.append("修正不可比")
        phrases.append("数据源刚切换，修正还不能和上一份比")
        why.append(("修正", "这一期换成了另一种盈利统计，年度增速和 surprise 都不和上一份比"))
    elif y is not None and y_prev is not None:
        delta = y - y_prev
        if delta > GROWTH_STEP:
            rtag, rphrase, level = "上修", "分析师在上修全年盈利", 1
        elif delta < -GROWTH_STEP:
            rtag, rphrase, level = "下修", "分析师在下修全年盈利", -1
        else:
            rtag, rphrase = "修正持平", "全年盈利修正接近持平"
        parts.append(rtag)
        phrases.append(rphrase)
        why.append(("修正", f"同一来源（{source}）的年度 EPS 增速 {_f(y, '{:.1f}')}%，较上一篇 {_f(delta, '{:+.1f}')} 个百分点"
                    f"（超过 ±{GROWTH_STEP:g} 才算上修或下修）。不跨 FactSet 和 LSEG"))
    else:
        parts.append("修正不足")
        phrases.append("还看不到能定档的盈利修正")
        why.append(("修正", "没有上修/下修次数，也没有同一来源的上一篇年度增速"))

    if v.get("q_growth") is not None:
        why.append(("当季增速", f"{quarter} EPS 同比 {_f(v.get('q_growth'), '{:.1f}')}%。换季时会跳，不单独决定上修或下修"))
    if v.get("fwd_eps_chg") is not None:
        why.append(("远期EPS", f"周报里的 Forward EPS 变化 {_f(v.get('fwd_eps_chg'), '{:+.1f}')}%，只作参考"))

    return _state(" · ".join(parts), level, _why(*why), _join_phrases(phrases), anchors)


def valuation_state(v: dict) -> dict:
    """远期市盈率换成盈利收益率，再减去 10 年实际利率。优先用周报里的标普远期市盈率。"""
    pe, erp, real = v.get("pe"), v.get("erp"), v.get("real")
    source = v.get("source") or ("manual" if v.get("manual") else "basket")
    who = {
        "manual": "手工录入的标普远期市盈率",
        "insight": "标普 500 远期市盈率",
        "basket": "核心篮子远期市盈率中位数",
    }.get(source, "远期市盈率")
    tail = {
        "manual": "（核心篮子和周报只作参考）",
        "insight": "（盈利周报里的未来四季市盈率，不是个股中位数）",
        "basket": "（还没有标普指数的远期市盈率，暂用个股中位数）",
    }.get(source, "")
    anchors = {
        "pe": "不和历史平均市盈率比；判断看盈利收益率减去实际利率",
        "erp": f"低于 {ERP_RICH:g}% 为贵，{ERP_RICH:g}–{ERP_CHEAP:g}% 大致合理，高于 {ERP_CHEAP:g}% 为便宜",
        "real": "10 年期实际利率，从盈利收益率里减去",
    }
    if pe is None:
        return _state("数据不足", None, [], "估值数据不足", anchors)
    ey = 100.0 / pe
    pe_t = f"{who} {_f(pe, '{:.1f}')} 倍，盈利收益率 {_f(ey, '{:.2f}')}%{tail}"
    if erp is None or real is None:
        why = _why(("市盈率", pe_t), ("实际利率", "还没有 10 年期实际利率，算不出风险溢价"))
        return _state("数据不足", None, why, "还没法把远期市盈率换成相对实际利率的风险溢价", anchors)
    if erp < ERP_RICH:
        label, level, summary = "贵", -1, "远期盈利收益率盖不住实际利率，估值偏贵"
    elif erp > ERP_CHEAP:
        label, level, summary = "便宜", 1, "远期盈利收益率明显高于实际利率，估值不贵"
    else:
        label, level, summary = "大致合理", 0, "远期盈利收益率相对实际利率大致合理"
    why = _why(
        ("市盈率", pe_t),
        ("风险溢价", f"10 年实际利率 {_f(real, '{:.2f}')}%，盈利收益率减去它是 {_f(erp, '{:.2f}')}%"
         f"（低于 {ERP_RICH:g}% 为贵，高于 {ERP_CHEAP:g}% 为便宜）"),
    )
    return _state(label, level, why, summary, anchors)


def _risk_tag(vix: float | None, cnn: float | None, aaii: float | None) -> str | None:
    if vix is None and cnn is None and aaii is None:
        return None
    if vix is None:
        if (aaii is not None and aaii <= AAII_FEAR) or (cnn is not None and cnn < CNN_FEAR):
            return "调查偏悲观"
        if (aaii is not None and aaii >= AAII_GREED) or (cnn is not None and cnn > CNN_GREED):
            return "调查偏贪婪"
        return "中性"
    tag = "平静" if vix < VIX_CALM else "紧张" if vix > VIX_STRESS else "正常"
    fear = (aaii is not None and aaii <= AAII_FEAR) or (cnn is not None and cnn < CNN_FEAR)
    greed = (aaii is not None and aaii >= AAII_GREED) or (cnn is not None and cnn > CNN_GREED)
    if fear and tag == "平静":
        return "平静，但调查偏悲观"
    if fear and tag == "正常":
        return "正常，调查偏悲观"
    if greed and tag == "紧张":
        return "紧张，但调查偏贪婪"
    if greed and tag == "平静":
        return "平静偏贪婪"
    return tag


def _risk_summary(tag: str) -> str:
    if "悲观" in tag:
        return "价格波动不高，但调查偏悲观" if tag.startswith("平静") or tag.startswith("正常") else "风险偏好偏恐惧"
    if "贪婪" in tag:
        return "风险偏好偏贪婪"
    return {"平静": "风险偏好平静", "正常": "风险偏好正常", "紧张": "风险偏好紧张", "中性": "风险偏好中性",
            "调查偏悲观": "调查偏悲观", "调查偏贪婪": "调查偏贪婪"}.get(tag, f"风险偏好{tag}")


def sentiment_state(v: dict) -> dict:
    """四块并列。标签用「 · 」接起来，缺的那块不写。"""
    vix, cnn, aaii = v.get("vix"), v.get("cnn"), v.get("aaii")
    breadth, margin_yoy = v.get("breadth"), v.get("margin_yoy")
    gap, top10 = v.get("margin_gap"), v.get("top10")
    anchors = {
        "vix": f"低于 {VIX_CALM:g} 平静，高于 {VIX_STRESS:g} 紧张",
        "cnn": f"低于 {CNN_FEAR:g} 恐惧，高于 {CNN_GREED:g} 贪婪",
        "aaii": f"牛熊差 ≤ {AAII_FEAR:g} 悲观，≥ {AAII_GREED:g} 乐观",
        "breadth": f"低于 {BREADTH_NARROW:g}% 为面窄",
        "margin_yoy": f"同比高于 +{MARGIN_UP:g}% 为杠杆扩张，低于 {MARGIN_DOWN:g} 为去杠杆",
        "margin_gap": f"融资同比比标普同比高过 {MARGIN_FASTER:g} 个百分点，算快于指数",
        "top10": f"前十大权重合计达到 {TOP10:g}% 为集中",
    }
    parts, phrases = [], []
    why_items = []

    risk = _risk_tag(vix, cnn, aaii)
    if risk:
        parts.append(f"风险偏好{risk}")
        phrases.append(_risk_summary(risk))
        bits = []
        if vix is not None:
            bits.append(f"VIX {_f(vix, '{:.1f}')}，" + ("低于 15，平静" if vix < VIX_CALM else
                                                      "高于 25，紧张" if vix > VIX_STRESS else "在 15–25，正常"))
        if cnn is not None:
            bits.append(f"CNN 恐贪 {_f(cnn, '{:.0f}')}，" + ("低于 25，恐惧" if cnn < CNN_FEAR else
                                                         "高于 75，贪婪" if cnn > CNN_GREED else "在中间"))
        if aaii is not None:
            bits.append(f"AAII 牛熊差 {_f(aaii, '{:+.1f}')} 个百分点，" + ("≤ −15，悲观" if aaii <= AAII_FEAR else
                                                                        "≥ 20，乐观" if aaii >= AAII_GREED else "在中间"))
        why_items.append(("风险偏好", "；".join(bits)))

    if breadth is not None:
        narrow = breadth < BREADTH_NARROW
        parts.append("参与度面窄" if narrow else "参与度正常")
        phrases.append("上涨的参与度窄" if narrow else "上涨的参与度不窄")
        why_items.append(("参与度", f"标普成分股里 {_f(breadth, '{:.0f}')}% 站上 200 日均线"
                          + ("，低于 40%，面窄" if narrow else "，不低于 40%")))

    if margin_yoy is not None:
        if margin_yoy > MARGIN_UP:
            mtag, msum = "杠杆扩张", "融资杠杆在扩张"
        elif margin_yoy < MARGIN_DOWN:
            mtag, msum = "杠杆去化", "融资杠杆在去化"
        else:
            mtag, msum = "杠杆平稳", "融资杠杆变化不大"
        faster = gap is not None and gap > MARGIN_FASTER
        if faster and margin_yoy > MARGIN_DOWN:
            msum += "，而且快于指数"
        parts.append(mtag)
        phrases.append(msum)
        t = f"客户融资余额同比 {_pct(margin_yoy)}（高于 +{MARGIN_UP:g}% 为扩张，低于 0 为去杠杆）"
        if gap is not None:
            t += f"；比标普指数同比 {_f(gap, '{:+.0f}')} 个百分点" + (
                f"，高过 {MARGIN_FASTER:g} 个百分点，融资比指数涨得更快" if faster else "")
        why_items.append(("杠杆", t))

    if top10 is not None:
        hot = top10 >= TOP10
        parts.append("集中" if hot else "集中度未过线")
        phrases.append("指数集中在少数公司" if hot else "前十大权重还没到集中的门槛")
        why_items.append(("集中度", f"跟踪标普 500 的 ETF 前十大权重合计 {_f(top10, '{:.1f}')}%"
                          + (f"，达到 {TOP10:g}%，少数公司就能带动指数" if hot else f"，还低于 {TOP10:g}%")))

    if not parts:
        return _state("数据不足", None, [], "情绪数据不足", anchors)
    return _state(" · ".join(parts), None, _why(*why_items), "；".join(phrases), anchors)


REGIMES = {
    (1, 1): ("顺风", "盈利预期在上修，估值不贵"),
    (1, 0): ("盈利支撑", "盈利预期在上修，估值大致合理"),
    (1, -1): ("涨但偏贵", "盈利预期在上修，但估值已贵"),
    (0, 1): ("估值便宜", "盈利修正接近持平，估值不贵"),
    (0, 0): ("中性", "盈利修正接近持平，估值大致合理"),
    (0, -1): ("估值偏贵", "盈利修正接近持平，但估值已贵"),
    (-1, 1): ("下修但便宜", "盈利预期在下修，估值已经不贵"),
    (-1, 0): ("盈利转弱", "盈利预期在下修，估值大致合理"),
    (-1, -1): ("双杀风险", "盈利预期在下修，估值仍贵"),
}


def environment(earn: dict, val: dict, sent: dict) -> dict:
    """环境名由盈利 × 估值决定。情绪写在下面，不参与起名，避免和前两块互相抵消。"""
    lines = [{"k": k, "t": t} for k, t in (
        ("盈利", earn.get("summary")), ("估值", val.get("summary")), ("情绪", sent.get("summary")),
    ) if t and t not in ("盈利数据不足", "估值数据不足", "情绪数据不足")]
    el, vl = earn.get("level"), val.get("level")
    if el is None or vl is None:
        return {"name": "数据不足", "head": "数据不足", "lines": lines}
    name, meaning = REGIMES[(el, vl)]
    return {"name": name, "head": f"{name}：{meaning}", "lines": lines}
