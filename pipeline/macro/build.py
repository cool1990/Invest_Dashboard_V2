"""把原始 FRED 序列整理成页面要用的 data/macro/dashboard.json。

页面分三层，每层只放别的层没有的东西：
- 结论：verdict（增长 × 通胀定环境名，下面四行白话写经济、流动性、财政、货币）
  和 dimensions 的 label / why（五个维度各一个标签，理由写用哪几个数、对照什么锚点得出）。
  不用 z 分数：和「2000 年以来平均」比没有经济含义，等权汇总又会把分歧抵消掉。
- 依据：dimensions 的 metrics（决定标签的几个数：最新值、较上期、对照的锚点），
  sections + charts（这几个数对应的图，core=True 的默认展开，其余折叠），
  通胀一块另附克利夫兰联储 Nowcast 与实际的对照（releases.nowcast）。
- 时间：releases 的即将发布（附情景门槛）、最近发布（实际 vs 预期），以及每条已公布数据的变动和会不会改判断。

缺数据的序列会被跳过；某张图一条序列都没有，就不输出这张图。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import series as ts
from ..page import FREQ, Chart, Line, Metric, metric_json, period_text
from ..series import Series
from . import consensus as cons
from . import cpi_contrib
from . import interpret as I
from . import importance
from .scenario import Scenario

RECENT_DAYS = 30
UPCOMING_DAYS = 10


def gdp_mix(contrib: dict[str, Series], q: date) -> str:
    """某一季 GDP 增速的分项贡献，写成一行备注。"""
    names = (("pce", "消费"), ("fixed", "固定投资"), ("inv", "库存"), ("gov", "政府"), ("nx", "净出口"))
    parts = [(nm, dict(contrib[k]).get(q)) for k, nm in names]
    parts = [f"{nm} {v:+.1f}" for nm, v in parts if v is not None]
    return "贡献：" + "，".join(parts) if parts else ""


# ---------------------------------------------------------------------------


class MacroBuilder:
    def __init__(self, raw: dict[str, Series], effr_expect: list[dict] | None = None,
                 asof: date | None = None, events: list[dict] | None = None,
                 nowcast: list[dict] | None = None, now: datetime | None = None):
        self.raw = raw
        self.effr_expect = effr_expect or []
        self.asof = asof or date.today()
        self.events = events or []
        self.nowcast = nowcast or []
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}
        self.actuals: dict[str, Series] = {}  # 日历指标 key → FRED 实际值（与预期同单位）
        # 情景试算要用：各维度状态函数的输入、状态本身、以及几条原始序列
        self.inputs: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.ctx: dict[str, Series] = {}

    # -- 取数 ---------------------------------------------------------------
    def s(self, sid: str) -> Series:
        return self.raw.get(sid) or []

    def m(self, sid: str, how: str = "mean") -> Series:
        return ts.to_monthly(self.s(sid), how)

    def w(self, sid: str, k: float = 1.0) -> Series:
        return ts.scale(ts.to_weekly(self.s(sid)), k)

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    def yoy_m(self, sid: str) -> Series:
        return ts.pct_change(self.m(sid, "last"), 12, "M")

    # =====================================================================
    # 增长
    def growth(self):
        gdp = self.s("GDPC1")
        gdp_qoq = ts.pct_change(gdp, 1, "Q", annualize=4)
        gdp_yoy = ts.pct_change(gdp, 4, "Q")
        by_year: dict[int, list[float]] = {}
        for d, v in gdp:
            by_year.setdefault(d.year, []).append(v)
        annual = [(date(y, 1, 1), sum(v) / 4) for y, v in sorted(by_year.items()) if len(v) == 4]
        gdp_annual = ts.pct_change(annual, 1, "A")
        gdpnow = self.s("GDPNOW")
        core_gdp = self.s("PB0000031Q225SBEA")
        contrib = {k: self.s(sid) for k, sid in (
            ("pce", "DPCERY2Q224SBEA"), ("fixed", "A007RY2Q224SBEA"), ("inv", "A014RY2Q224SBEA"),
            ("gov", "A822RY2Q224SBEA"), ("nx", "A019RY2Q224SBEA"))}

        payems = self.m("PAYEMS", "last")
        nfp = ts.diff(payems, 1, "M")
        nfp3 = ts.rolling(nfp, 3)
        unrate = self.m("UNRATE", "last")
        u6 = self.m("U6RATE", "last")
        sahm = self.m("SAHMREALTIME", "last")
        civpart = self.m("CIVPART", "last")
        jol = self.m("JTSJOL", "last")
        vu = ts.combine(lambda a, b: a / b if b else None, jol, self.m("UNEMPLOY", "last"))
        quits = self.m("JTSQUR", "last")
        ahe = self.m("CES0500000003", "last")
        ahe_yoy = ts.pct_change(ahe, 12, "M")
        ahe_3m = ts.pct_change(ahe, 3, "M", annualize=12)
        icsa4 = ts.scale(ts.rolling_obs(self.s("ICSA"), 4), 1e-3)
        ccsa = ts.scale(self.s("CCSA"), 1e-3)

        rs = self.m("RSAFS", "last")
        mv = self.m("RSMVPD", "last")
        ctrl = ts.combine(lambda a, b, c, d, e: a - b - c - d - e, rs, mv,
                          self.m("RSGASS", "last"), self.m("RSBMGESD", "last"), self.m("RSFSDP", "last"))
        rs_yoy = ts.pct_change(rs, 12, "M")
        rs_mom = ts.pct_change(rs, 1, "M")
        ctrl_yoy = ts.pct_change(ctrl, 12, "M")
        ctrl_mom = ts.pct_change(ctrl, 1, "M")
        rpce_yoy = self.yoy_m("PCEC96")
        rpce_mom = ts.pct_change(self.m("PCEC96", "last"), 1, "M")
        rdpi_yoy = self.yoy_m("DSPIC96")
        saving = self.m("PSAVERT", "last")
        credit_yoy = self.yoy_m("TOTALSL")
        revol_yoy = self.yoy_m("REVOLSL")
        cc_delinq = self.s("DRCCLACBS")

        dgo = self.m("DGORDER", "last")
        dgo_yoy = ts.pct_change(ts.rolling(dgo, 3), 12, "M")
        core_capex_yoy = ts.pct_change(ts.rolling(self.m("NEWORDER", "last"), 3), 12, "M")
        philly = self.m("GACDFSA066MSFRBPHI", "last")
        empire = self.m("GACDISA066MSFRBNY", "last")
        indpro_yoy = self.yoy_m("INDPRO")

        houst = self.m("HOUST", "last")
        permit = self.m("PERMIT", "last")
        hsn = self.m("HSN1F", "last")
        msacsr = self.m("MSACSR", "last")
        cs_yoy = self.yoy_m("CSUSHPINSA")
        mort = self.w("MORTGAGE30US")

        self.actuals.update({
            "gdp_qoq": gdp_qoq, "nfp": nfp, "unrate": unrate, "ahe_mom": ts.pct_change(ahe, 1, "M"),
            "retail_mom": rs_mom,
            "retail_exauto_mom": ts.pct_change(ts.combine(lambda a, b: a - b, rs, mv), 1, "M"),
            "jolts": jol, "icsa": ts.scale(self.s("ICSA"), 1e-3), "dgo_mom": ts.pct_change(dgo, 1, "M"),
            "permit": permit, "houst": houst, "hsn": hsn, "empire": empire, "philly": philly,
        })

        groups = [
            ("产出", self.add(
                Chart("gdp_q", "实际 GDP 与核心 GDP：季环比年化", "%", [
                    Line("GDP 季环比年化", gdp_qoq, "bar"), Line("核心 GDP 季环比年化", core_gdp),
                    Line("GDP 同比", gdp_yoy, dash=True)],
                    note="季度数据，来源 BEA。核心 GDP = 对私人国内购买者的最终销售（消费 + 固定投资），"
                         "去掉了净出口、库存和政府，更能代表内需冷热。", core=True),
                Chart("gdp_contrib", "GDP 增速按分项贡献", "百分点", [
                    Line("消费", contrib["pce"], "bar"), Line("固定投资", contrib["fixed"], "bar"),
                    Line("库存", contrib["inv"], "bar"), Line("政府", contrib["gov"], "bar"),
                    Line("净出口", contrib["nx"], "bar"), Line("GDP 季环比年化", gdp_qoq)],
                    stacked="bar", start=date(2010, 1, 1), core=True,
                    note="柱子之和 ≈ GDP 季环比年化（BEA 公布的贡献值，年化百分点）。"
                         "净出口和库存常常一个季度大正、下个季度大负，比如抢在关税前进口。"),
                Chart("gdpnow", "GDPNow：本季度实时预测", "%", [Line("GDPNow", gdpnow)],
                      note="亚特兰大联储对当季实际 GDP 环比年化的模型预测。", start=date(2014, 1, 1)),
                Chart("gdp_a", "实际 GDP：年度增速", "%", [Line("年度增速", gdp_annual, "bar")],
                      note="按全年四个季度均值计算。"),
                Chart("indpro", "工业产出与实际可支配收入同比", "%", [
                    Line("工业产出同比", indpro_yoy), Line("实际可支配收入同比", rdpi_yoy)]),
            )),
            ("就业", self.add(
                Chart("nfp", "非农新增就业", "千人", [
                    Line("当月新增", nfp, "bar"), Line("3 个月均值", nfp3)],
                    note="2020 年疫情期间数值极端，建议看近 5 年。", core=True),
                Chart("unrate", "失业率", "%", [
                    Line("U-3 失业率", unrate), Line("U-6 失业率", u6)], core=True),
                Chart("sahm", "Sahm 规则衰退指标", "百分点", [Line("Sahm 实时", sahm)],
                      note="失业率 3 个月均值较前 12 个月低点上升 ≥0.5 个百分点，历史上对应衰退开始。"),
                Chart("jolts", "职位空缺 / 失业人数", "倍", [Line("空缺/失业", vu)],
                      note="大于 1 表示职位多于求职者，劳动力市场偏紧。"),
                Chart("jolts_lv", "职位空缺数", "百万个", [Line("职位空缺", ts.scale(jol, 1e-3))]),
                Chart("quits", "离职率与劳动参与率", "%", [
                    Line("离职率", quits), Line("劳动参与率", civpart)]),
                Chart("ahe", "平均时薪增速", "%", [
                    Line("同比", ahe_yoy), Line("3 个月年化", ahe_3m, dash=True)]),
                Chart("claims", "初请失业金（4 周均值）", "千人", [Line("初请 4 周均值", icsa4)],
                      start=date(2000, 1, 1)),
                Chart("ccsa", "续请失业金", "千人", [Line("续请", ccsa)], start=date(2000, 1, 1)),
            )),
            ("消费", self.add(
                Chart("retail_yoy", "零售销售同比", "%", [
                    Line("零售总额", rs_yoy), Line("控制组", ctrl_yoy)],
                    note="控制组 = 零售总额 − 汽车 − 加油站 − 建材 − 餐饮，进入 GDP 的消费核算。", core=True),
                Chart("retail_mom", "零售销售环比", "%", [
                    Line("零售总额", rs_mom, "bar"), Line("控制组", ctrl_mom, "bar")]),
                Chart("real_pce", "实际个人消费支出", "%", [
                    Line("同比", rpce_yoy), Line("环比", rpce_mom, "bar")]),
                Chart("saving", "个人储蓄率", "%", [Line("储蓄率", saving)]),
                Chart("credit", "消费者信贷同比", "%", [
                    Line("消费者信贷总额", credit_yoy), Line("循环信贷（信用卡为主）", revol_yoy)]),
                Chart("cc_delinq", "信用卡贷款拖欠率", "%", [Line("拖欠率", cc_delinq)],
                      note="季度，商业银行口径，来源美联储。"),
            )),
            ("企业与制造业", self.add(
                Chart("durables", "耐用品订单同比（3 个月均值）", "%", [
                    Line("耐用品新订单", dgo_yoy), Line("核心资本品（非国防除飞机）", core_capex_yoy)]),
                Chart("regional", "地区联储制造业调查", "扩散指数", [
                    Line("费城联储", philly), Line("纽约联储", empire)],
                    note="ISM PMI 不在 FRED，这里先用地区联储调查代替；0 以上为扩张。"),
            )),
            ("地产", self.add(
                Chart("housing_starts", "新屋开工与建筑许可", "千套（年化）", [
                    Line("新屋开工", houst), Line("建筑许可", permit)]),
                Chart("new_home", "新屋销售", "千套（年化）", [Line("新屋销售", hsn)]),
                Chart("home_supply", "新屋库存月数", "月", [Line("库存月数", msacsr)]),
                Chart("home_price", "房价同比（Case-Shiller 全国）", "%", [Line("同比", cs_yoy)]),
                Chart("mortgage", "30 年期房贷利率", "%", [Line("30 年固定", mort)]),
            )),
        ]

        rpce_3m = ts.pct_change(self.m("PCEC96", "last"), 3, "M", annualize=12)
        metrics = [
            Metric("core_gdp", "上季核心 GDP（季环比年化）", core_gdp, "%", "{:.1f}", "Q", "gdp_q",
                   note="对私人国内购买者的最终销售：消费 + 固定投资"),
            Metric("gdp_q", "上季实际 GDP（季环比年化）", gdp_qoq, "%", "{:.1f}", "Q", "gdp_contrib",
                   note=gdp_mix(contrib, gdp_qoq[-1][0]) if gdp_qoq else ""),
            Metric("gdpnow", "GDPNow 本季预测", gdpnow, "%", "{:.1f}", "Q", "gdpnow", model=True),
            Metric("nfp3", "非农新增就业 3 个月均值", nfp3, "千人", "{:,.0f}", chart="nfp",
                   note=f"单月 {nfp[-1][1]:,.0f} 千人（{period_text(nfp[-1][0], 'M')}）" if nfp else ""),
            Metric("unrate", "失业率", unrate, "%", "{:.1f}", chart="unrate"),
            Metric("real_pce", "实际消费支出（3 个月年化）", rpce_3m, "%", "{:.1f}", chart="real_pce", ref=True),
            Metric("core_capex", "核心资本品订单（3 个月均值同比）", core_capex_yoy, "%", "{:.1f}",
                   chart="durables", ref=True, note="非国防、除飞机"),
        ]
        # 核心 GDP 和分项贡献只取与 GDP 同一季度的值，避免拿新旧两个季度比
        q = gdp_qoq[-1][0] if gdp_qoq else None
        core_q = dict(core_gdp).get(q) if q else (core_gdp[-1][1] if core_gdp else None)
        self.inputs["growth"] = {
            "gdpnow": gdpnow[-1][1] if gdpnow else None, "gdp_q": gdp_qoq[-1][1] if gdp_qoq else None,
            "nfp3": nfp3[-1][1] if nfp3 else None, "nfp3_ago": nfp3[-2][1] if len(nfp3) >= 2 else None,
            "unrate": unrate[-1][1] if unrate else None,
            "unrate_chg12": ts.diff(unrate, 12, "M")[-1][1] if len(unrate) > 12 else None,
            "sahm": sahm[-1][1] if sahm else None,
            "core_gdp": core_q, "contrib": {k: dict(v).get(q) for k, v in contrib.items()} if q else {},
        }
        self.ctx.update({"nfp": nfp, "unrate": unrate, "gdp_qoq": gdp_qoq})
        state = I.growth_state(self.inputs["growth"])
        return groups, metrics, state

    # =====================================================================
    # 通胀：PCE 按贡献拆分
    def pce_breakdown(self) -> dict[str, list[Line]]:
        """PCE 与核心 PCE 按贡献拆分，环比与同比各一套。

        某项对总体的贡献 ≈ 基期（环比用上月、同比用 12 个月前）的名义支出份额 × 该项价格变化。
        FRED 上月度数据不全，权重用最近可得的低频数据近似：
        - 住房名义 = 核心名义 × 上一年住房占核心的比重（DHSGRC1A027NBEA 为年度）
        - 核心商品名义 = 商品 − 食品 − 能源 × 能源商品占能源的比重（季度，沿用最近一季）
        - 超级核心名义 = 核心 − 核心商品 − 住房
        价格：核心 PCEPILFE、核心除住房 IA001176M、超级核心 IA001260M 都是月度。
        住房贡献 = 核心贡献 − 核心除住房贡献；核心商品贡献 = 核心除住房贡献 − 超级核心贡献。
        链式加总不严格可加，误差一般在 0.01 个百分点量级。
        """
        total_n = dict(self.m("PCE", "last"))
        core_n = dict(self.m("DPCCRC1M027SBEA", "last"))
        food_n = dict(self.m("DFXARC1M027SBEA", "last"))
        energy_n = dict(self.m("DNRGRC1M027SBEA", "last"))
        goods_n = dict(self.m("DGDSRC1", "last"))
        eg_share = ts.combine(lambda a, b: a / b if b else None, self.s("DGOERC1Q027SBEA"), self.s("DNRGRC1Q027SBEA"))

        core_year: dict[int, list[float]] = {}
        for d, v in core_n.items():
            core_year.setdefault(d.year, []).append(v)
        housing_w = {d.year: v / (sum(core_year[d.year]) / 12)
                     for d, v in self.s("DHSGRC1A027NBEA") if len(core_year.get(d.year, [])) == 12}

        def hw(d: date) -> float | None:
            for y in range(d.year - 1, d.year - 4, -1):
                if y in housing_w:
                    return housing_w[y]
            return None

        # 各块的月度名义支出
        housing_n, exh_n, super_n = {}, {}, {}
        for d, c in core_n.items():
            w = hw(d)
            if w is None:
                continue
            housing_n[d] = c * w
            exh_n[d] = c * (1 - w)
            eg = ts.asof(eg_share, d, 200)
            if eg is not None and d in goods_n and d in food_n and d in energy_n:
                core_goods = goods_n[d] - food_n[d] - energy_n[d] * eg
                super_n[d] = c - core_goods - housing_n[d]

        def contrib(num: dict, den: dict, price_id: str, lag: int) -> Series:
            out = []
            for d, chg in ts.pct_change(self.m(price_id, "last"), lag, "M"):
                base = ts._shift_month(d, -lag)
                if base in num and den.get(base):
                    out.append((d, num[base] / den[base] * chg))
            return out

        def lines(den: dict, lag: int, with_food_energy: bool) -> list[Line]:
            core_c = contrib(core_n, den, "PCEPILFE", lag)
            exh_c = contrib(exh_n, den, "IA001176M", lag)
            sc_c = contrib(super_n, den, "IA001260M", lag)
            out = []
            if with_food_energy:
                out += [Line("食品", contrib(food_n, den, "DFXARG3M086SBEA", lag), "bar"),
                        Line("能源", contrib(energy_n, den, "DNRGRG3M086SBEA", lag), "bar")]
            if exh_c:
                housing_c = ts.combine(lambda a, b: a - b, core_c, exh_c)
                if sc_c:
                    goods_c = ts.combine(lambda a, b: a - b, exh_c, sc_c)
                    out += [Line("核心商品", goods_c, "bar"), Line("住房", housing_c, "bar"),
                            Line("超级核心", sc_c, "bar")]
                else:
                    out += [Line("住房", housing_c, "bar"), Line("核心除住房", exh_c, "bar")]
            else:
                out.append(Line("核心", core_c, "bar"))
            return out

        return {"total_mom": lines(total_n, 1, True), "total_yoy": lines(total_n, 12, True),
                "core_mom": lines(core_n, 1, False), "core_yoy": lines(core_n, 12, False)}

    def inflation(self):
        pce = self.m("PCEPI", "last")
        core = self.m("PCEPILFE", "last")
        pce_yoy = ts.pct_change(pce, 12, "M")
        core_yoy = ts.pct_change(core, 12, "M")
        pce_mom = ts.pct_change(pce, 1, "M")
        core_mom = ts.pct_change(core, 1, "M")
        core_3m = ts.pct_change(core, 3, "M", annualize=12)
        core_6m = ts.pct_change(core, 6, "M", annualize=12)
        cpi = self.m("CPIAUCSL", "last")
        ccpi = self.m("CPILFESL", "last")
        cpi_yoy = ts.pct_change(cpi, 12, "M")
        ccpi_yoy = ts.pct_change(ccpi, 12, "M")
        cpi_mom = ts.pct_change(cpi, 1, "M")
        ccpi_mom = ts.pct_change(ccpi, 1, "M")
        cpi_3m = ts.pct_change(cpi, 3, "M", annualize=12)
        cpi_6m = ts.pct_change(cpi, 6, "M", annualize=12)
        ccpi_3m = ts.pct_change(ccpi, 3, "M", annualize=12)
        ccpi_6m = ts.pct_change(ccpi, 6, "M", annualize=12)
        br = self.pce_breakdown()
        cbr = cpi_contrib.breakdown(self.raw)
        supercore = self.m("IA001260M", "last")
        supercore_yoy = ts.pct_change(supercore, 12, "M")
        supercore_3m = ts.pct_change(supercore, 3, "M", annualize=12)
        goods_yoy = self.yoy_m("DGDSRG3M086SBEA")
        services_yoy = self.yoy_m("DSERRG3M086SBEA")
        fwd = self.w("T5YIFR")
        be10 = self.w("T10YIE")
        mich = self.m("MICH", "last")

        self.actuals.update({
            "pce_mom": pce_mom, "pce_yoy": pce_yoy, "core_pce_mom": core_mom, "core_pce_yoy": core_yoy,
            "cpi_mom": cpi_mom, "cpi_yoy": cpi_yoy, "core_cpi_mom": ccpi_mom,
            "core_cpi_yoy": ccpi_yoy, "mich": mich,
        })
        weight_note = ("权重用最近可得的低频数据近似：住房比重用上一年年度数据，能源商品比重用最近一季"
                       "（FRED 没有月度住房支出和汽油支出）。")

        groups = [
            ("核心 PCE", self.add(
                Chart("core_pce", "核心 PCE：环比与同比", "%", [
                    Line("环比", core_mom, "bar"), Line("同比", core_yoy)],
                    note="美联储 2% 目标针对 PCE 同比；核心环比约 0.17% 对应年化 2%。", core=True),
                Chart("core_contrib_mom", "核心 PCE 环比：按贡献拆分", "百分点",
                      br["core_mom"] + [Line("核心 PCE 环比", core_mom)], stacked="bar", start=date(2015, 1, 1),
                      note="对核心 PCE 的贡献 = 上月占核心的名义份额 × 分项价格环比。超级核心 = 服务除能源、住房。"
                           + weight_note, core=True),
                Chart("core_contrib_yoy", "核心 PCE 同比：按贡献拆分", "百分点",
                      br["core_yoy"] + [Line("核心 PCE 同比", core_yoy)], stacked="bar", start=date(2005, 1, 1),
                      note="对核心 PCE 的贡献 = 12 个月前占核心的名义份额 × 分项价格同比。", core=True),
                Chart("core_pce_ann", "核心 PCE 年化动能", "%", [
                    Line("3 个月年化", core_3m), Line("6 个月年化", core_6m), Line("同比", core_yoy, dash=True)],
                    core=True),
                Chart("supercore", "超级核心与商品、服务价格同比", "%", [
                    Line("超级核心（服务除能源、住房）", supercore_yoy), Line("超级核心 3 个月年化", supercore_3m, dash=True),
                    Line("PCE 商品", goods_yoy), Line("PCE 服务", services_yoy)],
                    note="超级核心是美联储最关注的与工资相关的服务通胀。"),
            )),
            ("整体 PCE", self.add(
                Chart("pce_yoy", "PCE 与核心 PCE 同比", "%", [
                    Line("PCE", pce_yoy), Line("核心 PCE", core_yoy)]),
                Chart("pce_mom", "PCE 与核心 PCE 环比", "%", [
                    Line("PCE", pce_mom, "bar"), Line("核心 PCE", core_mom, "bar")]),
                Chart("pce_contrib_mom", "PCE 环比：按贡献拆分", "百分点",
                      br["total_mom"] + [Line("PCE 环比", pce_mom)], stacked="bar", start=date(2015, 1, 1),
                      note="贡献 = 上月名义支出份额 × 分项价格环比。能源含汽油与电、燃气。" + weight_note),
                Chart("pce_contrib_yoy", "PCE 同比：按贡献拆分", "百分点",
                      br["total_yoy"] + [Line("PCE 同比", pce_yoy)], stacked="bar", start=date(2005, 1, 1),
                      note="贡献 = 12 个月前名义支出份额 × 分项价格同比。"),
            )),
            ("CPI 与预期", self.add(
                Chart("cpi", "CPI 与核心 CPI 同比", "%", [
                    Line("CPI", cpi_yoy), Line("核心 CPI", ccpi_yoy)],
                    note="数据表在有一致预期的月份另列预期，取日历里发布前最后一次看到的值；没有的月份留空。"),
                Chart("cpi_mom", "CPI 与核心 CPI 环比", "%", [
                    Line("CPI 环比", cpi_mom, "bar"), Line("核心 CPI 环比", ccpi_mom, "bar")],
                    note="季调环比。数据表的预期列与同比同一套日历，只填已经记下来的月份。"),
                Chart("cpi_ann", "CPI 年化动能（整体与核心）", "%", [
                    Line("核心 3 个月年化", ccpi_3m), Line("核心 6 个月年化", ccpi_6m),
                    Line("整体 3 个月年化", cpi_3m, dash=True), Line("整体 6 个月年化", cpi_6m, dash=True)],
                    note="由季调指数复利：3 个月年化 =（近 3 个月之比）^4 − 1，6 个月年化 =（近 6 个月之比）^2 − 1。"
                         "核心和整体都列。本站通胀标签仍看核心 PCE，不看这里。"
                         "没有指数的月份留空，不插值，也不把缺口两边当成相邻的一个月。"),
                Chart("cpi_contrib_mom", "CPI 环比：食品、能源、核心", "百分点", [
                    Line("食品", cbr["food"], "bar"), Line("能源", cbr["energy"], "bar"),
                    Line("核心", cbr["core"], "bar"), Line("CPI 环比", cpi_mom)],
                    stacked="bar", start=date(2015, 1, 1), digits=4,
                    note="对整体 CPI 的绝对贡献（百分点）。食品、核心 = 上月相对重要性 × 季调环比；"
                         "能源 = 整体环比 − 食品 − 核心，使三项相加等于整体环比，与 BLS 新闻稿 Table 6 的可加总一致。"
                         "比重用 BLS 每年 12 月 CPI-U，在同一套权重内按价格指数相对变化推到上一个月；"
                         "2026 年用 2025 年 12 月、2024 年权重的重置比重。跨过缺失月份的点留空。"),
                Chart("cpi_core_contrib_mom", "核心 CPI 环比：商品、住房、超级核心", "百分点", [
                    Line("核心商品", cbr["core_goods"], "bar"), Line("住房", cbr["shelter"], "bar"),
                    Line("超级核心", cbr["supercore"], "bar"), Line("核心 CPI 环比", ccpi_mom)],
                    stacked="bar", start=date(2015, 1, 1), digits=4,
                    note="对核心 CPI 的绝对贡献 = 对整体的贡献 ÷ 上月核心相对重要性。"
                         "核心商品 = 除食品与能源的商品；住房 = Shelter。"
                         "超级核心 = 核心服务剔除住房 = 服务除能源服务 − 住房"
                         "（BLS：Services less energy services − Shelter），没有单独指数。"
                         "三项大致加总到核心环比；公开指数四舍五入会留下大约 0.01 个百分点以内的残差。"),
                Chart("cpi_cleveland", "克利夫兰联储：中位数与 16% 截尾均值", "%", [
                    Line("中位数环比年化", self.s("MEDCPIM158SFRBCLE")),
                    Line("中位数同比", self.s("MEDCPIM159SFRBCLE")),
                    Line("16% 截尾均值环比年化", self.s("TRMMEANCPIM158SFRBCLE"), dash=True),
                    Line("16% 截尾均值同比", self.s("TRMMEANCPIM159SFRBCLE"), dash=True)],
                    note="环比年化是 FRED 的月度 Percent Change at Annual Rate，已经年化，不是未年化的月环比；"
                         "同比是 Percent Change from Year Ago。"
                         "2025 年 10 月官方 CPI 缺失，克利夫兰对该月使用插值，序列里的该月是其公布值。"),
                Chart("zori", "Zillow ZORI 新租约租金", "%", [
                    Line("同比", ts.pct_change(self.s("ZORI"), 12, "M")),
                    Line("环比", ts.pct_change(self.s("ZORI"), 1, "M"), "bar")],
                    note="Zillow Observed Rent Index，美国全国，平滑后的典型观察租金，按租赁存量加权。"
                         "日期对齐到月初，和 CPI 同一条月度轴。新租约领先 CPI 租金和业主等价租金大约 6–12 个月，"
                         "不是 CPI 住房本身。公开 CSV 还没更新的月份留空。"),
                Chart("expect", "通胀预期", "%", [
                    Line("5y5y 远期", fwd), Line("10 年盈亏平衡", be10), Line("密歇根一年期", mich)]),
            )),
        ]
        self._attach_cpi_forecasts()
        nc_rows = self._nowcast_rows("核心 PCE")
        metrics = [
            Metric("core_yoy", "核心 PCE 同比", core_yoy, "%", "{:.2f}", chart="core_contrib_yoy"),
            Metric("core_3m", "核心 PCE 3 个月年化", core_3m, "%", "{:.2f}", chart="core_pce_ann"),
            Metric("supercore", "超级核心 PCE 同比", supercore_yoy, "%", "{:.2f}", chart="supercore",
                   note="服务除能源、住房，与工资相关"),
            Metric("fwd", "5y5y 远期通胀预期", fwd, "%", "{:.2f}", "W", "expect"),
        ]
        if core_yoy and nc_rows and nc_rows[-1][0] > core_yoy[-1][0]:
            metrics.insert(1, Metric("nowcast", "核心 PCE 同比 Nowcast", nc_rows, "%", "{:.2f}", model=True,
                                     note="克利夫兰联储模型预测"))
        self.inputs["inflation"] = {
            "core_yoy": core_yoy[-1][1] if core_yoy else None, "core_3m": core_3m[-1][1] if core_3m else None,
            "supercore": supercore_yoy[-1][1] if supercore_yoy else None, "fwd": fwd[-1][1] if fwd else None,
            "nowcast": self.nowcast_after("核心 PCE", core_yoy[-1][0] if core_yoy else None),
            "core_yoy_period": f"{core_yoy[-1][0].month} 月" if core_yoy else None,
        }
        self.ctx.update({"core_idx": core, "ccpi_idx": ccpi})
        state = I.inflation_state(self.inputs["inflation"])
        return groups, metrics, state

    # =====================================================================
    # 流动性
    def reserves_frame(self) -> dict[str, Series]:
        """以美联储资产负债表的周三为基准，拆出准备金。

        准备金 = 总资产 − ON RRP − TGA − 流通中货币 − 其他负债与资本（倒挤）。
        「其他」把外国官方逆回购、存款机构以外的存款、资本等都算在里面。
        """
        assets = ts.scale(self.s("WALCL"), 1e-3)
        tga = ts.scale(self.s("WDTGAL"), 1e-3)
        reserves = ts.scale(self.s("WRBWFRBL"), 1e-3)
        rrp = ts.asof_align(assets, self.s("RRPONTSYD"), 4)
        cur = ts.asof_align(assets, ts.scale(self.s("WCURCIR"), 1e-3), 6)
        # RRP 早年没有日度数据时按 0 处理，避免整段缺失
        rrp_map = dict(rrp)
        rrp = [(d, rrp_map.get(d, 0.0)) for d, _ in assets]
        other = ts.combine(lambda a, r, t, c, res: a - r - t - c - res, assets, rrp, tga, cur, reserves)
        keep = {d for d, _ in other}
        f = lambda s: [(d, v) for d, v in s if d in keep]  # noqa: E731
        return {"assets": f(assets), "reserves": f(reserves), "rrp": f(rrp), "tga": f(tga),
                "currency": f(cur), "other": other}

    def liquidity(self):
        fr = self.reserves_frame()
        n = 4
        chg = {k: ts.diff_obs(v, n) for k, v in fr.items()}
        neg = lambda s: ts.scale(s, -1)  # noqa: E731
        iorb = ts.splice(self.s("IOER"), self.s("IORB"))
        sofr_iorb = ts.scale(ts.combine(lambda a, b: a - b, self.s("SOFR"), iorb), 100)
        effr_iorb = ts.scale(ts.combine(lambda a, b: a - b, self.s("EFFR"), iorb), 100)
        nfci = self.s("NFCI")
        hy = self.w("BAMLH0A0HYM2")
        ig = self.w("BAMLC0A0CM")
        usd = self.w("DTWEXBGS")

        groups = [
            ("准备金拆分", self.add(
                Chart("reserves_stack", "美联储负债结构：准备金从哪里来", "十亿美元", [
                    Line("准备金", fr["reserves"]), Line("ON RRP", fr["rrp"]), Line("TGA", fr["tga"]),
                    Line("流通中货币", fr["currency"]), Line("其他负债与资本", fr["other"])],
                    stacked="area", start=date(2008, 1, 1), core=True,
                    note="堆叠之和 = 美联储总资产。准备金 = 总资产 − ON RRP − TGA − 流通中货币 − 其他。"),
                Chart("reserves_chg", f"准备金 {n} 周变化：按来源拆分", "十亿美元", [
                    Line("总资产", chg["assets"], "bar"),
                    Line("ON RRP（取负）", neg(chg["rrp"]), "bar"),
                    Line("TGA（取负）", neg(chg["tga"]), "bar"),
                    Line("流通中货币（取负）", neg(chg["currency"]), "bar"),
                    Line("其他（取负）", neg(chg["other"]), "bar"),
                    Line("准备金变化", chg["reserves"])],
                    stacked="bar", start=date(2019, 1, 1), core=True,
                    note="柱子之和 = 准备金变化。正值表示该项在增加准备金，例如 TGA 下降、RRP 下降。"),
                Chart("reserves_lv", "准备金余额", "十亿美元", [Line("准备金（周三）", fr["reserves"])],
                      start=date(2008, 1, 1)),
            )),
            ("资金利率与金融状况", self.add(
                Chart("sofr_iorb", "SOFR − IORB 与 EFFR − IORB", "基点", [
                    Line("SOFR − IORB", ts.to_weekly(sofr_iorb)),
                    Line("EFFR − IORB", ts.to_weekly(effr_iorb))],
                    start=date(2018, 4, 1), core=True,
                    note="周内最后一个交易日。SOFR 持续高于 IORB 说明回购市场资金偏紧、准备金接近不充裕。"
                         "2021-07 前用 IOER。"),
                Chart("nfci", "芝加哥联储金融状况指数 NFCI", "指数", [Line("NFCI", nfci)],
                      note="0 为历史平均，正值偏紧、负值偏松。", core=True),
                Chart("hy", "信用利差", "%", [
                    Line("高收益 OAS", hy), Line("投资级 OAS", ig)],
                      note="ICE 美银期权调整利差，FRED 上只保留近几年。高收益是 BAMLH0A0HYM2，投资级是 BAMLC0A0CM。"),
                Chart("usd", "美元广义指数", "指数", [Line("美元指数", usd)], start=date(2006, 1, 1)),
            )),
        ]
        gdp = self.s("GDP")
        ratio = [(d, v / g * 100) for d, v in fr["reserves"] if (g := ts.asof(gdp, d, 200))]
        hy_d = self.s("BAMLH0A0HYM2")
        ig_d = self.s("BAMLC0A0CM")
        metrics = [
            Metric("sofr_iorb", "SOFR − IORB", sofr_iorb, "基点", "{:+.0f}", "D", "sofr_iorb"),
            Metric("reserves_ratio", "准备金占 GDP", ratio, "%", "{:.1f}", "W", "reserves_stack",
                   note=f"准备金 {fr['reserves'][-1][1]:,.0f} 十亿美元" if fr["reserves"] else ""),
            Metric("nfci", "金融状况 NFCI", nfci, "指数", "{:.2f}", "W", "nfci"),
            Metric("hy", "高收益债利差", hy_d, "%", "{:.2f}", "D", "hy"),
            Metric("ig", "投资级利差", ig_d, "%", "{:.2f}", "D", "hy"),
        ]
        state = I.liquidity_state({
            "sofr_iorb": sofr_iorb[-1][1] if sofr_iorb else None, "reserves_ratio": ratio[-1][1] if ratio else None,
            "nfci": nfci[-1][1] if nfci else None, "hy": hy_d[-1][1] if hy_d else None,
            "ig": ig_d[-1][1] if ig_d else None,
        })
        return groups, metrics, state

    # =====================================================================
    # 财政
    def fiscal(self):
        mts = self.m("MTSDS133FMS", "last")
        deficit12 = ts.scale(ts.rolling(mts, 12, "sum"), -1e-3)  # 正数 = 赤字，十亿美元
        gdp = self.s("GDP")
        deficit_pct = [(d, v / g * 100) for d, v in deficit12 if (g := ts.asof(gdp, d, 200))]
        # 财年数据 FRED 记在当年 1 月 1 日，挪到财年结束的 9 月 30 日，和月度线对齐
        fy_end = lambda s: [(date(d.year, 9, 30), v) for d, v in s]  # noqa: E731
        annual_def = fy_end(ts.scale(self.s("FYFSGDA188S"), -1))
        interest_pct = ts.combine(lambda a, b: a / b * 100 if b else None, self.s("A091RC1Q027SBEA"), gdp)
        interest_fy = fy_end(self.s("FYOIGDA188S"))
        debt = self.s("GFDEGDQ188S")
        deficit_chg = ts.diff(deficit_pct, 12, "M")

        groups = [
            ("赤字", self.add(
                Chart("deficit_pct", "赤字率", "% GDP", [
                    Line("滚动 12 个月 / 名义 GDP", deficit_pct), Line("财年赤字率", annual_def, "bar")],
                    note="正值为赤字。柱子是财年数据，画在财年结束的 9 月底。", core=True),
                Chart("deficit_usd", "联邦赤字：滚动 12 个月", "十亿美元", [Line("12 个月赤字", deficit12, "bar")],
                      note="正值为赤字。按月度财政报告（MTS）的收支差滚动加总。"),
            )),
            ("利息与债务", self.add(
                Chart("interest", "联邦利息支出占 GDP", "% GDP", [
                    Line("季度（年化）", interest_pct), Line("财年", interest_fy, "bar")], core=True),
                Chart("debt", "联邦债务占 GDP", "% GDP", [Line("债务/GDP", debt)]),
            )),
        ]
        metrics = [
            Metric("deficit", "赤字率（滚动 12 个月）", deficit_pct, "% GDP", "{:.1f}", chart="deficit_pct"),
            Metric("interest", "利息支出占 GDP", interest_pct, "% GDP", "{:.2f}", "Q", "interest"),
            Metric("debt", "联邦债务占 GDP", debt, "% GDP", "{:.0f}", "Q", "debt"),
        ]
        state = I.fiscal_state({
            "deficit": deficit_pct[-1][1] if deficit_pct else None,
            "deficit_chg12": deficit_chg[-1][1] if deficit_chg else None,
            "interest": interest_pct[-1][1] if interest_pct else None,
            "debt": debt[-1][1] if debt else None,
        })
        self.inputs["fiscal"] = {
            "deficit": deficit_pct[-1][1] if deficit_pct else None,
            "deficit_chg12": deficit_chg[-1][1] if deficit_chg else None,
        }
        return groups, metrics, state

    # =====================================================================
    # 货币政策
    def latest_expect(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for row in self.effr_expect:
            key = row["series_id"]
            if key not in latest or row["date"] > latest[key]["date"]:
                latest[key] = row
        return latest

    def _expect_pts(self, key: str) -> list[tuple[date, float]]:
        by: dict[date, float] = {}
        for r in self.effr_expect:
            if r.get("series_id") != key:
                continue
            try:
                by[date.fromisoformat(r["date"])] = float(r["value"])
            except (TypeError, ValueError):
                continue
        return sorted(by.items())

    def _expect_remark(self, key: str) -> str:
        rows = [r for r in self.effr_expect if r.get("series_id") == key and r.get("remark")]
        if not rows:
            return ""
        return max(rows, key=lambda r: r["date"]).get("remark") or ""

    def path_values(self, next_meet: float | None = None) -> dict:
        effr = self.s("EFFR")
        latest = self.latest_expect()
        get = lambda k: float(latest[k]["value"]) if k in latest else None  # noqa: E731
        ref_year = int(max(r["date"] for r in latest.values())[:4]) if latest else self.asof.year
        dmap = {d.year: v for d, v in self.s("FEDTARMD")}
        lr = self.s("FEDTARMDLR")
        if next_meet is None:
            next_meet = get("effr_next")
        return {"current": effr[-1][1] if effr else None, "next_meet": next_meet,
                "year_end": get("effr_year"), "next_year": get("effr_ny"),
                "dot_year": dmap.get(ref_year), "dot_next": dmap.get(ref_year + 1),
                "dot_long": lr[-1][1] if lr else None,
                "asof": max((r["date"] for r in latest.values()), default="")}

    def path_chart(self, pv: dict) -> Chart | None:
        cats = ["当前", "下次会议", "年底", "明年底", "长期"]
        market = [pv["current"], pv["next_meet"], pv["year_end"], pv["next_year"], None]
        dots = [pv["current"] if pv["dot_year"] is not None else None, None,
                pv["dot_year"], pv["dot_next"], pv["dot_long"]]
        lines = []
        if any(v is not None for v in market[1:]):
            lines.append(Line("市场隐含", market))  # type: ignore[arg-type]
        if any(v is not None for v in dots[2:]):
            lines.append(Line("点阵图中位数", dots, dash=True))  # type: ignore[arg-type]
        if not lines:
            return None
        return Chart("effr_path", "EFFR 路径：市场隐含 vs 点阵图", "%", lines, kind="path", categories=cats, core=True,
                     note=f"市场隐含来自早晨笔记（Investing Fed Rate Monitor，{pv['asof']}），"
                          "点阵图为最近一次 SEP 中位数。点阵图是区间中值，与 EFFR 通常相差几个基点。")

    def policy(self):
        effr_w = self.w("EFFR")
        upper = self.w("DFEDTARU")
        lower = self.w("DFEDTARL")
        dgs2 = self.w("DGS2")
        effr_m = self.m("EFFR")
        core_yoy = ts.pct_change(self.m("PCEPILFE", "last"), 12, "M")
        real_policy = ts.combine(lambda a, b: a - b, effr_m, core_yoy)
        two_minus = ts.scale(ts.combine(lambda a, b: a - b, self.m("DGS2"), effr_m), 100)
        t10y2y = self.w("T10Y2Y")
        t10y3m = self.w("T10Y3M")
        dgs10 = self.w("DGS10")
        real10 = self.w("DFII10")

        meet_s = self._expect_pts("effr_next")
        year_s, ny_s = self._expect_pts("effr_year"), self._expect_pts("effr_ny")
        exp_lines = [
            Line("下次会议", meet_s),
            Line("年底 EFFR", year_s),
            Line("明年底 EFFR", ny_s),
        ]
        pv = self.path_values(meet_s[-1][1] if meet_s else None)
        path = self.path_chart(pv)
        groups = [
            ("政策利率", self.add(
                *([path] if path else []),
                Chart("effr", "EFFR、目标区间与 2 年期国债", "%", [
                    Line("目标上限", upper, dash=True), Line("目标下限", lower, dash=True),
                    Line("EFFR", effr_w), Line("2 年期国债", dgs2)],
                    note="2 年期收益率反映市场对未来两年政策利率的平均预期。", core=True),
                Chart("real_policy", "实际政策利率", "%", [Line("EFFR − 核心 PCE 同比", real_policy)],
                      note="正值越大，政策越紧。中性实际利率一般估计在 0.5–1%。", core=True),
                Chart("effr_expect_hist", "市场隐含 EFFR 的变化", "%", exp_lines,
                      start=date(2026, 1, 1), note="早晨笔记从 2026-09 开始记录，历史会逐日累积。"),
                Chart("two_minus", "2 年期国债 − EFFR", "基点", [Line("2Y − EFFR", two_minus)],
                      note="为正说明市场预期加息，为负说明预期降息。"),
            )),
            ("收益率曲线", self.add(
                Chart("curve", "收益率曲线利差", "百分点", [
                    Line("10Y − 2Y", t10y2y), Line("10Y − 3M", t10y3m)],
                    note="倒挂（小于 0）历史上领先衰退，衰退往往在重新转正后到来。", core=True),
                Chart("yields", "10 年期名义与实际利率", "%", [
                    Line("10 年期国债", dgs10), Line("10 年期 TIPS 实际利率", real10)]),
            )),
        ]
        metrics = [
            Metric("real_policy", "实际政策利率", real_policy, "%", "{:.2f}", chart="real_policy",
                   note="EFFR − 核心 PCE 同比"),
            Metric("meeting", "市场隐含下次会议 EFFR", exp_lines[0].data, "%", "{:.3f}", "O", "effr_path"),
            Metric("year_end", "市场隐含年底 EFFR", exp_lines[1].data, "%", "{:.3f}", "O", "effr_path"),
            Metric("next_year", "市场隐含明年底 EFFR", exp_lines[2].data, "%", "{:.3f}", "O", "effr_path"),
        ]
        state = I.policy_state({**pv, "real_policy": real_policy[-1][1] if real_policy else None})
        self.inputs["policy"] = {"current": pv.get("current")}
        meet_lvl, meet_why = I.tenor_signals(meet_s)
        year_lvl, year_why = I.tenor_signals(year_s)
        ny_lvl, ny_why = I.tenor_signals(ny_s)
        _, cross_why = I.cross_signal(meet_s, year_s)
        meet_remark = self._expect_remark("effr_next")
        diverge = cross_why[0] if cross_why else ""
        state["anchors"]["meeting"] = I.implied_anchor(meet_remark, meet_lvl, meet_why, diverge)
        state["anchors"]["year_end"] = I.implied_anchor(
            self._expect_remark("effr_year"), year_lvl, year_why, diverge)
        state["anchors"]["next_year"] = I.implied_anchor(self._expect_remark("effr_ny"), ny_lvl, ny_why)
        return groups, metrics, state

    # =====================================================================
    # 克利夫兰联储 Nowcast
    @staticmethod
    def _period(text: str) -> date | None:
        try:
            y, m = text.split("-")[:2]
            return date(int(y), int(m), 1)
        except (ValueError, AttributeError):
            return None

    def _attach_cpi_forecasts(self) -> None:
        """同比、环比的数据表加预期列。只填日历里已经有的月份，其余留空。"""
        specs = {
            "cpi": {"CPI y/y": "CPI", "Core CPI y/y": "核心 CPI"},
            "cpi_mom": {"CPI m/m": "CPI 环比", "Core CPI m/m": "核心 CPI 环比"},
        }
        for cid, mapping in specs.items():
            chart = self.charts.get(cid)
            columns = self._forecast_columns(mapping) if chart else []
            if chart and columns:
                chart["forecasts"] = columns

    def _forecast_columns(self, mapping: dict[str, str]) -> list[dict]:
        buckets: dict[str, dict[date, float]] = {}
        for event in self.events:
            title = event.get("title", "")
            series_name = mapping.get(title)
            spec = cons.EVENTS.get(title)
            if not series_name or spec is None:
                continue
            value = cons.parse_value(event.get("forecast") or "")
            if value is None:
                continue
            try:
                ref = cons.ref_period(spec, cons.release_date(event["release_at"]))
            except (KeyError, ValueError):
                continue
            buckets.setdefault(series_name, {})[ref] = value
        out = []
        for series_name in dict.fromkeys(mapping.values()):
            pts = buckets.get(series_name)
            if not pts:
                continue
            out.append({"name": f"{series_name} 预期", "for": series_name,
                        "data": [[d.isoformat(), round(v, 4)] for d, v in sorted(pts.items())]})
        return out

    def nowcast_after(self, measure: str, latest: date | None) -> list[tuple[str, float]]:
        """比最新官方数据更新的各期 Nowcast（同比），按期间排序。"""
        return [(f"{p.month} 月", v) for p, v in self._nowcast_rows(measure) if not latest or p > latest]

    # =====================================================================
    # 预期与意外
    def _find(self, key: str, ref: date) -> float | None:
        s = self.actuals.get(key) or []
        return dict(s).get(ref)

    @staticmethod
    def _decimals(text: str) -> int:
        m = text.strip().rstrip("%KMBkmb").split(".")
        return len(m[1]) if len(m) > 1 else 0

    def _ref_text(self, spec: cons.EventSpec, ref: date) -> str:
        kind = spec.ref[0]
        if kind == "Q":
            return f"{ref.year}Q{(ref.month - 1) // 3 + 1}"
        if kind == "M":
            return f"{ref.year}-{ref.month:02d}"
        return f"截至 {ref.isoformat()} 当周"

    def _prev_obs(self, key: str, ref: date) -> tuple[date, float] | None:
        prev = [(d, v) for d, v in (self.actuals.get(key) or []) if d < ref]
        return prev[-1] if prev else None

    @staticmethod
    def _wan_ge(thousands: float) -> str:
        """千个换成万个：256 → 25.6 万个。"""
        return f"{abs(thousands) / 10:.1f} 万个"

    def _regime_line(self, in_rules: bool) -> str:
        """这条数据落下来之后，增长标签和整体环境还在不在原处。in_rules 表示它进了试算。"""
        g = self.states.get("growth") or {}
        env = I.environment(g, self.states.get("inflation") or {}, self.states.get("policy") or {},
                            self.states.get("liquidity") or {}, self.states.get("fiscal"))
        frozen = f"增长仍为「{g.get('label') or '—'}」，整体环境仍为「{env.get('name') or '—'}」"
        if in_rules:
            return f"没有改变判断：{frozen}"
        return f"这条不进本站标签，{frozen}，判断没有变化"

    def _jolts_note(self, ref: date, actual: float, forecast: float | None, forecast_text: str,
                    previous_text: str) -> tuple[str, str, str]:
        """职位空缺：写出修订、较上月、较预期，以及空缺/失业和离职率。它不进增长标签。"""
        prev = self._prev_obs("jolts", ref)
        bits = [f"{ref.year} 年 {ref.month} 月 {actual / 1000:.3f} 百万个"]
        if prev:
            delta = actual - prev[1]
            way = "少" if delta < 0 else "多" if delta > 0 else "持平于"
            bits.append("比修订后的上月持平" if delta == 0 else f"比修订后的上月{way} {self._wan_ge(delta)}")
        if forecast is not None:
            gap = actual - forecast
            if gap == 0:
                bits.append(f"符合预期 {forecast_text or ''}".strip())
            else:
                bits.append(f"{'低于' if gap < 0 else '高于'}预期 {forecast_text}（{'少' if gap < 0 else '多'} {self._wan_ge(gap)}）")
        lower = [d for d, v in (self.actuals.get("jolts") or []) if d < ref and v < actual]
        if lower:
            last = lower[-1]
            months = (ref.year - last.year) * 12 + (ref.month - last.month)
            if 0 < months <= 24:
                bits.append(f"要回到 {last.year} 年 {last.month} 月才有更低的读数")
        published = cons.parse_value(previous_text)
        if prev and published is not None and abs(prev[1] - published) >= 50:
            bits.append(f"日历前值 {previous_text} 是修订前的上月，修订后为 {prev[1] / 1000:.3f} 百万个")
        move = "，".join(bits) + "。"

        unemp = dict(self.m("UNEMPLOY", "last"))
        ratio = actual / unemp[ref] if unemp.get(ref) else None
        ratio_prev = prev[1] / unemp[prev[0]] if prev and unemp.get(prev[0]) else None
        parts = []
        if ratio is not None:
            if ratio > 1.02:
                side = "职位仍多于求职者"
            elif ratio > 1:
                side = "职位仍略多于求职者"
            elif ratio < 0.98:
                side = "求职者已经多于职位"
            else:
                side = "职位和求职者大致相当"
            if ratio_prev is None or abs(ratio - ratio_prev) < 0.005:
                parts.append(f"空缺/失业 {ratio:.2f}，{side}")
            else:
                verb = "降到" if ratio < ratio_prev else "升到"
                parts.append(f"空缺/失业从 {ratio_prev:.2f} {verb} {ratio:.2f}，{side}")
        quits = dict(self.m("JTSQUR", "last"))
        q, q_prev = quits.get(ref), quits.get(prev[0]) if prev else None
        if q is not None:
            if q_prev is None or abs(q - q_prev) < 0.05:
                parts.append(f"离职率仍为 {q:.1f}%")
            else:
                parts.append(f"离职率从 {q_prev:.1f}% 到 {q:.1f}%")
        parts.append(self._regime_line(False))
        labor = (self.states.get("growth") or {}).get("labor")
        if prev and labor and labor != "数据不足":
            down = actual < prev[1]
            cooling = labor in ("降温", "疲弱", "恶化")
            if (down and cooling) or ((not down) and labor == "强"):
                parts.append(f"变动方向和现在的「就业{labor}」一致，用来确认，不单独改标签")
            else:
                parts.append(f"变动方向和现在的「就业{labor}」不完全同向，但仍不单独改标签")
        return f"{actual / 1000:.3f} 百万个", move, "。".join(parts) + "。"

    def release_note(self, spec: cons.EventSpec, ref: date, actual: float | None, forecast: float | None,
                     forecast_text: str, previous_text: str, impact: dict | None,
                     verdict: str | None) -> dict:
        """给「昨日发布」用：变动一句，意义一句（标签变了没有）。"""
        if actual is None:
            return {"move": "实际值还没进 FRED。", "meaning": "数据未到，先不改判断。"}
        if spec.key == "jolts":
            level, move, meaning = self._jolts_note(ref, actual, forecast, forecast_text, previous_text)
            return {"level_text": level, "move": move, "meaning": meaning}
        bits = []
        prev = self._prev_obs(spec.key, ref)
        if prev:
            delta = actual - prev[1]
            if delta == 0:
                bits.append("与上期相同")
            else:
                bits.append(f"较上期 {'+' if delta > 0 else '−'}{abs(delta):,.2f} {spec.unit}")
        if verdict:
            bits.append(verdict)
        if impact and impact.get("changed"):
            meaning = impact["text"] + "。判断因此变化。"
        else:
            meaning = self._regime_line(impact is not None)
        return {"move": "，".join(bits) + ("。" if bits else ""), "meaning": meaning}

    def judge(self, spec: cons.EventSpec, actual: float, forecast: float, decimals: int) -> tuple[float, str, str]:
        """返回 (意外, 文字, 方向 pos/neg/flat)。pos 表示对经济偏强或通胀偏热。"""
        a = round(actual, decimals)
        diff = round(a - forecast, decimals)
        if diff == 0:
            return 0.0, "符合预期", "flat"
        higher = diff > 0
        if spec.up == "强":
            return diff, "好于预期" if higher else "不及预期", "pos" if higher else "neg"
        if spec.up == "弱":  # 失业率、初请：高于预期是坏消息
            return diff, "差于预期" if higher else "好于预期", "neg" if higher else "pos"
        return diff, "高于预期（偏热）" if higher else "低于预期（偏冷）", "pos" if higher else "neg"

    def releases(self) -> dict:
        now = self.now
        recent, upcoming = [], []
        for e in self.events:
            try:
                at = datetime.fromisoformat(e["release_at"])
            except (KeyError, ValueError):
                continue
            title = e.get("title", "")
            spec = cons.EVENTS.get(title)
            name = spec.name if spec else cons.WATCH_ONLY.get(title)
            f_text, p_text = e.get("forecast", ""), e.get("previous", "")
            row = {"title": name or title, "title_en": title, "release_at": e["release_at"],
                   "bj": cons.to_beijing(e["release_at"]), "impact": e.get("impact", ""),
                   "forecast_text": f_text, "previous_text": p_text, "dim": cons.dim_of(title),
                   "key": spec.key if spec else None, "bj_date": at.astimezone(cons.BJ).date().isoformat(),
                   "importance": importance.rate(title)}
            shown = spec or name or e.get("impact") == "High"
            # 北京时间今天已经发布的也放进日历，页面上「今日发布」按访问时的北京日期挑出来
            today = at <= now and row["bj_date"] == now.astimezone(cons.BJ).date().isoformat()
            if shown and today:
                upcoming.append({**row, "done": True})
            if at > now:
                if at <= now + timedelta(days=UPCOMING_DAYS) and shown:
                    if spec:
                        ref = cons.ref_period(spec, at.date())
                        row["ref"] = self._ref_text(spec, ref)
                        sc = self.scen.scenarios(spec.key, ref, cons.parse_value(f_text), f_text)
                        if sc:
                            row["scenario"] = sc
                    upcoming.append(row)
                continue
            if not spec or at < now - timedelta(days=RECENT_DAYS):
                continue
            ref = cons.ref_period(spec, at.date())
            row["ref"] = self._ref_text(spec, ref)
            row["unit"] = spec.unit
            actual = self._find(spec.key, ref)
            forecast = cons.parse_value(f_text)
            verdict = None
            if actual is None:
                row["status"] = "等待 FRED 更新"
            else:
                dec = self._decimals(f_text) if f_text else 1
                row["actual_text"] = f"{round(actual, dec):,.{dec}f}"
                if forecast is not None:
                    diff, verdict, direction = self.judge(spec, actual, forecast, dec)
                    surprise_text = f"{diff:+,.{dec}f}"
                    if spec.key == "jolts":
                        surprise_text = f"{diff / 1000:+.3f}M"
                    row.update({"surprise": diff, "surprise_text": surprise_text, "verdict": verdict,
                                "dir": direction})
                if spec.key == "jolts":
                    row["actual_text"] = f"{actual / 1000:.3f}M"
                imp = self.scen.impact(spec.key, ref, actual)
                if imp is None and spec.key == "jolts":
                    imp = {"changed": False, "text": "没有改变判断"}
                if imp:
                    row["impact"] = imp
            judged = row.get("impact") if isinstance(row.get("impact"), dict) else None
            row.update(self.release_note(spec, ref, actual, forecast, f_text, p_text, judged, verdict))
            row["market"] = self.market_reaction(at.date())
            recent.append(row)
        recent.sort(key=lambda r: r["release_at"], reverse=True)
        upcoming.sort(key=lambda r: r["release_at"])
        # 克利夫兰联储：每个口径取最近两期
        nc = {}
        for r in self.nowcast:
            nc.setdefault(r["measure"], []).append(r)
        nowcast = []
        for measure in ("CPI", "核心 CPI", "PCE", "核心 PCE"):
            for r in nc.get(measure, [])[-2:]:
                nowcast.append({"measure": measure, "period": r["period"], "nowcast": r.get("nowcast", ""),
                                "actual": r.get("actual", "")})
        return {"recent": recent, "upcoming": upcoming, "nowcast": nowcast, "importance_rule": importance.RULE}

    def market_reaction(self, day: date) -> str:
        """发布当天 2 年期国债的日变化，以及早晨笔记里年底 EFFR 隐含值在发布前后的变化。"""
        bits = []
        y2 = self.s("DGS2")
        on = [v for d, v in y2 if d == day]
        before = [v for d, v in y2 if d < day]
        if on and before:
            bits.append(f"2Y {(on[0] - before[-1]) * 100:+.0f}bp")
        ye = sorted((r["date"], float(r["value"])) for r in self.effr_expect if r.get("series_id") == "effr_year")
        pre = [v for d, v in ye if d <= day.isoformat()]
        post = [v for d, v in ye if d > day.isoformat()]
        if pre and post:
            bits.append(f"年底 EFFR 隐含 {(post[0] - pre[-1]) * 100:+.0f}bp")
        return "；".join(bits)

    def _nowcast_rows(self, measure: str) -> list[tuple[date, float]]:
        out = []
        for r in self.nowcast:
            p = self._period(r.get("period", ""))
            if r.get("measure") == measure and p and r.get("nowcast") not in ("", None):
                out.append((p, float(r["nowcast"])))
        return sorted(out)

    # =====================================================================
    def metric_json(self, m: Metric, anchors: dict[str, str], extras: dict | None = None) -> dict | None:
        js = metric_json(m, anchors, self.charts)
        if not js:
            return None
        n, _ = FREQ[m.freq]
        value = m.data[-1][1]
        delta = value - m.data[-1 - n][1] if len(m.data) > n else None
        move = I.change_move(m.id, delta)
        if move:
            js["move"] = move
        st = I.level_status(m.id, value, extras)
        if st:
            js["status"] = st
        return js

    def _metric_extras(self, key: str) -> dict:
        """状态列需要的额外上下文（跨指标对照）。"""
        g = self.inputs.get("growth") or {}
        i = self.inputs.get("inflation") or {}
        f = self.inputs.get("fiscal") or {}
        p = self.inputs.get("policy") or {}
        out = {
            "unrate_chg12": g.get("unrate_chg12"), "sahm": g.get("sahm"),
            "core_yoy": i.get("core_yoy"), "deficit_chg12": f.get("deficit_chg12"),
            "current": p.get("current"),
        }
        if key == "inflation" and i.get("nowcast"):
            # nowcast 相对最新官方同比的差，给状态列用
            yoy = i.get("core_yoy")
            if yoy is not None:
                out["nowcast_gap"] = i["nowcast"][-1][1] - yoy
        return out

    def build(self) -> dict:
        dims = [("growth", "增长", self.growth), ("inflation", "通胀", self.inflation),
                ("liquidity", "流动性", self.liquidity), ("fiscal", "财政", self.fiscal),
                ("policy", "货币", self.policy)]
        built = [(key, name, *fn()) for key, name, fn in dims]
        self.states = {key: st for key, _, _, _, st in built}
        self.scen = Scenario(self)
        releases = self.releases()  # 需要 actuals 和各维度状态，放在各维度之后

        out_dims, sections = [], []
        for key, name, groups, metrics, state in built:
            extras = self._metric_extras(key)
            out_dims.append({
                "key": key, "name": name, "label": state["label"], "why": state.get("why", []),
                "head": state["head"],
                "metrics": [x for x in (self.metric_json(m, state.get("anchors", {}), extras) for m in metrics) if x],
            })
            sections.append({"key": key, "name": name,
                             "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})

        st = self.states
        env = I.environment(st["growth"], st["inflation"], st["policy"], st["liquidity"], st["fiscal"])
        return {
            "asof": self.asof.isoformat(),
            "method": "每个维度按经济锚点判断，只用已经公布的数据定状态：增长的产出看上季核心 GDP（消费 + 固定投资）对约 2% 的潜在增速"
                      "（GDPNow 是模型预测，只作参考），就业看非农 3 个月均值、失业率和 Sahm；"
                      "通胀看核心 PCE 对 2% 目标和 3 个月年化的短期动能，并看超级核心和 5y5y；"
                      "克利夫兰联储 Nowcast 是模型预测，不改标签，只有和官方值差超过 0.3 个百分点才写进理由；"
                      "流动性分资金市场、金融条件、信用三块；财政分财政脉冲与偿债压力；货币分当前立场与市场路径。"
                      "整体环境的名字由增长 × 通胀的组合决定。阈值都写在 pipeline/macro/interpret.py。",
            "verdict": {"name": env["name"], "headline": env["head"], "lines": env["lines"]},
            "dimensions": out_dims,
            "releases": releases,
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(raw: dict[str, Series], effr_expect: list[dict] | None = None, asof: date | None = None,
                    events: list[dict] | None = None, nowcast: list[dict] | None = None,
                    now: datetime | None = None) -> dict:
    return MacroBuilder(raw, effr_expect, asof, events, nowcast, now).build()
