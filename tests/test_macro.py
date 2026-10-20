"""宏观板块的计算测试。只用标准库：python3 -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import fred, series as ts  # noqa: E402
from pipeline.macro import consensus as cons, cpi_contrib, dxy, effr_expect, interpret as I, treasury, zori  # noqa: E402
from pipeline.macro.build import MacroBuilder, build_dashboard  # noqa: E402


def months(start: date, n: int) -> list[date]:
    out, d = [], start
    for _ in range(n):
        out.append(d)
        d = ts._shift_month(d, 1)
    return out


class SeriesTest(unittest.TestCase):
    def test_pct_change_uses_calendar_not_neighbour(self):
        s = [(date(2024, 1, 1), 100.0), (date(2024, 3, 1), 110.0)]  # 缺 2 月
        self.assertEqual(ts.pct_change(s, 1, "M"), [])
        s.append((date(2024, 4, 1), 121.0))
        self.assertAlmostEqual(ts.pct_change(s, 1, "M")[0][1], 10.0)

    def test_annualized_quarterly(self):
        s = [(date(2024, 1, 1), 100.0), (date(2024, 4, 1), 101.0)]
        v = ts.pct_change(s, 1, "Q", annualize=4)[0][1]
        self.assertAlmostEqual(v, (1.01 ** 4 - 1) * 100)

    def test_rolling_sum_requires_full_window(self):
        s = [(d, 1.0) for d in months(date(2020, 1, 1), 13)]
        out = ts.rolling(s, 12, "sum")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], (date(2020, 12, 1), 12.0))

    def test_to_weekly_keeps_last_obs(self):
        s = [(date(2024, 1, 1), 1.0), (date(2024, 1, 5), 2.0), (date(2024, 1, 8), 3.0)]
        self.assertEqual(ts.to_weekly(s), [(date(2024, 1, 5), 2.0), (date(2024, 1, 8), 3.0)])

    def test_asof_gap(self):
        s = [(date(2024, 1, 1), 1.0)]
        self.assertEqual(ts.asof(s, date(2024, 1, 20)), 1.0)
        self.assertIsNone(ts.asof(s, date(2024, 6, 1), 60))

    def test_percentile(self):
        self.assertEqual(ts.percentile_rank([1, 2, 3, 4], 4), 87.5)

    def test_fred_csv_parse(self):
        text = "observation_date,UNRATE\n2024-01-01,3.7\n2024-02-01,.\n2024-03-01,3.9\n"
        self.assertEqual(fred.parse_csv(text), [(date(2024, 1, 1), 3.7), (date(2024, 3, 1), 3.9)])


class PceBreakdownTest(unittest.TestCase):
    """构造一组可加的分项，检验各项贡献之和接近总体与核心的变化。"""

    def setUp(self):
        ms = months(date(2019, 1, 1), 42)
        # 名义支出与价格月增速：食品、能源商品、能源服务、核心商品、住房、超级核心
        comp = {"food": (100.0, 0.002), "egoods": (40.0, 0.006), "eserv": (20.0, -0.003),
                "cgoods": (250.0, 0.0005), "housing": (200.0, 0.004), "super": (390.0, 0.003)}
        price = {k: [] for k in comp}
        nominal = {k: [] for k in comp}
        for i, d in enumerate(ms):
            for k, (n0, g) in comp.items():
                p = (1 + g) ** i
                price[k].append((d, p * 100))
                nominal[k].append((d, n0 * p))

        def add(*keys):
            return [(d, sum(nominal[k][i][1] for k in keys)) for i, d in enumerate(ms)]

        def price_of(*keys):
            out, level = [], 100.0
            for i, d in enumerate(ms):
                if i:
                    tot = sum(nominal[k][i - 1][1] for k in keys)
                    level *= 1 + sum(nominal[k][i - 1][1] / tot * (price[k][i][1] / price[k][i - 1][1] - 1)
                                     for k in keys)
                out.append((d, level))
            return out

        def annual(keys, freq_q=False):
            out = []
            for y in (2019, 2020, 2021):
                vals = [v for d, v in add(*keys) if d.year == y]
                out.append((date(y, 1, 1), sum(vals) / len(vals)))
            return out

        def quarterly(*keys):
            out = []
            series = add(*keys)
            for d, v in series:
                if d.month in (1, 4, 7, 10):
                    q = [x for dd, x in series if dd.year == d.year and d.month <= dd.month < d.month + 3]
                    out.append((d, sum(q) / len(q)))
            return out

        allk = tuple(comp)
        core = ("cgoods", "housing", "super")
        self.raw = {
            "PCE": add(*allk), "PCEPI": price_of(*allk),
            "DFXARG3M086SBEA": price["food"], "DFXARC1M027SBEA": nominal["food"],
            "DNRGRG3M086SBEA": price_of("egoods", "eserv"), "DNRGRC1M027SBEA": add("egoods", "eserv"),
            "PCEPILFE": price_of(*core), "DPCCRC1M027SBEA": add(*core),
            "IA001176M": price_of("cgoods", "super"), "IA001260M": price["super"],
            "DHSGRC1A027NBEA": annual(("housing",)),
            "DGDSRC1": add("food", "egoods", "cgoods"),
            "DGOERC1Q027SBEA": quarterly("egoods"), "DNRGRC1Q027SBEA": quarterly("egoods", "eserv"),
        }

    def check_sum(self, lines, headline, delta):
        maps = [dict(ln.data) for ln in lines]
        common = set.intersection(*(set(m) for m in maps))
        self.assertGreater(len(common), 12)
        for d in common:
            self.assertAlmostEqual(sum(m[d] for m in maps), headline[d], delta=delta)
        return maps

    def test_total_and_core_sum(self):
        br = MacroBuilder(self.raw, asof=date(2022, 7, 1)).pce_breakdown()
        self.assertEqual([ln.name for ln in br["total_mom"]], ["食品", "能源", "核心商品", "住房", "超级核心"])
        self.assertEqual([ln.name for ln in br["core_mom"]], ["核心商品", "住房", "超级核心"])
        # 住房比重用上一年年度值、能源商品比重用季度均值，与当月真实比重略有差别
        self.check_sum(br["total_mom"], dict(ts.pct_change(self.raw["PCEPI"], 1, "M")), 0.003)
        maps = self.check_sum(br["core_mom"], dict(ts.pct_change(self.raw["PCEPILFE"], 1, "M")), 0.003)
        # 超级核心贡献 ≈ 占核心份额 × 0.3%（份额由近似权重倒算，允许少量误差）
        d = date(2021, 6, 1)
        share = 390 * 1.003 ** 28 / dict(self.raw["DPCCRC1M027SBEA"])[date(2021, 5, 1)]
        self.assertAlmostEqual(maps[2][d], share * 0.3, delta=0.003)
        self.check_sum(br["core_yoy"], dict(ts.pct_change(self.raw["PCEPILFE"], 12, "M")), 0.05)

    def test_fallbacks(self):
        raw = dict(self.raw)
        raw.pop("DGOERC1Q027SBEA")
        br = MacroBuilder(raw).pce_breakdown()
        self.assertEqual([ln.name for ln in br["core_mom"]], ["住房", "核心除住房"])
        raw.pop("DHSGRC1A027NBEA")
        br = MacroBuilder(raw).pce_breakdown()
        self.assertEqual([ln.name for ln in br["total_mom"]], ["食品", "能源", "核心"])


class ConsensusTest(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(cons.parse_value("150K"), 150)
        self.assertEqual(cons.parse_value("4.2%"), 4.2)
        self.assertEqual(cons.parse_value("-0.3%"), -0.3)
        self.assertEqual(cons.parse_value("7.25M"), 7250)
        self.assertIsNone(cons.parse_value(""))

    def test_ref_period(self):
        E = cons.EVENTS
        self.assertEqual(cons.ref_period(E["Non-Farm Employment Change"], date(2026, 10, 2)), date(2026, 9, 1))
        self.assertEqual(cons.ref_period(E["Core PCE Price Index m/m"], date(2026, 9, 26)), date(2026, 8, 1))
        self.assertEqual(cons.ref_period(E["JOLTS Job Openings"], date(2026, 9, 30)), date(2026, 8, 1))
        self.assertEqual(cons.ref_period(E["Final GDP q/q"], date(2026, 12, 22)), date(2026, 7, 1))
        self.assertEqual(cons.ref_period(E["Final GDP q/q"], date(2026, 9, 30)), date(2026, 4, 1))
        self.assertEqual(cons.ref_period(E["Advance GDP q/q"], date(2026, 10, 29)), date(2026, 7, 1))
        self.assertEqual(cons.ref_period(E["Unemployment Claims"], date(2026, 10, 1)), date(2026, 9, 26))

    def test_merge_keeps_prerelease_forecast(self):
        from datetime import datetime, timezone
        ev = {"release_at": "2026-10-02T08:30:00-04:00", "title": "Non-Farm Employment Change",
              "impact": "High", "forecast": "150K", "previous": "142K"}
        before = datetime(2026, 10, 1, tzinfo=timezone.utc)
        after = datetime(2026, 10, 3, tzinfo=timezone.utc)
        rows = cons.merge_events([], [ev], before)
        rows = cons.merge_events(rows, [{**ev, "forecast": "155K"}], before)
        self.assertEqual(rows[0]["forecast"], "155K")
        rows = cons.merge_events(rows, [{**ev, "forecast": "999K"}], after)
        self.assertEqual(rows[0]["forecast"], "155K")

    def test_release_vs_actual(self):
        from datetime import datetime, timezone
        raw = {"PAYEMS": [(date(2026, 8, 1), 1000.0), (date(2026, 9, 1), 1180.0)],
               "UNRATE": [(date(2026, 9, 1), 4.3)]}
        events = [
            {"release_at": "2026-10-02T08:30:00-04:00", "title": "Non-Farm Employment Change",
             "impact": "High", "forecast": "150K", "previous": "142K"},
            {"release_at": "2026-10-02T08:30:00-04:00", "title": "Unemployment Rate",
             "impact": "High", "forecast": "4.2%", "previous": "4.1%"},
            {"release_at": "2026-10-06T10:00:00-04:00", "title": "ISM Services PMI",
             "impact": "High", "forecast": "51.0", "previous": "50.5"},
        ]
        dash = build_dashboard(raw, [], date(2026, 10, 3), events, [], datetime(2026, 10, 3, tzinfo=timezone.utc))
        rec = {r["title_en"]: r for r in dash["releases"]["recent"]}
        self.assertEqual(rec["Non-Farm Employment Change"]["actual_text"], "180")
        self.assertEqual(rec["Non-Farm Employment Change"]["verdict"], "好于预期")
        self.assertEqual(rec["Unemployment Rate"]["verdict"], "差于预期")
        self.assertEqual([r["title"] for r in dash["releases"]["upcoming"]], ["ISM 服务业 PMI"])
        self.assertEqual(rec["Non-Farm Employment Change"]["surprise_text"], "+30")

    def test_jolts_reading(self):
        from datetime import datetime, timezone
        raw = {
            "JTSJOL": [(date(2026, 3, 1), 6887.0), (date(2026, 7, 1), 7335.0), (date(2026, 8, 1), 7079.0)],
            "JTSQUR": [(date(2026, 7, 1), 1.9), (date(2026, 8, 1), 1.9)],
            "UNEMPLOY": [(date(2026, 7, 1), 6916.0), (date(2026, 8, 1), 7031.0)],
        }
        events = [{"release_at": "2026-09-29T10:00:00-04:00", "title": "JOLTS Job Openings",
                   "impact": "Medium", "forecast": "7.23M", "previous": "7.27M"}]
        now = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
        dash = build_dashboard(raw, [], date(2026, 9, 30), events, [], now)
        row = {r["title_en"]: r for r in dash["releases"]["recent"]}["JOLTS Job Openings"]
        self.assertEqual(row["bj_date"], "2026-09-29")
        self.assertEqual(row["ref"], "2026-08")
        self.assertEqual(row["verdict"], "不及预期")
        self.assertEqual(row["level_text"], "7.079 百万个")
        self.assertEqual(row["actual_text"], "7.079M")
        self.assertEqual(row["surprise_text"], "-0.151M")
        self.assertIn("少 25.6 万个", row["move"])
        self.assertIn("少 15.1 万个", row["move"])
        self.assertIn("7.335 百万个", row["move"])
        self.assertIn("2026 年 3 月", row["move"])
        self.assertIn("空缺/失业从 1.06 降到 1.01", row["meaning"])
        self.assertIn("离职率仍为 1.9%", row["meaning"])
        self.assertIn("不进本站标签", row["meaning"])
        self.assertIn("判断没有变化", row["meaning"])
        self.assertFalse(row["impact"]["changed"])

    def test_upcoming_and_nowcast(self):
        from datetime import datetime, timezone
        raw = {"PCEPILFE": [(d, 100 + i * 0.25) for i, d in enumerate(months(date(2024, 1, 1), 31))]}
        events = [{"release_at": "2026-09-30T08:30:00-04:00", "title": "Core PCE Price Index m/m",
                   "impact": "High", "forecast": "0.3%", "previous": "0.2%"}]
        nowcast = [{"period": "2026-8", "measure": "核心 PCE", "nowcast": "3.40", "actual": ""},
                   {"period": "2026-9", "measure": "核心 PCE", "nowcast": "3.49", "actual": ""}]
        dash = build_dashboard(raw, [], date(2026, 9, 28), events, nowcast, datetime(2026, 9, 28, tzinfo=timezone.utc))
        up = dash["releases"]["upcoming"][0]
        self.assertEqual((up["dim"], up["ref"]), ("inflation", "2026-08"))
        # Nowcast 只放在通胀那一块：不在即将发布里重复
        self.assertNotIn("nowcast_text", up)
        self.assertNotIn("nowcast", {x["id"] for x in dash["dimensions"][1]["metrics"]})
        rows = [r for r in dash["releases"]["nowcast"] if r["measure"] == "核心 PCE"]
        self.assertEqual([(r["period"], r["nowcast"]) for r in rows], [("2026-8", "3.40"), ("2026-9", "3.49")])


class ScenarioTest(unittest.TestCase):
    """情景门槛要和规则一致：非农 3 个月均值跨过 50 / 150 千人时判断改变。"""

    def setUp(self):
        from datetime import datetime, timezone
        ms = months(date(2024, 1, 1), 32)  # 到 2026-08
        pay, level = [], 1000.0
        for i, d in enumerate(ms):
            level += {len(ms) - 2: 21, len(ms) - 1: 162}.get(i, 30)
            pay.append((d, level))
        self.raw = {"PAYEMS": pay, "UNRATE": [(d, 4.1) for d in ms], "GDPC1": [], "GDPNOW": [(date(2026, 7, 1), 5.0)],
                    # 核心 PCE 比非农晚一个月：8 月的还没公布
                    "PCEPILFE": [(d, 100 * 1.0028 ** i) for i, d in enumerate(ms[:-1])]}
        self.events = [
            {"release_at": "2026-10-02T08:30:00-04:00", "title": "Non-Farm Employment Change", "impact": "High",
             "forecast": "98K", "previous": "162K"},
            {"release_at": "2026-09-30T08:30:00-04:00", "title": "Core PCE Price Index m/m", "impact": "High",
             "forecast": "0.3%", "previous": "0.2%"},
        ]
        self.now = datetime(2026, 9, 28, tzinfo=timezone.utc)

    def test_nfp_thresholds(self):
        dash = build_dashboard(self.raw, [], date(2026, 9, 28), self.events, [], self.now)
        up = {r["key"]: r for r in dash["releases"]["upcoming"]}
        sc = up["nfp"]["scenario"]
        # 新 3 个月均值 = (21 + 162 + X) / 3：X ≥ 267 时 ≥ 150（强），X ≤ −34 时 < 50（疲弱）；
        # 只留预期两侧最近的门槛，更远的「恶化」（X ≤ −184）不写，3 个月均值的区间也不写
        self.assertEqual(sc["affects"][0], "就业")
        self.assertFalse(sc["changed"])
        self.assertTrue(sc["text"].startswith("预期 98K，就业仍为「降温」；≥ 267K 变为「强」，增长变为「扩张偏强」"))
        self.assertIn("；≤ −34K 变为「疲弱」", sc["text"])
        self.assertNotIn("恶化", sc["text"])
        self.assertNotIn("均值", sc["text"])

    def test_core_pce_levels(self):
        dash = build_dashboard(self.raw, [], date(2026, 9, 28), self.events, [], self.now)
        up = {r["key"]: r for r in dash["releases"]["upcoming"]}
        # 环比本身没有标签，只写会不会改通胀和整体环境
        self.assertEqual(up["core_pce_mom"]["scenario"]["text"], "预期 0.3%，通胀仍为「偏热」，整体环境不变")

    def test_importance_and_today(self):
        from datetime import datetime, timezone

        from pipeline.macro import importance
        self.assertEqual(importance.rate("Non-Farm Employment Change")["stars"], 5)
        self.assertEqual(importance.rate("Final GDP q/q")["stars"], 1)
        self.assertEqual(importance.rate("FOMC Member Waller Speaks")["stars"], 2)
        self.assertIsNone(importance.rate("Something Else"))
        # 北京时间同一天、已经发布的条目也留在日历里，标 done，给「今日发布」用
        now = datetime(2026, 9, 30, 13, 0, tzinfo=timezone.utc)  # 北京时间 09-30 21:00
        dash = build_dashboard(self.raw, [], date(2026, 9, 30), self.events, [], now)
        rows = {r["key"]: r for r in dash["releases"]["upcoming"]}
        self.assertTrue(rows["core_pce_mom"]["done"])
        self.assertEqual(rows["core_pce_mom"]["bj_date"], "2026-09-30")
        self.assertEqual(rows["core_pce_mom"]["importance"]["stars"], 4)
        self.assertEqual(rows["nfp"]["bj_date"], "2026-10-02")

    def test_revision_has_no_scenario(self):
        # 已经公布过的期间（例如 GDP 终值）只是修订：日历上只留时间和预期
        raw = dict(self.raw)
        raw["PCEPILFE"] = [(d, 100 * 1.0028 ** i) for i, d in enumerate(months(date(2024, 1, 1), 32))]
        dash = build_dashboard(raw, [], date(2026, 9, 28), self.events, [], self.now)
        up = {r["key"]: r for r in dash["releases"]["upcoming"]}
        self.assertNotIn("scenario", up["core_pce_mom"])

    def test_impact_after_release(self):
        from datetime import datetime, timezone
        raw = dict(self.raw)
        pay = list(raw["PAYEMS"]) + [(date(2026, 9, 1), raw["PAYEMS"][-1][1] + 300)]
        raw["PAYEMS"] = pay
        dash = build_dashboard(raw, [], date(2026, 10, 3), self.events, [], datetime(2026, 10, 3, tzinfo=timezone.utc))
        rec = {r["title_en"]: r for r in dash["releases"]["recent"]}
        imp = rec["Non-Farm Employment Change"]["impact"]
        self.assertTrue(imp["changed"])
        self.assertIn("就业：「降温」→「强」", imp["text"])


class InterpretTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(I.core_mom(I.Ctx(0.4))[1], "alert")
        self.assertEqual(I.core_mom(I.Ctx(0.15))[1], "ok")
        # 环比通胀的级别按 3 个月均值
        self.assertEqual(I.core_cpi_mom(I.Ctx(0.29, extra={"avg3": 0.16}))[1], "ok")

    def test_growth_uses_actual_gdp(self):
        # 产出按已公布的上季实际 GDP 判断，GDPNow 5.0% 只作参考
        st = I.growth_state({"gdpnow": 5.0, "gdp_q": 1.4838, "nfp3": 71, "nfp3_ago": 38, "unrate_chg12": -0.2,
                             "sahm": -0.07})
        # 1.48 按公布精度算 1.5；标签不在理由里重复，理由写用哪几个数、对照什么
        self.assertEqual([w["k"] for w in st["why"]], ["产出接近潜在", "就业降温"])
        self.assertEqual(st["why"][0]["t"], "上季实际 GDP 1.5%，接近约 2% 的潜在增速")
        self.assertIn("月均新增 7.1 万人，只够维持失业率不变", st["why"][1]["t"])
        self.assertNotIn("GDPNow", st["head"])
        self.assertEqual(st["summary"], "产出接近潜在、就业降温")
        self.assertEqual(st["label"], "接近潜在")
        self.assertFalse(st["split"])
        self.assertEqual(st["level"], 0)
        self.assertIn("不进判断", st["anchors"]["gdpnow"])
        self.assertIn("高 3.5 个百分点", st["anchors"]["gdpnow"])
        # 还没有实际值时才用 GDPNow，并在句子里写明
        only = I.growth_state({"gdpnow": 5.0, "nfp3": 71})
        self.assertEqual(only["output"], "偏强")
        self.assertIn("暂按 GDPNow 模型预测 5.0%", only["head"])
        # 有核心 GDP 时以它定档；GDP 总量被净出口拖到另一档时，句子里写明
        dragged = I.growth_state({"gdp_q": 0.8, "core_gdp": 2.1, "nfp3": 71,
                                  "contrib": {"pce": 1.3, "fixed": 0.4, "inv": 0.3, "gov": 0.2, "nx": -1.4}})
        self.assertEqual(dragged["output"], "接近潜在")
        # 总量和各项贡献并列写出，拉高的在前、拖累的在后
        self.assertEqual(dragged["why"][0]["t"], "核心 GDP 2.1%，接近约 2% 的潜在增速；"
                                                 "总量 0.8%，消费贡献 +1.3、固定投资 +0.4，库存 +0.3、政府 +0.2、净出口 −1.4")
        self.assertIn("以核心为准", dragged["anchors"]["gdp_q"])
        # 反过来：库存把总量抬高，核心需求其实偏弱
        lifted = I.growth_state({"gdp_q": 2.6, "core_gdp": 1.0, "nfp3": 71,
                                 "contrib": {"inv": 1.1, "nx": 0.5, "gov": -0.2}})
        self.assertEqual(lifted["output"], "低于潜在")
        self.assertIn("总量 2.6%，库存 +1.1、净出口 +0.5、政府 −0.2", lifted["head"])
        # 分档相同时不加注
        same = I.growth_state({"gdp_q": 1.6, "core_gdp": 2.2, "nfp3": 71})
        self.assertNotIn("拖累", same["head"])
        # 分化时标签写「分化」，理由里分别写产出和就业，不再重复「分化」
        split = I.growth_state({"gdp_q": 1.5, "core_gdp": 4.2, "nfp3": 71})
        self.assertEqual(split["label"], "分化")
        self.assertNotIn("分化", split["head"])
        self.assertEqual(split["summary"], "产出偏强，但就业降温")
        # 分化时顶部标题用增长那句，不写「增长大致在潜在水平」
        env = I.environment(split, I.inflation_state({"core_yoy": 3.34}), {}, {})
        self.assertEqual(env["head"], "通胀粘性：产出偏强，但就业降温，通胀仍高于目标")
        weak = I.growth_state({"gdpnow": 0.3, "gdp_q": 0.8, "nfp3": -20, "unrate_chg12": 0.6, "sahm": 0.6})
        self.assertEqual(weak["label"], "收缩风险")

    def test_fiscal_and_policy_split(self):
        f = I.fiscal_state({"deficit": 5.4, "deficit_chg12": -0.7, "interest": 3.84, "debt": 123})
        self.assertEqual(f["label"], "脉冲收缩 · 偿债压力高")
        p = I.policy_state({"real_policy": 0.28, "current": 3.88, "year_end": 4.24, "next_year": 4.76,
                            "dot_next": 4.10})
        self.assertEqual(p["label"], "立场接近中性 · 市场定价加息")
        self.assertIn("加息约 3.5 次", p["head"])
        self.assertIn("比点阵图 4.10% 鹰 66bp", p["head"])
        # 标签已写结论，理由的小标题只写看哪一块，不重复标签
        self.assertEqual([w["k"] for w in f["why"]], ["财政脉冲", "偿债压力"])
        self.assertIn("对增长是拖累", f["why"][0]["t"])
        self.assertIn("超过 1990 年代峰值", f["summary"])
        self.assertNotIn("year_end", p["anchors"])

    def test_environment(self):
        g = I.growth_state({"gdpnow": 5.0, "gdp_q": 1.5, "nfp3": 71, "nfp3_ago": 38})
        i = I.inflation_state({"core_yoy": 3.34, "core_3m": 3.05, "nowcast": [("8 月", 3.40), ("9 月", 3.49)]})
        self.assertEqual([w["k"] for w in i["why"]], ["明显高于目标", "短期动能持平"])
        env = I.environment(g, i, {"summary": "政策立场接近中性"}, {"summary": "金融条件宽松"},
                            {"summary": "赤字在收窄"})
        self.assertEqual(env["name"], "通胀粘性")
        self.assertEqual(env["head"], "通胀粘性：增长大致在潜在水平，通胀仍高于目标")
        self.assertEqual([x["k"] for x in env["lines"]], ["经济", "流动性", "财政", "货币"])
        self.assertEqual(env["lines"][0]["t"], "产出接近潜在、就业降温；核心通胀明显高于目标，短期没有回落")
        # Nowcast 是模型预测：只比官方高 0.15 个百分点时，顶部和理由里都不提
        self.assertNotIn("Nowcast", i["head"] + str(env))
        hot = I.inflation_state({"core_yoy": 3.34, "core_3m": 3.05, "nowcast": [("9 月", 3.8)]})
        self.assertIn("模型预计回升", [w["k"] for w in hot["why"]])
        self.assertNotIn("Nowcast", str(I.environment(g, hot, {}, {})))
        stag = I.environment({"level": -1, "label": "放缓"}, {"level": 1, "label": "偏热"}, {}, {})
        self.assertEqual(stag["name"], "滞胀风险")


class ReservesTest(unittest.TestCase):
    def test_identity(self):
        wed = [date(2024, 1, 3), date(2024, 1, 10)]
        raw = {
            "WALCL": [(wed[0], 8_000_000.0), (wed[1], 8_010_000.0)],
            "WDTGAL": [(wed[0], 700_000.0), (wed[1], 650_000.0)],
            "WRBWFRBL": [(wed[0], 3_500_000.0), (wed[1], 3_600_000.0)],
            "WCURCIR": [(wed[0], 2_300_000.0), (wed[1], 2_300_000.0)],
            "RRPONTSYD": [(date(2024, 1, 2), 900.0), (wed[1], 850.0)],  # 十亿美元，第一周用前一天
        }
        fr = MacroBuilder(raw).reserves_frame()
        for i, d in enumerate(wed):
            total = sum(dict(fr[k])[d] for k in ("reserves", "rrp", "tga", "currency", "other"))
            self.assertAlmostEqual(total, dict(fr["assets"])[d])
        self.assertAlmostEqual(dict(fr["rrp"])[wed[0]], 900.0)
        self.assertAlmostEqual(dict(fr["other"])[wed[0]], 8000 - 900 - 700 - 2300 - 3500)


class DashboardTest(unittest.TestCase):
    def test_empty_data_builds(self):
        dash = build_dashboard({}, [], date(2026, 9, 28))
        self.assertEqual(len(dash["dimensions"]), 5)
        self.assertEqual(dash["verdict"]["name"], "数据不足")
        self.assertEqual(dash["charts"], {})

    def test_metric_rows(self):
        ms = months(date(2024, 1, 1), 30)
        raw = {"UNRATE": [(d, 4.0 + (0.1 if i == len(ms) - 1 else 0)) for i, d in enumerate(ms)]}
        m = {x["id"]: x for x in build_dashboard(raw, [], date(2026, 9, 28))["dimensions"][0]["metrics"]}
        c = m["unrate"]["chg"]
        self.assertEqual((c["text"], c["dir"], c["label"], c["base"]), ("+0.1", "up", "上月", "4.0"))
        self.assertEqual(m["unrate"]["date"], "2026-06")
        self.assertIn("Sahm", m["unrate"]["anchor"])
        raw = {"UNRATE": [(d, 4.0) for d in ms]}
        m = {x["id"]: x for x in build_dashboard(raw, [], date(2026, 9, 28))["dimensions"][0]["metrics"]}
        self.assertEqual(m["unrate"]["chg"]["text"], "持平")

    def test_core_gdp_rows(self):
        qs = [date(2025, m, 1) for m in (4, 7, 10)] + [date(2026, 1, 1), date(2026, 4, 1)]
        raw = {"GDPC1": [(d, 100 * 1.002 ** i) for i, d in enumerate(qs)],  # 每季约 0.8% 年化
               "PB0000031Q225SBEA": [(d, 2.0 + i * 0.05) for i, d in enumerate(qs)],
               "A019RY2Q224SBEA": [(d, -1.3) for d in qs], "DPCERY2Q224SBEA": [(d, 1.4) for d in qs],
               "PAYEMS": [(d, 1000.0 + 70 * i) for i, d in enumerate(months(date(2025, 1, 1), 20))]}
        dash = build_dashboard(raw, [], date(2026, 9, 28))
        g = dash["dimensions"][0]
        m = {x["id"]: x for x in g["metrics"]}
        self.assertEqual([x["id"] for x in g["metrics"]][:2], ["core_gdp", "gdp_q"])
        self.assertEqual((m["core_gdp"]["text"], m["core_gdp"]["date"]), ("2.2", "2026Q2"))
        self.assertEqual(m["gdp_q"]["note"], "贡献：消费 +1.4，净出口 -1.3")
        self.assertIn("总量 0.8%，消费贡献 +1.4，净出口 −1.3", g["why"][0]["t"])
        self.assertIn("gdp_contrib", dash["charts"])

    def test_nfp_row_uses_3m_average(self):
        # 格子里写 3 个月均值（规则用的数），单月放备注
        ms = months(date(2026, 1, 1), 8)
        pay = [(d, 1000.0 + sum((30, 30, 30, 30, 30, 21, 30, 162)[:i + 1])) for i, d in enumerate(ms)]
        m = {x["id"]: x for x in build_dashboard({"PAYEMS": pay}, [], date(2026, 9, 28))["dimensions"][0]["metrics"]}
        self.assertEqual(m["nfp3"]["text"], "71")
        self.assertEqual(m["nfp3"]["note"], "单月 162 千人（2026-08）")

    def test_effr_path(self):
        raw = {"EFFR": [(date(2026, 9, 24), 3.88)],
               "FEDTARMD": [(date(2026, 1, 1), 3.9), (date(2027, 1, 1), 3.6)],
               "FEDTARMDLR": [(date(2026, 9, 17), 3.0)]}
        exp = [{"date": "2026-09-27", "series_id": k, "value": v, "obs_date": "", "remark": ""}
               for k, v in (("effr_next", "4.046"), ("effr_year", "4.24"), ("effr_ny", "4.762"))]
        c = build_dashboard(raw, exp, date(2026, 9, 28))["charts"]["effr_path"]
        market, dots = c["series"]
        self.assertEqual(c["categories"], ["当前", "下次会议", "年底", "明年底", "长期"])
        self.assertEqual(market["data"], [3.88, 4.046, 4.24, 4.762, None])
        self.assertEqual(dots["data"], [3.88, None, 3.9, 3.6, 3.0])

    def test_meeting_anchor(self):
        raw = {"EFFR": [(date(2026, 9, 28), 3.88)]}
        remark_next = "最大概率区间3.75 - 4.00(52.9%)；**隐含加息0.5次**"
        remark_year = "最大概率区间4.00 - 4.25(51.9%)；**隐含加息1.3次**"
        exp = [
            {"date": "2026-09-28", "series_id": "effr_next", "value": "4.061", "remark": ""},
            {"date": "2026-09-29", "series_id": "effr_next", "value": "3.998", "remark": remark_next},
            {"date": "2026-09-28", "series_id": "effr_year", "value": "4.263", "remark": ""},
            {"date": "2026-09-29", "series_id": "effr_year", "value": "4.204", "remark": remark_year},
            {"date": "2026-09-29", "series_id": "effr_ny", "value": "4.783", "remark":
             "最大概率区间4.75 - 5.00(28.9%)；**隐含加息3.6次**"},
        ]
        m = {x["id"]: x for x in build_dashboard(raw, exp, date(2026, 9, 29))["dimensions"][4]["metrics"]}
        self.assertEqual(m["meeting"]["text"], "3.998")
        self.assertEqual(m["meeting"]["note"], "")
        self.assertEqual(m["meeting"]["chg"]["base"], "4.061")
        self.assertEqual(m["meeting"]["move"]["label"], "略转鸽")
        self.assertEqual(m["meeting"]["status"]["label"], "按兵不动")
        self.assertIn("隐含加息0.5次，最大概率区间 3.75 - 4.00（52.9%）", m["meeting"]["anchor"])
        self.assertIn("略转鸽：当日下跌 6.3bp，达到 5bp", m["meeting"]["anchor"])
        self.assertIn("隐含加息1.3次，最大概率区间 4.00 - 4.25（51.9%）", m["year_end"]["anchor"])
        self.assertIn("略转鸽：当日下跌 5.9bp，达到 5bp", m["year_end"]["anchor"])
        self.assertNotIn("背离", m["meeting"]["anchor"])
        self.assertIn("隐含加息3.6次", m["next_year"]["anchor"])
        self.assertNotIn("转鹰", m["next_year"]["anchor"])
        self.assertNotIn("转鸽", m["next_year"]["anchor"])
        self.assertEqual(m["year_end"]["status"]["label"], "定价加息约 1.3 次")
        self.assertEqual(m["next_year"]["status"]["label"], "定价加息约 3.6 次")


class CreditSpreadTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual([I.credit_level("hy", x) for x in (2.9, 3, 4.5, 6, 8)],
                         ["乐观", "正常", "紧张", "压力", "危机"])
        self.assertEqual([I.credit_level("ig", x) for x in (0.8, 0.9, 1.3, 1.8, 2.5)],
                         ["乐观", "正常", "紧张", "压力", "危机"])
        self.assertEqual(I.credit_status("hy", 3.02)["label"], "正常")
        self.assertEqual(I.credit_status("ig", 0.83)["label"], "乐观")

    def test_change(self):
        self.assertEqual(I.change_move("hy", 0.24)["label"], "平稳")
        self.assertEqual(I.change_move("hy", 0.25)["label"], "明显走阔")
        self.assertEqual(I.change_move("hy", -0.50)["label"], "明显收窄")
        self.assertEqual(I.change_move("hy", 0.51)["label"], "大幅走阔")
        self.assertEqual(I.change_move("ig", 0.04)["label"], "平稳")
        self.assertEqual(I.change_move("ig", -0.05)["label"], "明显收窄")
        self.assertEqual(I.change_move("ig", 0.10)["label"], "明显走阔")
        self.assertEqual(I.change_move("ig", 0.11)["label"], "大幅走阔")
        self.assertEqual(I.credit_anchor("hy"), "< 3% 乐观，3–4.5% 正常，4.5–6% 紧张，6–8% 压力，≥ 8% 危机")
        self.assertNotIn("当前为", I.credit_anchor("hy", 3.02))


class ChangeStatusTest(unittest.TestCase):
    def test_degree_and_direction(self):
        self.assertEqual(I.change_move("meeting", -0.058)["label"], "略转鸽")
        self.assertEqual(I.change_move("meeting", -0.12)["label"], "明显转鸽")
        self.assertEqual(I.change_move("meeting", 0.10)["label"], "略转鹰")
        self.assertEqual(I.change_move("meeting", 0.101)["label"], "明显转鹰")
        self.assertEqual(I.change_move("meeting", 0.02)["label"], "持平")
        self.assertEqual(I.change_move("nfp3", 30)["label"], "略转好")
        self.assertEqual(I.change_move("nfp3", 60)["label"], "明显转好")
        self.assertEqual(I.change_move("unrate", 0.2)["label"], "略转差")
        self.assertEqual(I.change_move("unrate", 0.3)["label"], "明显转差")
        self.assertEqual(I.change_move("core_yoy", -0.15)["label"], "略转好")
        self.assertIsNone(I.change_move("nope", 1))
        labels = {I.change_move(k, 1)["label"] for k in ("nfp3", "unrate", "core_yoy", "deficit")}
        self.assertTrue(labels <= {"明显转好", "略转好", "持平", "略转差", "明显转差"})
        self.assertEqual(I.change_move("meeting", 1)["label"], "明显转鹰")


class LevelStatusTest(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(I.level_status("core_gdp", 4.2)["label"], "偏强")
        self.assertEqual(I.level_status("core_gdp", 2.0)["label"], "接近潜在")
        self.assertEqual(I.level_status("nfp3", 71)["label"], "降温")
        self.assertEqual(I.level_status("nfp3", 160)["label"], "强")
        self.assertEqual(I.level_status("core_cpi_mom", 0.33)["label"], "明显高于目标")
        self.assertEqual(I.level_status("core_pce_mom", 0.17)["label"], "接近目标")
        self.assertEqual(I.level_status("supercore_cpi_mom", 0.10)["label"], "已降温")
        self.assertEqual(I.level_status("supercore_pce_mom", 0.30)["label"], "粘性强")
        mom_anchor = I.inflation_state({"core_yoy": 3.34, "core_cpi_mom": 0.3348})["anchors"]["core_cpi_mom"]
        self.assertIn("单月折年", mom_anchor)
        self.assertEqual(I.level_status("core_yoy", 3.34)["label"], "明显高于目标")
        self.assertEqual(I.level_status("core_yoy", 2.1)["label"], "接近目标")
        self.assertEqual(I.level_status("supercore", 3.92)["label"], "粘性强")
        self.assertEqual(I.level_status("fwd", 2.36)["label"], "锚定")
        self.assertEqual(I.level_status("hy", 3.02)["label"], "正常")
        self.assertEqual(I.level_status("ig", 0.83)["label"], "乐观")
        self.assertEqual(I.level_status("nfci", -0.56)["label"], "宽松")
        self.assertEqual(I.level_status("reserves_ratio", 9.1)["label"], "接近下限")
        self.assertEqual(I.level_status("sofr_iorb", 0)["label"], "不紧")
        self.assertEqual(I.level_status("deficit", 5.4, {"deficit_chg12": -0.7})["label"], "收缩")
        self.assertEqual(I.level_status("interest", 3.84)["label"], "高")
        self.assertEqual(I.level_status("real_policy", 0.28)["label"], "接近中性")
        self.assertEqual(I.level_status("meeting", 4.20, {"current": 3.88})["label"], "定价加息约 1.3 次")
        self.assertEqual(I.level_status("meeting", 3.90, {"current": 3.88})["label"], "按兵不动")
        self.assertEqual(I.level_status("meeting", 4.05, {"current": 3.88})["label"], "定价加息")
        self.assertEqual(I.level_status("unrate", 4.1, {"unrate_chg12": -0.2, "sahm": -0.07})["label"], "下降")
        self.assertEqual(I.level_status("core_3m", 3.05, {"core_yoy": 3.34})["label"], "短期动能持平")
        self.assertEqual(I.level_status("nowcast", 3.49, {"nowcast_gap": 0.15})["label"], "略高")


class EffrSignalTest(unittest.TestCase):
    def test_daily_and_five_day_levels(self):
        flat = [(date(2026, 9, d), 4.20) for d in range(24, 30)]
        self.assertEqual(I.tenor_signals(flat), ("", []))
        watch = flat[:-1] + [(date(2026, 9, 29), 4.26)]  # 当日 +6bp，5 日不够
        level, reasons = I.tenor_signals(watch)
        self.assertEqual(level, "略转鹰")
        self.assertIn("当日上涨 6bp，达到 5bp", reasons)
        hot = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.31)]
        level, reasons = I.tenor_signals(hot)
        self.assertEqual(level, "明显转鹰")
        self.assertIn("大于 10bp", reasons[0])
        # 正好 10bp 还不到「大于 10bp」
        edge = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.30)]
        self.assertEqual(I.tenor_signals(edge)[0], "略转鹰")
        down = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.14)]
        self.assertEqual(I.tenor_signals(down)[0], "略转鸽")
        five = [(date(2026, 9, 20), 4.00), (date(2026, 9, 24), 4.00), (date(2026, 9, 25), 4.08)]
        level, reasons = I.tenor_signals(five)
        self.assertEqual(level, "略转鹰")
        self.assertTrue(any("5 日累计" in r and "超过 5bp" in r for r in reasons))
        five_big = [(date(2026, 9, 20), 4.00), (date(2026, 9, 25), 4.12)]
        level, reasons = I.tenor_signals(five_big)
        self.assertEqual(level, "略转鹰")
        self.assertTrue(any("超过 0.1%" in r for r in reasons))

    def test_anchor_without_probability(self):
        remark = "隐含加息1.3次，最大概率区间4.00 - 4.25，5日累计变化历史快照不足"
        self.assertEqual(I.implied_anchor(remark), "隐含加息1.3次，最大概率区间 4.00 - 4.25")

    def test_meeting_versus_year(self):
        meet = [(date(2026, 9, 28), 4.00), (date(2026, 9, 29), 4.08)]
        year_up = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.28)]
        self.assertEqual(I.cross_signal(meet, year_up), ("", []))
        year_down = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.12)]
        level, reasons = I.cross_signal(meet, year_down)
        self.assertEqual((level, reasons), ("背离", ["背离（短期转鹰，长期转鸽）"]))
        year_flat = [(date(2026, 9, 28), 4.20), (date(2026, 9, 29), 4.205)]
        level, reasons = I.cross_signal(meet, year_flat)
        self.assertEqual((level, reasons), ("背离", ["背离（短期转鹰，长期几乎不动）"]))


class CpiContribTest(unittest.TestCase):
    """2026-09 用冻结的 FRED 指数和 BLS 2025-12 重置比重，对照新闻稿 Table 6。"""

    def setUp(self):
        self.anchor = cpi_contrib.Anchor(2026, 2026, date(2025, 12, 1), {
            "food": 13.698, "energy": 6.383, "core": 79.919,
            "core_goods": 19.176, "shelter": 35.625, "services_ex_energy": 60.744,
        })
        self.raw = {
            "CPIAUCSL": [(date(2025, 12, 1), 326.031), (date(2026, 8, 1), 334.131), (date(2026, 9, 1), 336.298)],
            "CPIUFDSL": [(date(2025, 12, 1), 344.632), (date(2026, 8, 1), 350.309), (date(2026, 9, 1), 350.477)],
            "CPILFESL": [(date(2025, 12, 1), 331.814), (date(2026, 8, 1), 337.765), (date(2026, 9, 1), 338.896)],
            "CUSR0000SACL1E": [(date(2025, 12, 1), 167.325), (date(2026, 8, 1), 167.939), (date(2026, 9, 1), 168.361)],
            "CUSR0000SAH1": [(date(2025, 12, 1), 421.039), (date(2026, 8, 1), 430.227), (date(2026, 9, 1), 431.908)],
            "CUSR0000SASLE": [(date(2025, 12, 1), 437.090), (date(2026, 8, 1), 447.075), (date(2026, 9, 1), 448.723)],
        }

    def test_sep2026_matches_table6_partition(self):
        br = cpi_contrib.breakdown(self.raw, [self.anchor])
        sep = date(2026, 9, 1)
        food, energy, core = (dict(br[k])[sep] for k in ("food", "energy", "core"))
        headline = (336.298 / 334.131 - 1) * 100
        self.assertAlmostEqual(food + energy + core, headline, places=9)
        # BLS Table 6（2026-09）：食品约 +0.006、核心约 +0.265、能源约 +0.377
        self.assertAlmostEqual(food, 0.0065, delta=0.001)
        self.assertAlmostEqual(core, 0.266, delta=0.002)
        self.assertAlmostEqual(energy, 0.377, delta=0.003)
        goods, shelter, super_ = (dict(br[k])[sep] for k in ("core_goods", "shelter", "supercore"))
        self.assertAlmostEqual(goods, 0.059, delta=0.005)
        self.assertAlmostEqual(shelter, 0.175, delta=0.005)
        self.assertAlmostEqual(super_, 0.107, delta=0.005)

    def test_missing_month_is_left_blank(self):
        raw = {
            "CPIAUCSL": [(date(2025, 9, 1), 100.0), (date(2025, 11, 1), 101.0), (date(2025, 12, 1), 102.0)],
            "CPIUFDSL": [(date(2025, 9, 1), 100.0), (date(2025, 11, 1), 100.2), (date(2025, 12, 1), 100.3)],
            "CPILFESL": [(date(2025, 9, 1), 100.0), (date(2025, 11, 1), 100.4), (date(2025, 12, 1), 100.5)],
            "CUSR0000SACL1E": [(date(2025, 9, 1), 50.0), (date(2025, 11, 1), 50.1), (date(2025, 12, 1), 50.2)],
            "CUSR0000SAH1": [(date(2025, 9, 1), 80.0), (date(2025, 11, 1), 80.2), (date(2025, 12, 1), 80.3)],
            "CUSR0000SASLE": [(date(2025, 9, 1), 90.0), (date(2025, 11, 1), 90.3), (date(2025, 12, 1), 90.4)],
        }
        anchor = cpi_contrib.Anchor(2025, 2025, date(2025, 12, 1), {
            "food": 10, "core": 80, "core_goods": 20, "shelter": 30, "services_ex_energy": 60,
        })
        br = cpi_contrib.breakdown(raw, [anchor])
        months = {d for d, _ in br["food"]}
        self.assertNotIn(date(2025, 11, 1), months)  # 上月 10 月没有指数
        self.assertIn(date(2025, 12, 1), months)

    def test_supercore_mom_matches_its_contribution(self):
        br = cpi_contrib.breakdown(self.raw, [self.anchor])
        sc = cpi_contrib.supercore_cpi(self.raw, [self.anchor])
        sep = date(2026, 9, 1)
        mom = dict(sc["mom"])[sep]
        levels = {k: dict(self.raw[sid]) for k, sid in cpi_contrib.INDEX.items()}
        prev = date(2026, 8, 1)
        ri_s = cpi_contrib.ri_at(self.anchor, levels, "services_ex_energy", prev)
        ri_h = cpi_contrib.ri_at(self.anchor, levels, "shelter", prev)
        ri_core = cpi_contrib.ri_at(self.anchor, levels, "core", prev)
        # 对核心的贡献（百分点）= 环比（%）× 超级核心占核心的比重
        self.assertAlmostEqual(dict(br["supercore"])[sep], mom * (ri_s - ri_h) / ri_core, places=4)
        self.assertAlmostEqual(mom, 0.34, delta=0.03)
        self.assertEqual(sc["ann3"], [])  # 窗口里没有连续三个月
        self.assertEqual(sc["yoy"], [])

    def test_yoy_partition_adds_to_headline(self):
        # 从 2024-12 锚起，每月整体 +1%、食品持平、核心跟着整体，同比贡献应加总回整体同比
        anchor = cpi_contrib.Anchor(2025, 2026, date(2024, 12, 1), {
            "food": 10.0, "core": 80.0, "core_goods": 20.0, "shelter": 30.0, "services_ex_energy": 60.0,
        })
        raw = {sid: [] for sid in (
            "CPIAUCSL", "CPIUFDSL", "CPILFESL", "CUSR0000SACL1E", "CUSR0000SAH1", "CUSR0000SASLE")}
        level = {sid: 100.0 for sid in raw}
        d = date(2024, 12, 1)
        for _ in range(13):
            for sid in raw:
                raw[sid].append((d, level[sid]))
            level["CPIAUCSL"] *= 1.01
            level["CPILFESL"] *= 1.01
            level["CUSR0000SACL1E"] *= 1.005
            level["CUSR0000SAH1"] *= 1.012
            level["CUSR0000SASLE"] *= 1.011
            d = ts._shift_month(d, 1)
        yoy = cpi_contrib.contributions(raw, 12, [anchor])
        end = date(2025, 12, 1)
        food, energy, core = (dict(yoy[k])[end] for k in ("food", "energy", "core"))
        headline = (dict(raw["CPIAUCSL"])[end] / dict(raw["CPIAUCSL"])[date(2024, 12, 1)] - 1) * 100
        self.assertAlmostEqual(food + energy + core, headline, places=6)
        self.assertGreater(dict(yoy["supercore"])[end], 0)

    def test_forecast_columns_only_on_cpi_actuals(self):
        start, rows_cpi, rows_core = date(2025, 9, 1), [], []
        level_cpi, level_core = 320.0, 330.0
        for _ in range(13):
            rows_cpi.append((start, level_cpi))
            rows_core.append((start, level_core))
            level_cpi *= 1.003
            level_core *= 1.002
            start = ts._shift_month(start, 1)
        events = [
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "CPI y/y", "forecast": "3.6%"},
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "CPI m/m", "forecast": "0.6%"},
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "Core CPI y/y", "forecast": "2.5%"},
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "Core CPI m/m", "forecast": "0.2%"},
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "PPI m/m", "forecast": "0.4%"},
        ]
        from datetime import datetime, timezone
        dash = build_dashboard({"CPIAUCSL": rows_cpi, "CPILFESL": rows_core}, [], date(2026, 10, 15),
                               events, [], datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(dash["charts"]["cpi"]["forecasts"][0]["data"], [["2026-09-01", 3.6]])
        self.assertEqual([c["for"] for c in dash["charts"]["cpi"]["forecasts"]], ["CPI", "核心 CPI"])
        self.assertEqual(dash["charts"]["cpi_mom"]["forecasts"][1]["data"], [["2026-09-01", 0.2]])
        self.assertNotIn("forecasts", dash["charts"]["cpi_ann"])
        self.assertEqual([s["name"] for s in dash["charts"]["cpi_ann"]["series"]],
                         ["3 个月年化", "6 个月年化", "同比"])
        self.assertEqual(dash["charts"]["cpi_ann"]["title"], "CPI 年化动能")
        core_ann = dash["charts"]["core_cpi_ann"]
        self.assertEqual([s["name"] for s in core_ann["series"]], ["3 个月年化", "6 个月年化", "同比"])
        self.assertTrue(core_ann["core"])
        self.assertEqual([c["for"] for c in dash["charts"]["core_cpi"]["forecasts"]], ["环比", "同比"])
        self.assertEqual(dash["charts"]["core_cpi"]["forecasts"][0]["data"], [["2026-09-01", 0.2]])
        inf = dash["dimensions"][1]
        self.assertEqual(inf["metric_layout"], "surprise")
        self.assertEqual([m["id"] for m in inf["metrics"]], ["core_cpi_mom"])
        self.assertEqual(inf["metrics"][0]["expect"], "0.20")
        self.assertEqual(inf["metrics"][0]["verdict"]["label"], "符合预期")

    def test_inflation_table_matches_forecast_month(self):
        # 核心 CPI 最新是 9 月、核心 PCE 最新是 8 月，预期必须落在各自那一期，不能串月
        cpi, pce, super_ = [], [], []
        d, cpi_v, pce_v, sc_v = date(2025, 8, 1), 330.0, 128.0, 130.0
        for i in range(14):
            cpi.append((d, cpi_v))
            if d <= date(2026, 8, 1):
                pce.append((d, pce_v))
                super_.append((d, sc_v))
            cpi_v *= 1.003  # 环比 0.3%，预期 0.2% → 超预期
            pce_v *= 1.002  # 环比 0.2%，预期 0.3% → 低于预期
            sc_v *= 1.002
            d = ts._shift_month(d, 1)
        events = [
            {"release_at": "2026-10-14T08:30:00-04:00", "title": "Core CPI m/m", "forecast": "0.2%"},
            {"release_at": "2026-09-30T08:30:00-04:00", "title": "Core PCE Price Index m/m", "forecast": "0.3%"},
            {"release_at": "2026-08-12T08:30:00-04:00", "title": "Core CPI m/m", "forecast": "9.9%"},
        ]
        from datetime import datetime, timezone
        dash = build_dashboard(
            {"CPILFESL": cpi, "PCEPILFE": pce, "IA001260M": super_}, [], date(2026, 10, 15),
            events, [], datetime(2026, 10, 15, tzinfo=timezone.utc))
        m = {x["id"]: x for x in dash["dimensions"][1]["metrics"]}
        self.assertEqual([x["id"] for x in dash["dimensions"][1]["metrics"]],
                         ["core_cpi_mom", "core_pce_mom", "supercore_pce_mom"])
        self.assertEqual(m["core_cpi_mom"]["date"], "2026-09")
        self.assertEqual((m["core_cpi_mom"]["expect"], m["core_cpi_mom"]["verdict"]["label"]), ("0.20", "超预期"))
        self.assertEqual(m["core_pce_mom"]["date"], "2026-08")
        self.assertEqual((m["core_pce_mom"]["expect"], m["core_pce_mom"]["verdict"]["label"]), ("0.30", "低于预期"))
        self.assertIsNone(m["supercore_pce_mom"]["expect"])
        self.assertIsNone(m["supercore_pce_mom"]["verdict"])
        core_ids = [cid for g in dash["sections"][1]["groups"] for cid in g["charts"]
                    if dash["charts"][cid]["core"]]
        titles = [dash["charts"][i]["title"] for i in core_ids]
        want = ["核心 PCE：环比与同比", "核心 PCE 环比：按贡献拆分", "核心 PCE 同比：按贡献拆分",
                "核心 PCE 年化动能", "超级核心 PCE 年化动能", "核心 CPI：环比与同比",
                "核心 CPI 环比：按贡献拆分", "核心 CPI 同比：按贡献拆分", "核心 CPI 年化动能"]
        self.assertEqual([t for t in titles if t in want], want)
        self.assertLess(titles.index("超级核心 PCE 年化动能"), titles.index("核心 CPI：环比与同比"))
        self.assertEqual([s["name"] for s in dash["charts"]["core_pce_ann"]["series"]],
                         [s["name"] for s in dash["charts"]["core_cpi_ann"]["series"]])


class ZoriTest(unittest.TestCase):
    def test_parse_aligns_to_month_start(self):
        text = ("RegionID,SizeRank,RegionName,RegionType,2026-07-31,2026-08-31\n"
                "1,0,United States,country,100,102\n"
                "2,1,Somewhere,metro,1,1\n")
        series = zori.parse_wide(text)
        self.assertEqual(series, [(date(2026, 7, 1), 100.0), (date(2026, 8, 1), 102.0)])
        yoy = ts.pct_change([(date(2025, 8, 1), 100.0)] + series, 12, "M")
        self.assertEqual(yoy[0][0], date(2026, 8, 1))
        self.assertAlmostEqual(yoy[0][1], 2.0)


class EffrExpectTest(unittest.TestCase):
    def test_next_meeting_keeps_zq_sessions_only(self):
        text = (
            "date,series_id,name,value,unit,change_text,label,obs_date,carried,section,remark,hike_count,source\n"
            "2026-09-29,effr_next,下次,3.998,百分比,,中性,2026-09-29,,利率,"
            "Investing Fed Rate Monitor；按目标上限-EFFR利差12bp折算,,x\n"
            "2026-09-30,下次会议EFFR,下次,3.969,百分比,,中性,2026-09-30,,利率,"
            "隐含加息0.4次，最大概率区间3.75 - 4.00,,x\n"
            "2026-10-03,下次会议EFFR,下次,3.93,百分比,,中性,2026-10-02,1,利率,"
            "休市/未更新，使用最近价格 2026-10-02；TradingView CBOT:ZQX2026 收盘96.070→100−价格=3.930%，日期2026-10-02,,x\n"
            "2026-10-04,下次会议EFFR,下次,3.93,百分比,,中性,2026-10-02,1,利率,"
            "休市/未更新，使用最近价格 2026-10-02；CBOT:ZQX2026 收盘96.070→100−价格=3.930%，日期2026-10-02,,x\n"
            "2026-10-16,下次会议EFFR,下次,3.92,百分比,,中性,,,利率,"
            "隐含加息0.2次；合约 CBOT:ZQX2026，收盘96.080→100−价格=3.920%，日期2026-10-16,,x\n"
            "2026-10-17,下次会议EFFR,下次,3.92,百分比,,中性,,,利率,"
            "合约 CBOT:ZQX2026，收盘96.080→100−价格=3.920%，日期2026-10-16,,x\n"
            "2026-10-16,effr_year,年底,4.15,百分比,,中性,,,利率,"
            "合约 CBOT:ZQF2027，收盘95.850→100−价格=4.150%，日期2026-10-16,,x\n"
            "2026-09-26,cnn_fg,CNN,33,点,,,2026-09-26,,,,,x\n"
        )
        rows = effr_expect.prepare(effr_expect.parse_source(text))
        nxt_rows = [r for r in rows if r["series_id"] == "effr_next"]
        self.assertEqual([(r["date"], r["value"]) for r in nxt_rows],
                         [("2026-10-02", "3.93"), ("2026-10-16", "3.92")])
        self.assertIn(effr_expect.METHOD_MARK, nxt_rows[0]["remark"])
        self.assertIn("ZQX2026", nxt_rows[0]["remark"])
        self.assertNotIn(effr_expect.METHOD_MARK, nxt_rows[1]["remark"])
        self.assertEqual([r["value"] for r in rows if r["series_id"] == "effr_year"], ["4.15"])

    def test_merge_drops_investing_history(self):
        old = [{"date": "2026-09-29", "series_id": "effr_next", "value": "3.998",
                "obs_date": "2026-09-29", "remark": "按目标上限-EFFR利差12bp"}]
        new = [{"date": "2026-10-16", "series_id": "effr_next", "value": "3.92",
                "obs_date": "2026-10-16", "remark": "CBOT:ZQX2026 100−价格"}]
        rows = effr_expect.prepare(effr_expect.merge(effr_expect._drop_old_next(old), new))
        self.assertEqual([(r["date"], r["value"]) for r in rows], [("2026-10-16", "3.92")])
        self.assertIn(effr_expect.METHOD_MARK, rows[0]["remark"])


class DxyTest(unittest.TestCase):
    def test_skips_blank_days_and_uses_official_last(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")

        def ts(y, m, d, hour=0):
            return int(datetime(y, m, d, hour, tzinfo=ny).timestamp())

        payload = {"chart": {"result": [{
            "timestamp": [ts(2026, 10, 15), ts(2026, 10, 16), ts(2026, 10, 18)],
            "indicators": {"quote": [{"close": [102.0, 101.839996, None]}]},
            "meta": {"regularMarketPrice": 101.779, "regularMarketTime": ts(2026, 10, 16, 17)},
        }]}}
        self.assertEqual(dxy.parse_chart(payload),
                         [(date(2026, 10, 15), 102.0), (date(2026, 10, 16), 101.779)])
        # 报价落在没有 K 线的日子上时，不补出这一天
        payload["chart"]["result"][0]["meta"]["regularMarketTime"] = ts(2026, 10, 18, 12)
        self.assertEqual([d for d, _ in dxy.parse_chart(payload)],
                         [date(2026, 10, 15), date(2026, 10, 16)])


class TreasuryTest(unittest.TestCase):
    NOMINAL = (
        "Date,2 Yr,3 Mo,10 Yr\n"
        "10/16/2026,4.74,4.26,5.09\n"
        "10/19/2026,4.77,4.27,5.15\n"
        "10/20/2026,,4.28,\n"
    )
    REAL = "Date,10 YR\n10/16/2026,2.74\n10/19/2026,2.80\n"

    def test_parse_and_extend_only_newer_dates(self):
        nominal = treasury.parse_curve(self.NOMINAL)
        real = treasury.parse_curve(self.REAL)
        self.assertEqual(nominal["10 Yr"][-1], (date(2026, 10, 19), 5.15))
        self.assertEqual(nominal["3 Mo"][-1], (date(2026, 10, 20), 4.28))
        existing = [(date(2026, 10, 15), 5.10), (date(2026, 10, 16), 5.09)]
        merged, added = treasury.extend_series(existing, nominal["10 Yr"])
        self.assertEqual(added, "2026-10-19")
        self.assertEqual(merged[-1], (date(2026, 10, 19), 5.15))
        self.assertEqual(merged[0], (date(2026, 10, 15), 5.10))
        # 不覆盖 FRED 已有的最后一天，也不在空文件上用一年曲线替换全历史
        again, none = treasury.extend_series(merged, nominal["10 Yr"])
        self.assertIsNone(none)
        self.assertEqual(again, merged)
        self.assertEqual(treasury.extend_series([], nominal["10 Yr"])[1], None)

    def test_apply_writes_levels_and_lags_spreads(self):
        import tempfile
        from pipeline.fred import read_series, write_series

        curves = {
            treasury.NOMINAL: treasury.parse_curve(self.NOMINAL),
            treasury.REAL: treasury.parse_curve(self.REAL),
        }
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            write_series(raw / "DGS10.csv", [(date(2026, 10, 16), 5.09)])
            write_series(raw / "DGS2.csv", [(date(2026, 10, 16), 4.74)])
            write_series(raw / "DFII10.csv", [(date(2026, 10, 16), 2.74)])
            write_series(raw / "T10Y2Y.csv", [(date(2026, 10, 19), 0.38)])  # 已经跟上，不改
            write_series(raw / "T10YIE.csv", [(date(2026, 10, 16), 2.35)])
            filled = treasury.apply(raw, curves)
            self.assertEqual(filled["DGS10"], "2026-10-19")
            self.assertEqual(filled["DGS2"], "2026-10-19")
            self.assertEqual(filled["DFII10"], "2026-10-19")
            self.assertNotIn("T10Y2Y", filled)
            self.assertEqual(read_series(raw / "T10Y2Y.csv"), [(date(2026, 10, 19), 0.38)])
            self.assertEqual(read_series(raw / "T10YIE.csv")[-1], (date(2026, 10, 19), 2.35))
            self.assertEqual(read_series(raw / "DGS10.csv")[-1], (date(2026, 10, 19), 5.15))


if __name__ == "__main__":
    unittest.main()
