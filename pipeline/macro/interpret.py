"""规则：维度状态与整体环境。

每个维度按经济锚点判断，而不是和历史平均比。阈值都是常用的经验值，写在各函数里，方便以后改。
只用已经公布的数据定状态；模型预测（GDPNow、克利夫兰联储 Nowcast）只作参考，
在句子里按它和官方数据差多少来措辞，不改写状态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Ctx:
    v: float
    extra: dict = field(default_factory=dict)


Result = tuple[str, str]


def ann(mom_pct: float) -> float:
    return ((1 + mom_pct / 100) ** 12 - 1) * 100


def vs_target(x: float, target: float = 2.0, band: float = 0.25) -> str:
    if x > target + band:
        return f"高于 {target:g}% 目标"
    if x < target - band:
        return f"低于 {target:g}% 目标"
    return f"接近 {target:g}% 目标"


# ---- 环比通胀：情景试算里给「核心 PCE 环比」「核心 CPI 环比」定级 ----
def core_mom(c: Ctx, hi: float = 3.0, mid: float = 2.5, target: float = 2.0) -> Result:
    """单月噪音大：级别按 3 个月均值（extra avg3）的折年率定，单月只作补充。"""
    avg3 = c.extra.get("avg3")
    if avg3 is None:
        a = ann(c.v)
        return f"单月折年约 {a:.1f}%，{vs_target(a, target)}", "alert" if a >= hi else "watch" if a >= mid else "ok"
    a3, a1 = ann(avg3), ann(c.v)
    t = f"3 个月均值 {avg3:.2f}% 折年约 {a3:.1f}%，{vs_target(a3, target)}；单月 {c.v:.2f}% 折年约 {a1:.1f}%"
    return t, "alert" if a3 >= hi else "watch" if a3 >= mid else "ok"


def core_cpi_mom(c: Ctx) -> Result:
    # CPI 长期比 PCE 高约 0.3–0.5 个百分点，阈值相应放宽
    return core_mom(c, hi=3.5, mid=2.8, target=2.0)




# ---------------------------------------------------------------------------
# 维度状态
#
# 每个函数拿到一组数（缺的为 None），返回：
#   label    状态标签，放在卡片右上角
#   why      理由，每条 {"k": 小结论, "t": 用哪几个数、对照什么锚点得出的}；标签不在理由里重复
#   head     理由连成一句，写进判断变化日志
#   summary  给顶部总结用的一句白话，不带数字（数字都在理由和依据里）
#   anchors  {指标 id: 对照的判断逻辑}，第二层「对照」列
#   level    给整体环境用的档位（增长 / 通胀：+1 / 0 / −1）
#   split    是否存在明显分歧
#
# 第二层每个数另有两列，在 build 里按指标 id 补上：
#   move     较上期变动（明显转好 / 略转好 / …）
#   status   当前水平（乐观 / 紧张 / 正常 / 偏强 / 接近目标 等）

def _f(x: float | None, fmt: str = "{:.1f}") -> str:
    return "—" if x is None else fmt.format(x)


def _hi_lo(x: float, digits: int = 2, unit: str = " 个百分点") -> str:
    return f"{'高' if x >= 0 else '低'} {abs(x):.{digits}f}{unit}"


def _why(*items: tuple[str, str] | None) -> list[dict]:
    return [{"k": k, "t": t} for k, t in (x for x in items if x) if t]


def _head(why: list[dict]) -> str:
    return "；".join(f"{w['k']}：{w['t']}" for w in why) or "数据不足"


def _wan(k: float) -> str:
    """千人 → 万人。"""
    return f"{k / 10:.1f} 万"


def _output_band(x: float | None) -> tuple[int | None, str]:
    """季环比年化增速对约 2% 的潜在增速分档。按公布精度（一位小数）比：页面显示 1.5 就按 1.5 算。"""
    if x is None:
        return None, "数据不足"
    x = round(x, 1)
    if x >= 2.5:
        return 1, "偏强"
    if x >= 1.5:
        return 0, "接近潜在"
    if x >= 0.5:
        return -1, "低于潜在"
    return -1, "停滞或收缩"


# 各档对 2% 潜在增速的说法
VS_POTENTIAL = {"偏强": "高于", "接近潜在": "接近", "低于潜在": "低于", "停滞或收缩": "远低于"}

# GDP 里和内需冷热关系不大、季度波动又大的几块：GDP 与核心 GDP 分档不同时，用它们解释差在哪
NOISE = (("nx", "净出口"), ("inv", "库存"), ("gov", "政府"))


def gap_driver(gdp: float, core: float, contrib: dict) -> str:
    """GDP 总量比核心 GDP 高（低）时，在净出口、库存、政府里找往同一方向贡献最大的那项。"""
    up = gdp > core
    parts = [(name, contrib[k]) for k, name in NOISE if contrib.get(k) is not None and (contrib[k] > 0) == up]
    if not parts:
        return ""
    name, c = max(parts, key=lambda p: abs(p[1]))
    return f"{name}{'拉高' if c > 0 else '拖累'} {abs(c):.1f} 个百分点"


CORE_PARTS = (("pce", "消费"), ("fixed", "固定投资"))


def contrib_text(contrib: dict) -> str:
    """「消费贡献 +2.3、固定投资 +1.2，净出口 −1.1、库存 −0.7、政府 −0.2」：
    先写核心两项，再写其余几项，同号的按大小排，拉高的在前、拖累的在后。"""
    sign = lambda c: f"{c:+.1f}".replace("-", "−")  # noqa: E731
    core = [(n, contrib[k]) for k, n in CORE_PARTS if contrib.get(k) is not None]
    rest = sorted(((n, contrib[k]) for k, n in NOISE if contrib.get(k) is not None),
                  key=lambda p: (p[1] < 0, -abs(p[1])))
    groups = ["、".join(f"{n}{'贡献' if i == 0 and g is core else ''} {sign(c)}" for i, (n, c) in enumerate(g))
              for g in (core, rest) if g]
    return "，".join(groups)


def growth_state(v: dict) -> dict:
    now, last, core = v.get("gdpnow"), v.get("gdp_q"), v.get("core_gdp")
    contrib = v.get("contrib") or {}
    nfp3 = v.get("nfp3")
    ur, u12, sahm = v.get("unrate"), v.get("unrate_chg12"), v.get("sahm")
    # 产出只按已公布的数据判断，优先看核心 GDP（对私人国内购买者的最终销售 = 消费 + 固定投资）：
    # GDP 总量里的净出口、库存、政府季度波动大，和内需冷热关系不大，抢进口一个季度就能把总量压低或抬高。
    # 核心 GDP 与 GDP 分档不同时，以核心 GDP 定档，理由里写明总量被哪一块拉低或拉高。
    # 两个都没有时才用 GDPNow（模型预测）顶上。
    by_model = last is None and core is None and now is not None
    o, o_txt = _output_band(core if core is not None else last if last is not None else now)
    driver, differs = "", core is not None and last is not None and _output_band(last)[1] != o_txt
    if o is None:
        o_why = ""
    elif core is not None:
        # 核心 GDP 是消费 + 固定投资自己的增速，不是总量加回净出口；总量和各项贡献并列写出，不挑一项当原因
        o_why = f"核心 GDP {round(core, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"
        if last is not None:
            o_why += f"；总量 {round(last, 1):.1f}%" + (f"，{mix}" if (mix := contrib_text(contrib)) else "")
        if differs:
            driver = gap_driver(last, core, contrib)
    elif by_model:
        o_why = f"还没有上季 GDP，暂按 GDPNow 模型预测 {round(now, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"
    else:
        o_why = f"上季实际 GDP {round(last, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"

    if nfp3 is None:
        l, l_txt = None, "数据不足"
    else:
        l_txt = _labor_band(nfp3, u12, sahm)
        l = {"强": 1, "降温": 0, "疲弱": -1, "恶化": -1}[l_txt]
    u_txt = ""
    if ur is not None and u12 is not None:
        u_txt = f"失业率 {ur:.1f}%，" + ("与一年前持平" if abs(u12) < 0.05 else f"比一年前{_hi_lo(u12, 1)}")
    elif u12 is not None:
        u_txt = "失业率与一年前持平" if abs(u12) < 0.05 else f"失业率比一年前{_hi_lo(u12, 1)}"
    if sahm is not None:
        u_txt += ("，" if u_txt else "") + (f"Sahm 规则 {sahm:.2f} 已触发（≥ 0.5）" if sahm >= 0.5 else "Sahm 规则未触发")
    if nfp3 is None:
        l_why = ""
    else:
        pace = {"强": "高于 15 万", "降温": "只够维持失业率不变（约 5–10 万）",
                "疲弱": "低于维持失业率不变所需的约 5 万"}.get(l_txt, "为负" if nfp3 < 0 else "")
        l_why = f"非农近 3 个月月均新增 {_wan(nfp3)}人" + (f"，{pace}" if pace else "") + (f"；{u_txt}" if u_txt else "")

    if o is None or l is None:
        level = o if l is None else l
        split = False
    else:
        level, split = min(o, l) if o != l else o, o != l
    if split:
        label = "分化"
    else:
        label = {1: "扩张偏强", 0: "接近潜在", -1: "放缓"}.get(level, "数据不足")
        if o_txt == "停滞或收缩" or l_txt == "恶化":
            label = "收缩风险"
    why = _why((f"产出{o_txt}", o_why), (f"就业{l_txt}", l_why))
    summary = "" if o is None and l is None else \
        f"产出{o_txt}，但就业{l_txt}" if split else f"产出{o_txt}、就业{l_txt}"
    bands = "潜在增速约 2%：≥ 2.5 偏强，1.5–2.5 接近潜在，< 1.5 低于潜在"
    if core is None:
        gdp_anchor = bands
    elif differs:
        gdp_anchor = "与核心 GDP 分档不同，以核心为准" + (f"：{driver}" if driver else "")
    else:
        gdp_anchor = "与核心 GDP 分档相同"
    anchors = {
        "core_gdp": bands,
        "gdp_q": gdp_anchor,
        "real_pce": "月度消费，两次 GDP 之间的更新；不进判断",
        "core_capex": "设备投资的先行指标；不进判断",
        "nfp3": "维持失业率不变约需 50–100 千人；≥ 150 且失业率没升为强",
        "unrate": f"较一年前 {_f(u12, '{:+.1f}')} 个百分点，Sahm {_f(sahm, '{:.2f}')}（0.5 触发）",
    }
    if now is not None:
        t = "模型预测，不进判断" if not by_model else "模型预测；还没有上季实际值，暂用它判断产出"
        if last is not None:
            t += f"；比上季实际{_hi_lo(now - last, 1)}"
            if abs(now - last) > 2:
                t += "，要等 GDP 初值确认"
        anchors["gdpnow"] = t
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": level, "split": split, "output": o_txt, "labor": l_txt}


def _mom_anchor(mom: float | None, kind: str) -> str | None:
    """环比的对照：把页面上看到的单月环比折年，再对 2%（超级核心沿用同比那档）。"""
    if mom is None:
        return None
    a = ann(mom)
    if kind == "super":
        return f"单月折年 {a:.2f}%：≤ 2.75% 与目标相容，≥ 3.5% 粘性强"
    return f"单月折年 {a:.2f}%：比 2% 目标{_hi_lo(a - 2)}"


def _mom_level(mom: float, supercore: bool) -> dict:
    a = ann(round(mom, 2))
    if supercore:
        if a <= 2.75:
            return _st("cooled", "已降温")
        if a < 3.5:
            return _st("elevated", "偏高")
        return _st("sticky", "粘性强")
    return _st(*_inflation_band(a))


def _nowcast_word(gap: float) -> str:
    """Nowcast 与最新官方同比的差，按大小措辞：±0.1 以内持平，0.1–0.3 略高/略低，再大才说明显。"""
    if gap > 0.3:
        return "明显偏高"
    if gap > 0.1:
        return "略高"
    if gap < -0.3:
        return "明显偏低"
    if gap < -0.1:
        return "略低"
    return "持平"


def inflation_state(v: dict) -> dict:
    # 按页面显示的精度（两位小数）比较，理由里的差值和两个数对得上
    r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
    yoy, m3, sc, fwd = (r2(v.get(k)) for k in ("core_yoy", "core_3m", "supercore", "fwd"))
    nc = [(p, r2(x)) for p, x in v.get("nowcast") or []]  # 比最新官方数据更新的期间
    period = v.get("core_yoy_period")  # 最新官方同比的期间文字，例如「7 月」
    if yoy is None:
        return {"label": "数据不足", "why": [], "head": "数据不足", "summary": "", "anchors": {},
                "level": None, "split": False}
    gap = yoy - 2
    lvl_txt = "明显高于目标" if gap >= 0.75 else "略高于目标" if gap >= 0.25 else \
        "接近目标" if gap > -0.25 else "低于目标"
    level = 1 if gap >= 0.5 else -1 if gap <= -0.25 else 0
    mom = None if m3 is None else m3 - yoy
    mom_k = None if mom is None else "短期在加速" if mom > 0.3 else "短期在放缓" if mom < -0.3 else "短期动能持平"
    label = {1: "偏热", 0: "接近目标", -1: "偏冷"}[level]
    lvl_why = f"核心 PCE 同比 {yoy:.2f}%{f'（{period}）' if period else ''}，比 2% 目标{_hi_lo(gap)}"
    mom_why = None if mom is None else \
        f"3 个月年化 {m3:.2f}%，比同比{_hi_lo(mom)}，{'在 ±0.3 以内' if abs(mom) <= 0.3 else '超出 ±0.3'}"
    sc_why = None if sc is None else (
        "服务通胀已降温" if sc <= 2.75 else "服务通胀偏高" if sc < 3.5 else "服务通胀粘",
        f"超级核心 PCE（服务除能源、住房，跟工资走）{sc:.2f}%，≤ 2.75% 才与 2% 目标相容")
    fwd_why = None if fwd is None else (
        "长期预期锚定" if 2 <= fwd <= 2.5 else "长期预期上移" if fwd > 2.5 else "长期预期偏低",
        f"5y5y 远期通胀预期 {fwd:.2f}%，2–2.5% 为锚定")
    anchors = {
        "core_yoy": f"目标 2%：{_hi_lo(gap)}",
        "supercore": "≤ 2.75% 与目标相容，≥ 3.5% 粘性强",
        "fwd": "2–2.5% 为锚定",
    }
    if mom is not None:
        anchors["core_3m"] = f"对同比 {yoy:.2f}%：{_hi_lo(mom)}，±0.3 以内为动能持平"
    # 依据表上的四条环比不改右上角标签，只给「状态 / 对照」用。按显示的两位小数折年。
    for key, kind in (("core_cpi_mom", "core"), ("core_pce_mom", "core"),
                      ("supercore_cpi_mom", "super"), ("supercore_pce_mom", "super")):
        text = _mom_anchor(r2(v.get(key)), kind)
        if text:
            anchors[key] = text
    # Nowcast 是模型预测，不改标签；只有和官方差超过 0.3 个百分点才写进理由
    nc_gap = nc_word = nc_why = None
    if nc:
        nc_gap = nc[-1][1] - yoy
        nc_word = _nowcast_word(nc_gap)
        anchors["nowcast"] = (f"最新官方 {yoy:.2f}%{f'（{period}）' if period else ''}：{_hi_lo(nc_gap)}，"
                              f"算{nc_word}（±0.1 以内算持平，超过 0.3 算明显）")
        if nc_word in ("明显偏高", "明显偏低"):
            nc_why = (f"模型预计{'回升' if nc_gap > 0 else '回落'}",
                      f"克利夫兰联储 Nowcast 预计 {nc[-1][0]} {nc[-1][1]:.2f}%，比最新官方{_hi_lo(nc_gap)}；"
                      "模型预测，不改标签")
    why = _why((lvl_txt, lvl_why), (mom_k, mom_why) if mom_k else None, sc_why, fwd_why, nc_why)
    flat = {1: "，短期没有回落", -1: "，短期没有回升"}.get(level, "，短期平稳")
    summary = f"核心通胀{lvl_txt}" + {"短期在加速": "，且短期在加速", "短期在放缓": "，但短期在放缓",
                                     "短期动能持平": flat}.get(mom_k or "", "")
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": level, "split": False, "mom": mom, "nowcast_gap": nc_gap, "nowcast_word": nc_word}


_CREDIT_BANDS = {"hy": (3.0, 4.5, 6.0, 8.0), "ig": (0.9, 1.3, 1.8, 2.5)}
_CREDIT_NAMES = ("乐观", "正常", "紧张", "压力", "危机")
_CREDIT_KEYS = ("optimistic", "normal", "tight", "stress", "crisis")
_CREDIT_RULE = {
    "hy": "< 3% 乐观，3–4.5% 正常，4.5–6% 紧张，6–8% 压力，≥ 8% 危机",
    "ig": "< 0.9% 乐观，0.9–1.3% 正常，1.3–1.8% 紧张，1.8–2.5% 压力，≥ 2.5% 危机",
}
# (明显的下限，大幅必须严格大于这个数)。单位是百分点，25bp = 0.25。
_SPREAD = {"hy": (0.25, 0.50), "ig": (0.05, 0.10)}


def _st(key: str, label: str) -> dict:
    return {"key": key, "label": label}


def credit_level(kind: str, value: float) -> str:
    """利差水平：低于第一档为乐观，越高越紧，过最后一档为危机。"""
    i = 0
    for cut in _CREDIT_BANDS[kind]:
        if value < cut:
            break
        i += 1
    return _CREDIT_NAMES[i]


def credit_status(kind: str, value: float) -> dict:
    i = 0
    for cut in _CREDIT_BANDS[kind]:
        if value < cut:
            break
        i += 1
    return _st(_CREDIT_KEYS[i], _CREDIT_NAMES[i])


def credit_anchor(kind: str, value: float | None = None) -> str:
    """对照只写分档规则；当前水平放在「状态」列。"""
    return _CREDIT_RULE[kind]


def _spread_move(metric_id: str, delta: float) -> dict:
    """较上期：上升为走阔，下降为收窄。垃圾债 25/50bp，投资级 5/10bp。"""
    mild, strong = _SPREAD[metric_id]
    mag = abs(delta)
    if mag < mild or delta == 0:
        return _st("calm", "平稳")
    wide = delta > 0
    if mag > strong:
        return _st("wide2" if wide else "tight2", "大幅走阔" if wide else "大幅收窄")
    return _st("wide1" if wide else "tight1", "明显走阔" if wide else "明显收窄")


def liquidity_state(v: dict) -> dict:
    sofr, ratio, nfci_v, hy_v = v.get("sofr_iorb"), v.get("reserves_ratio"), v.get("nfci"), v.get("hy")
    if sofr is not None and sofr > 0 or ratio is not None and ratio < 9:
        fund, fl = "偏紧", "watch"
    elif ratio is not None and ratio < 11:
        fund, fl = "平稳，但准备金接近下限", "watch"
    else:
        fund, fl = "充裕", "ok"
    fin = None if nfci_v is None else "宽松" if nfci_v < -0.3 else "略松" if nfci_v < 0 else "偏紧"
    ig_v = v.get("ig")
    hy_lvl = credit_level("hy", hy_v) if hy_v is not None else None
    ig_lvl = credit_level("ig", ig_v) if ig_v is not None else None
    cred = hy_lvl
    loose = fin in ("宽松", "略松") and fund != "偏紧"
    label = "宽松，资金面需留意" if loose and fl != "ok" else "宽松" if loose else \
        "资金面偏紧" if fund == "偏紧" else "中性"
    # 标签已经写了结论，理由的小标题只写看的是哪一块，结论放在句尾
    fund_t = "；".join(x for x in (
        None if sofr is None else f"SOFR 比 IORB {_hi_lo(sofr, 0, ' 基点')}，"
                                  + ("回购资金偏紧" if sofr > 0 else "回购资金不紧（高于 0 才算紧）"),
        None if ratio is None else f"准备金占 GDP {ratio:.1f}%，" + (
            "已低于 9–11% 的充足下限" if ratio < 9 else "已在 9–11% 的充足下限区间" if ratio < 11
            else "高于 9–11% 的充足下限")) if x)
    why = _why(
        ("资金面", fund_t) if sofr is not None or ratio is not None else None,
        ("金融条件", f"芝加哥联储 NFCI {nfci_v:.2f}，" + {"宽松": "低于 −0.3，宽松", "略松": "在 −0.3 到 0 之间，略松",
                                                     "偏紧": "高于 0（历史平均），偏紧"}[fin]) if fin else None,
        ("信用", "；".join(x for x in (
            None if hy_v is None else f"高收益债利差 {hy_v:.2f}%，{hy_lvl}",
            None if ig_v is None else f"投资级利差 {ig_v:.2f}%，{ig_lvl}",
        ) if x)) if hy_v is not None or ig_v is not None else None,
    )
    summary = "，".join(x for x in (fin and f"金融条件{fin}", cred and f"信用利差{cred}") if x)
    if sofr is not None or ratio is not None:
        summary += ("；" if summary else "") + f"资金面{fund}"
    anchors = {
        "sofr_iorb": "高于 0 说明回购资金偏紧",
        "reserves_ratio": "充足下限估计约 9–11%",
        "nfci": "0 为历史平均；< 0 略松，< −0.3 宽松",
        "hy": credit_anchor("hy"),
        "ig": credit_anchor("ig"),
    }
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False}


def fiscal_state(v: dict) -> dict:
    d, chg, it, debt = v.get("deficit"), v.get("deficit_chg12"), v.get("interest"), v.get("debt")
    imp = None if chg is None else "收缩" if chg < -0.5 else "扩张" if chg > 0.5 else "中性"
    press = None if it is None else "高" if it >= 3 else "偏高" if it >= 2.5 else "可控"
    label = f"脉冲{imp or '—'} · 偿债压力{press or '—'}"
    imp_t = None
    if imp:
        imp_t = (f"赤字率 {d:.1f}% GDP（滚动 12 个月），" if d is not None else "赤字率") + \
            ("与一年前差不多" if imp == "中性" else f"比一年前{'收窄' if chg < 0 else '扩大'} {abs(chg):.1f} 个百分点") + \
            {"收缩": "，政府少花钱，对增长是拖累", "扩张": "，政府多花钱，托底增长", "中性": "，对增长影响不大"}[imp]
    press_t = None
    if press:
        press_t = f"利息支出占 GDP {it:.2f}%，" + ("已超过 1990 年代峰值约 3.2%" if it >= 3.2 else
                                                   f"1990 年代峰值约 3.2%，{press}") + \
            (f"；联邦债务占 GDP {debt:.0f}%" if debt is not None else "")
    why = _why(("财政脉冲", imp_t) if imp else None, ("偿债压力", press_t) if press else None)
    summary = "；".join(x for x in (
        {"收缩": "赤字在收窄，对增长是拖累", "扩张": "赤字在扩大，托底增长", "中性": "赤字变化不大，对增长影响中性"}.get(imp or ""),
        press and ("利息负担已超过 1990 年代峰值" if it >= 3.2 else f"偿债压力{press}")) if x)
    anchors = {
        "deficit": f"脉冲看 12 个月变化：较一年前 {_f(chg, '{:+.1f}')} 个百分点，超过 ±0.5 算收缩或扩张",
        "interest": "1990 年代峰值约 3.2%；≥ 2.5% 偏高，≥ 3% 高",
        "debt": "≥ 100% 偏高",
    }
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False}


# 较上期变动。五个固定词，方便筛选：明显转好 / 略转好 / 持平 / 略转差 / 明显转差。
# 方向按这条指标自己的合意方向，不是涨跌本身：
# 增长类数值上升为好（失业率相反）；通胀类上升为差；
# 流动性里利差和 NFCI 上升为差，准备金占比上升为好；
# 财政里赤字率上升视为对增长更托底（好），利息和债务上升为差；
# 货币里实际利率和隐含 EFFR 上升是更紧，算差。
# 每条：(上升算好, 略的下限, 明显的下限, 明显是否必须严格大于下限)。
_CHANGE = {
    "core_gdp": (True, 0.3, 1.0, False),
    "gdp_q": (True, 0.3, 1.0, False),
    "gdpnow": (True, 0.3, 1.0, False),
    "nfp3": (True, 20.0, 50.0, False),
    "unrate": (False, 0.1, 0.3, False),
    "real_pce": (True, 0.5, 1.5, False),
    "core_capex": (True, 1.0, 3.0, False),
    "core_yoy": (False, 0.1, 0.3, False),
    "core_3m": (False, 0.2, 0.5, False),
    "supercore": (False, 0.1, 0.3, False),
    "fwd": (False, 0.05, 0.15, False),
    "nowcast": (False, 0.1, 0.3, False),
    "sofr_iorb": (False, 2.0, 5.0, False),
    "reserves_ratio": (True, 0.3, 0.8, False),
    "nfci": (False, 0.1, 0.25, False),
    # 信用利差的变动在 change_move 里单独写：走阔 / 收窄，不套用好坏。
    "deficit": (True, 0.3, 0.5, False),
    "interest": (False, 0.1, 0.25, False),
    "debt": (False, 1.0, 3.0, False),
    "real_policy": (False, 0.1, 0.25, False),
    "meeting": (False, 0.05, 0.10, True),
    "year_end": (False, 0.05, 0.10, True),
    "next_year": (False, 0.05, 0.10, True),
}


def change_move(metric_id: str, delta: float | None) -> dict | None:
    """较上期的变动 → 固定词（明显转好 / 略转好 / …）。没有这条规则或没有上期时返回 None。"""
    if delta is None:
        return None
    if metric_id in _SPREAD:
        return _spread_move(metric_id, delta)
    spec = _CHANGE.get(metric_id)
    if spec is None:
        return None
    up_good, mild, strong, strict = spec
    mag = abs(delta)
    if mag < mild:
        return _st("flat", "持平")
    big = mag > strong if strict else mag >= strong
    # 隐含 EFFR：上升是转鹰，下降是转鸽，不套用别的指标的好坏。
    if metric_id in ("meeting", "year_end", "next_year"):
        if delta > 0:
            return _st("hawk2", "明显转鹰") if big else _st("hawk1", "略转鹰")
        return _st("dove2", "明显转鸽") if big else _st("dove1", "略转鸽")
    good = (delta > 0) == up_good
    if big:
        return _st("good2", "明显转好") if good else _st("bad2", "明显转差")
    return _st("good1", "略转好") if good else _st("bad1", "略转差")


# 兼容旧名
change_status = change_move


_OUTPUT_KEYS = {"偏强": "strong", "接近潜在": "potential", "低于潜在": "below", "停滞或收缩": "stall"}
_LABOR_KEYS = {"强": "strong", "降温": "cool", "疲弱": "weak", "恶化": "worse"}


def _labor_band(nfp3: float, u12: float | None = None, sahm: float | None = None) -> str:
    if (sahm is not None and sahm >= 0.5) or (u12 is not None and u12 >= 0.5) or nfp3 < 0:
        return "恶化"
    if nfp3 >= 150 and (u12 is None or u12 <= 0.2):
        return "强"
    if nfp3 >= 50:
        return "降温"
    return "疲弱"


def _inflation_band(yoy: float) -> tuple[str, str]:
    gap = yoy - 2
    if gap >= 0.75:
        return "hot2", "明显高于目标"
    if gap >= 0.25:
        return "hot1", "略高于目标"
    if gap > -0.25:
        return "on_target", "接近目标"
    return "cold", "低于目标"


def path_status(current: float | None, implied: float | None) -> dict | None:
    """市场隐含路径相对当前 EFFR：定价加息 / 定价降息 / 按兵不动。"""
    if current is None or implied is None:
        return None
    n = (implied - current) / 0.25
    if abs(n) <= 0.5:
        return _st("hold", "按兵不动")
    if n > 0:
        return _st("hike", f"定价加息约 {abs(n):.1f} 次") if abs(n) >= 1 else _st("hike", "定价加息")
    return _st("cut", f"定价降息约 {abs(n):.1f} 次") if abs(n) >= 1 else _st("cut", "定价降息")


def level_status(metric_id: str, value: float | None, extra: dict | None = None) -> dict | None:
    """指标当前水平 →「状态」列。对照列只写分档规则，不重复水平词。"""
    if value is None:
        return None
    extra = extra or {}
    if metric_id in ("core_gdp", "gdp_q", "gdpnow", "real_pce"):
        _, txt = _output_band(value)
        return _st(_OUTPUT_KEYS[txt], txt)
    if metric_id == "nfp3":
        txt = _labor_band(value, extra.get("unrate_chg12"), extra.get("sahm"))
        return _st(_LABOR_KEYS[txt], txt)
    if metric_id == "unrate":
        sahm, u12 = extra.get("sahm"), extra.get("unrate_chg12")
        if sahm is not None and sahm >= 0.5:
            return _st("alert", "警报")
        if u12 is not None and u12 >= 0.5:
            return _st("up2", "明显上升")
        if u12 is not None and u12 <= -0.5:
            return _st("down2", "明显下降")
        if u12 is not None and u12 >= 0.2:
            return _st("up1", "上升")
        if u12 is not None and u12 <= -0.2:
            return _st("down1", "下降")
        return _st("stable", "平稳")
    if metric_id == "core_capex":
        # 设备投资同比：≥ 5 偏强，0–5 平稳，< 0 走弱
        if value >= 5:
            return _st("strong", "偏强")
        if value >= 0:
            return _st("stable", "平稳")
        return _st("weak", "走弱")
    if metric_id in ("core_cpi_mom", "core_pce_mom", "supercore_cpi_mom", "supercore_pce_mom"):
        return _mom_level(value, metric_id.startswith("supercore"))
    if metric_id == "core_yoy":
        return _st(*_inflation_band(value))
    if metric_id == "core_3m":
        yoy = extra.get("core_yoy")
        if yoy is None:
            return _st(*_inflation_band(value))
        mom = value - yoy
        if mom > 0.3:
            return _st("accel", "短期在加速")
        if mom < -0.3:
            return _st("slow", "短期在放缓")
        return _st("flat_mom", "短期动能持平")
    if metric_id == "supercore":
        if value <= 2.75:
            return _st("cooled", "已降温")
        if value < 3.5:
            return _st("elevated", "偏高")
        return _st("sticky", "粘性强")
    if metric_id == "fwd":
        if 2 <= value <= 2.5:
            return _st("anchored", "锚定")
        if value > 2.5:
            return _st("upshift", "上移")
        return _st("low", "偏低")
    if metric_id == "nowcast":
        gap = extra.get("nowcast_gap")
        if gap is None:
            return None
        word = _nowcast_word(gap)
        key = {"明显偏高": "high2", "略高": "high1", "持平": "flat", "略低": "low1", "明显偏低": "low2"}[word]
        return _st(key, word)
    if metric_id == "sofr_iorb":
        return _st("tight", "偏紧") if value > 0 else _st("ok", "不紧")
    if metric_id == "reserves_ratio":
        if value < 9:
            return _st("tight", "低于下限")
        if value < 11:
            return _st("watch", "接近下限")
        return _st("ample", "充裕")
    if metric_id == "nfci":
        if value < -0.3:
            return _st("loose", "宽松")
        if value < 0:
            return _st("easy", "略松")
        return _st("tight", "偏紧")
    if metric_id in _SPREAD:
        return credit_status(metric_id, value)
    if metric_id == "deficit":
        chg = extra.get("deficit_chg12")
        if chg is None:
            return None
        if chg < -0.5:
            return _st("contract", "收缩")
        if chg > 0.5:
            return _st("expand", "扩张")
        return _st("neutral", "中性")
    if metric_id == "interest":
        if value >= 3:
            return _st("high", "高")
        if value >= 2.5:
            return _st("elevated", "偏高")
        return _st("ok", "可控")
    if metric_id == "debt":
        return _st("elevated", "偏高") if value >= 100 else _st("ok", "正常")
    if metric_id == "real_policy":
        if value < 0:
            return _st("easy", "宽松")
        if value < 1:
            return _st("neutral", "接近中性")
        if value < 2:
            return _st("tight", "偏紧")
        return _st("restrictive", "紧缩")
    if metric_id in ("meeting", "year_end", "next_year"):
        return path_status(extra.get("current"), value)
    return None


def policy_state(v: dict) -> dict:
    real, cur, ye, ny, dot_ny = (v.get(k) for k in ("real_policy", "current", "year_end", "next_year", "dot_next"))
    stance = None if real is None else "宽松" if real < 0 else "接近中性" if real < 1 else "偏紧" if real < 2 else "紧缩"

    def moves(x: float) -> str:
        n = (x - cur) / 0.25
        return "不变" if abs(n) <= 0.5 else f"{'加息' if n > 0 else '降息'}约 {abs(n):.1f} 次"

    path, n = None, None
    if cur is not None and ny is not None:
        n = (ny - cur) / 0.25
        path = "按兵不动" if abs(n) <= 0.5 else moves(ny)
    gap = None if ny is None or dot_ny is None else ny - dot_ny
    priced = "加息" if n and n > 0.5 else "降息" if n and n < -0.5 else "不变" if n is not None else "—"
    label = f"立场{stance or '—'} · 市场定价{priced}"
    stance_t = None
    if real is not None:
        stance_t = f"实际政策利率（EFFR − 核心 PCE 同比）{real:.2f}%，" + (
            "低于 0，政策在刺激" if stance == "宽松" else
            "略低于约 0.5–1% 的中性估计，谈不上紧" if real < 0.5 else
            "在约 0.5–1% 的中性估计之内" if stance == "接近中性" else
            "高于约 0.5–1% 的中性估计，在压需求" if stance == "偏紧" else "比中性估计高 1 个百分点以上")
    path_t = None
    if path:
        meet = v.get("next_meet")
        bits = []
        if meet is not None:
            bits.append(f"下次会议 {meet:.2f}%")
        if ye is not None:
            bits.append(f"年底 {ye:.2f}%")
        bits.append(f"明年底 {ny:.2f}%")
        path_t = f"当前 EFFR {cur:.2f}%，市场隐含{'、'.join(bits)}，相当于到明年底{path}"
        if gap is not None and abs(gap) >= 0.25:
            path_t += f"；比点阵图 {dot_ny:.2f}% {'鹰' if gap > 0 else '鸽'} {abs(gap) * 100:.0f}bp"
    why = _why(("当前立场", stance_t) if stance else None, ("市场路径", path_t) if path else None)
    summary = "；".join(x for x in (
        stance and f"政策立场{stance}",
        path and (f"市场定价到明年底{path}"
                  + (f"，比美联储点阵图更{'鹰' if gap > 0 else '鸽'}" if gap is not None and abs(gap) >= 0.25 else ""))) if x)
    anchors = {"real_policy": "中性约 0.5–1%：< 0 宽松，< 1 接近中性，1–2 偏紧，≥ 2 紧缩"}
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False, "hikes": n}


_HIKE = re.compile(r"隐含(加息|降息)\s*([\d.]+)\s*次")
# 旧备注是「4.00 - 4.25(51.9%)」，新备注把概率拿掉，只留「4.00 - 4.25」
_BAND = re.compile(r"最大概率区间\s*([\d.]+)\s*-\s*([\d.]+)(?:\s*[（(]\s*([\d.]+)\s*%\s*[）)])?")
_LEVEL = {"": 0, "略转鸽": 1, "略转鹰": 1, "明显转鸽": 2, "明显转鹰": 2}


def stronger(a: str, b: str) -> str:
    return a if _LEVEL.get(a, 0) >= _LEVEL.get(b, 0) else b


def _bp(delta: float) -> float:
    """百分点差换成基点，保留一位小数，避开二进制误差。"""
    return round(delta * 100, 1)


def _move_text(delta: float) -> str:
    bp = _bp(delta)
    num = f"{abs(bp):.1f}".rstrip("0").rstrip(".")
    return f"{'上涨' if bp > 0 else '下跌'} {num}bp"


def daily_delta(series: list[tuple[date, float]]) -> float | None:
    """上一条正好是前一天时，才算当日变动。"""
    if len(series) < 2:
        return None
    (d0, v0), (d1, v1) = series[-2], series[-1]
    if (d1 - d0).days != 1:
        return None
    return v1 - v0


def five_day_delta(series: list[tuple[date, float]]) -> float | None:
    """最新值减去 5 个日历日之前最后一条。历史不够就不算。"""
    if not series:
        return None
    end_d, end_v = series[-1]
    prior = [v for d, v in series if d <= end_d - timedelta(days=5)]
    if not prior:
        return None
    return end_v - prior[-1]


def _hawk_dove(delta: float, big: bool) -> str:
    """利率上升为转鹰，下降为转鸽。"""
    if delta > 0:
        return "明显转鹰" if big else "略转鹰"
    return "明显转鸽" if big else "略转鸽"


def tenor_signals(series: list[tuple[date, float]]) -> tuple[str, list[str]]:
    """单条隐含利率。

    当日变动达到 5bp 为略转鹰或略转鸽，大于 10bp 为明显转鹰或明显转鸽。
    5 日累计超过 5bp、以及超过 0.1%（10bp），都只到「略」。
    """
    level, reasons = "", []
    daily = daily_delta(series)
    if daily is not None:
        bp = abs(_bp(daily))
        if bp > 10:
            level = _hawk_dove(daily, True)
            reasons.append(f"当日{_move_text(daily)}，大于 10bp")
        elif bp >= 5:
            level = _hawk_dove(daily, False)
            reasons.append(f"当日{_move_text(daily)}，达到 5bp")
    five = five_day_delta(series)
    if five is not None:
        bp = abs(_bp(five))
        mild = _hawk_dove(five, False)
        if bp > 10:
            level = stronger(level, mild)
            reasons.append(f"5 日累计{_move_text(five)}，超过 0.1%")
        elif bp > 5:
            level = stronger(level, mild)
            reasons.append(f"5 日累计{_move_text(five)}，超过 5bp")
    return level, reasons


def _turn_word(delta: float, bp: float) -> str:
    if bp <= 1:
        return "几乎不动"
    return "转鹰" if delta > 0 else "转鸽"


def cross_signal(meeting: list[tuple[date, float]], year: list[tuple[date, float]]) -> tuple[str, list[str]]:
    """下次会议（短期）和年底（长期）方向相反，或一个超过 5bp、另一个几乎不动，提示背离。"""
    dm, dy = daily_delta(meeting), daily_delta(year)
    if dm is None or dy is None:
        return "", []
    bm, by = abs(_bp(dm)), abs(_bp(dy))
    opposite = dm * dy < 0 and bm > 1 and by > 1
    one_sided = (bm > 5 and by <= 1) or (by > 5 and bm <= 1)
    if not opposite and not one_sided:
        return "", []
    text = f"背离（短期{_turn_word(dm, bm)}，长期{_turn_word(dy, by)}）"
    return "背离", [text]


def implied_anchor(remark: str, level: str = "", reasons: list[str] | None = None, diverge: str = "") -> str:
    """对照：隐含加息次数和最大概率区间；有信号时接上理由。"""
    remark = remark or ""
    hike, band = _HIKE.search(remark), _BAND.search(remark)
    parts = []
    if hike:
        parts.append(f"隐含{hike.group(1)}{hike.group(2)}次")
    if band:
        rng = f"最大概率区间 {band.group(1)} - {band.group(2)}"
        if band.group(3):
            rng += f"（{band.group(3)}%）"
        parts.append(rng)
    text = "，".join(parts)
    reasons = [r for r in (reasons or []) if r]
    bits = []
    if level and reasons:
        bits.append(f"{level}：" + "；".join(reasons))
    if diverge:
        bits.append(diverge)
    if bits:
        text = (text + "。" if text else "") + "。".join(bits)
    return text


# 增长 × 通胀九宫格：每格一句白话，说明这个名字是什么意思
REGIMES = {
    (1, 1): ("过热", "增长强于潜在，通胀高于目标"),
    (0, 1): ("通胀粘性", "增长大致在潜在水平，通胀仍高于目标"),
    (-1, 1): ("滞胀风险", "增长放缓，通胀仍高于目标"),
    (1, 0): ("稳健扩张", "增长强于潜在，通胀接近目标"),
    (0, 0): ("金发姑娘", "增长大致在潜在水平，通胀接近目标"),
    (-1, 0): ("放缓", "增长低于潜在，通胀接近目标"),
    (1, -1): ("复苏", "增长强于潜在，通胀仍低于目标"),
    (0, -1): ("低通胀", "增长大致在潜在水平，通胀低于目标"),
    (-1, -1): ("衰退风险", "增长放缓，通胀低于目标"),
}


def environment(g: dict, i: dict, p: dict, liq: dict, f: dict | None = None) -> dict:
    """增长 × 通胀定环境名；下面四行分别写经济、流动性、财政、货币。"""
    gl, il = g.get("level"), i.get("level")
    if gl is None or il is None:
        return {"name": "数据不足", "head": "数据不足", "lines": []}
    name, meaning = REGIMES[(gl, il)]
    if g.get("split") and g.get("summary"):
        # 分化时档位取了较弱的一侧，「增长大致在潜在水平」会把强的那一侧抹平；直接用增长那句
        meaning = g["summary"] + "，" + meaning.split("，", 1)[1]
    econ = "；".join(x for x in (g.get("summary"), i.get("summary")) if x)
    lines = [{"k": k, "t": t} for k, t in (
        ("经济", econ), ("流动性", liq.get("summary")), ("财政", (f or {}).get("summary")), ("货币", p.get("summary")),
    ) if t]
    return {"name": name, "head": f"{name}：{meaning}", "lines": lines}
