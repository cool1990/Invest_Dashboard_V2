"""加密货币四步规则和解析。不访问网络。"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.crypto import basis, binance, bitview, build, checkonchain, etf, mstr, urpd  # noqa: E402
from pipeline.crypto import interpret as I  # noqa: E402
from pipeline.crypto.build import build_dashboard, delta  # noqa: E402


def _sample():
    return {
        "price": 85749,
        "premia": [16.0, 9.9, 10.8],
        "mvrv": 1.60,
        "nupl": 0.375,
        "profit_share": 77.5,
        "ex_flow": -2906,
        "etf_7d": 285.8,
        "lth_7d": -18100,
        "ex_supply_7d": -9378,
        "oi_pct": 2.5,
        "funding_7d": 4.4,
        "urpd": {"lo": 84000, "hi": 86000, "inside": True},
        "cluster": "7.4–7.8 万",
    }


class LabelTest(unittest.TestCase):
    def test_price_bands(self):
        self.assertEqual(I.price_label([16.0, 9.9, 10.8]), "中性")
        self.assertEqual(I.price_label([49.9, 49.9, 49.9]), "中性")
        self.assertEqual(I.price_label([50, 50, 50]), "贵")
        self.assertEqual(I.price_label([-1, 10, 12]), "便宜")
        self.assertEqual(I.price_label([-0.1, -2, -3]), "便宜")
        self.assertEqual(I.price_label([None, None, None]), "数据不足")

    def test_profit_bands(self):
        self.assertEqual(I.profit_label(0.99, 0.1, 40), "亏损")
        self.assertEqual(I.profit_label(1.60, 0.375, 77.5), "中等")
        self.assertEqual(I.profit_label(2.39, 0.5, 89), "中等")
        self.assertEqual(I.profit_label(2.4, 0.5, 80), "过热")
        self.assertEqual(I.profit_label(1.2, 0.2, 90), "过热")
        self.assertEqual(I.profit_label(None, -0.01, None), "亏损")
        self.assertEqual(I.profit_label(None, None, None), "数据不足")

    def test_behavior_votes(self):
        self.assertEqual(I.behavior_label(-2906, 285.8, -18100, -9378), "吸筹")
        self.assertEqual(I.behavior_label(-1000, None, None, None), "吸筹")
        self.assertEqual(I.behavior_label(-999, None, None, None), "数据不足")
        self.assertEqual(I.behavior_label(-2906, 285.8, -18100, None), "中性")
        self.assertEqual(I.behavior_label(2000, -200, -20000, 8000), "派发")
        self.assertEqual(I.behavior_label(None, None, None, None), "数据不足")

    def test_leverage_bands(self):
        self.assertEqual(I.leverage_label(2.5, 4.4), "低")
        self.assertEqual(I.leverage_label(2.9, 14.9), "低")
        self.assertEqual(I.leverage_label(None, 4), "低")
        self.assertEqual(I.leverage_label(3.9, 10), "中")
        self.assertEqual(I.leverage_label(4, 4), "高")
        self.assertEqual(I.leverage_label(2, 40), "高")
        self.assertEqual(I.leverage_label(None, None), "数据不足")

    def test_sample_sentence(self):
        got = I.compose(_sample())
        self.assertEqual(got["price"], "中性")
        self.assertEqual(got["profit"], "中等")
        self.assertEqual(got["behavior"], "吸筹")
        self.assertEqual(got["leverage"], "低")
        self.assertIn("85749", got["headline"])
        self.assertIn("横盘", got["headline"])
        self.assertIn("风险不大", got["synthesis"])
        self.assertIn("84000", got["synthesis"])
        self.assertIn("7.4", got["synthesis"])

    def test_missing_does_not_invent_a_price(self):
        got = I.compose({})
        self.assertEqual(got["price"], "数据不足")
        self.assertNotIn("85749", got["headline"])
        self.assertTrue(I.rules())


class DeltaTest(unittest.TestCase):
    def test_exact_day_only(self):
        start = date(2026, 10, 5)
        s = [(start - timedelta(days=8), 10), (start, 12)]
        self.assertIsNone(delta(s, 7))
        s2 = [(start - timedelta(days=7), 10), (start, 12)]
        self.assertEqual(delta(s2, 7), 2)


class UrpdTest(unittest.TestCase):
    def test_densest_bin_keeps_near_spot(self):
        payload = {
            "close": 85000, "date": "2026-10-05",
            "buckets": [
                {"price_floor": 100, "supply": 3_000_000},
                {"price_floor": 84000, "supply": 800_000},
                {"price_floor": 85500, "supply": 700_000},
                {"price_floor": 62000, "supply": 1_000_000},
            ],
        }
        got = urpd.band(payload)
        self.assertEqual(got["lo"], 84000)
        self.assertEqual(got["hi"], 86000)
        self.assertAlmostEqual(got["supply"], 1_500_000)


class BitviewAlignTest(unittest.TestCase):
    def test_absolute_index(self):
        dates = {"start": 10, "data": ["2026-10-04", "2026-10-05"]}
        values = {"start": 10, "data": [73939.2, None]}
        self.assertEqual(bitview.align(dates, values), {"2026-10-04": 73939.2})


class FundingAnnTest(unittest.TestCase):
    def test_neutral_eight_hour_is_about_11(self):
        self.assertAlmostEqual(binance.annualize_funding(0.0001), 10.95, places=2)

    def test_day_mean(self):
        rows = [
            {"time": "2026-10-05T00:00:00Z", "rate": "0.00010000"},
            {"time": "2026-10-05T08:00:00Z", "rate": "0.00010000"},
            {"time": "2026-10-05T16:00:00Z", "rate": "0.00010000"},
        ]
        daily, week = binance.funding_annualized(rows)
        self.assertEqual(daily[-1][0], date(2026, 10, 5))
        self.assertAlmostEqual(daily[-1][1], 10.95, places=2)
        self.assertAlmostEqual(week[-1][1], 10.95, places=2)


class EtfParseTest(unittest.TestCase):
    HTML = """
    <table class="etf"><thead><tr><th>Date</th><th>IBIT</th><th>FBTC</th><th>Total</th></tr></thead><tbody>
      <tr><td>05 Oct 2026</td><td>69.9</td><td>(74.5)</td><td>(89.8)</td></tr>
      <tr><td>Total</td><td>1</td><td>2</td><td>3</td></tr>
    </tbody></table>
    """

    def test_issuers_and_total(self):
        rows = etf.parse_html(self.HTML)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-10-05")
        self.assertAlmostEqual(float(rows[0]["total_usd_mn"]), -89.8)
        self.assertAlmostEqual(float(rows[0]["IBIT"]), 69.9)
        self.assertAlmostEqual(float(rows[0]["FBTC"]), -74.5)


class MstrParseTest(unittest.TestCase):
    TEXT = (
        "BTC Update During Period October 1, 2026 to October 4, 2026* BTC Acquired (1) "
        "Aggregate Purchase Price (in millions) (2) Average Purchase Price (2) 334 $28.7 $85,838.8 "
        "As of October 4, 2026* Aggregate BTC Holdings Aggregate Purchase Price (in billions) (2) "
        "Average Purchase Price (2) 848,000 $63.97 $75,440.7"
    )

    def test_table(self):
        row = mstr.parse_text(self.TEXT, "0001193125-26-413164", "2026-10-05")
        self.assertEqual(row["asof"], "2026-10-04")
        self.assertEqual(row["holdings"], 848000)
        self.assertAlmostEqual(row["avg_cost"], 75440.7)
        self.assertEqual(row["acquired"], 334)

    def test_prose_holdings(self):
        text = (
            "Bitcoin Holdings Update On February 26, 2024, MicroStrategy announced that, "
            "during the period between February 15, 2024 and February 25, 2024, MicroStrategy "
            "acquired approximately 3,000 bitcoins for approximately $155.4 million in cash, "
            "at an average price of approximately $51,813 per bitcoin. "
            "As of February 25, 2024, MicroStrategy, together with its subsidiaries, "
            "held an aggregate of approximately 193,000 bitcoins, which were acquired at an "
            "aggregate purchase price of approximately $6.09 billion and an average purchase "
            "price of approximately $31,544 per bitcoin."
        )
        row = mstr.parse_text(text, "0001193125-24-045396", "2024-02-26")
        self.assertEqual(row["asof"], "2024-02-25")
        self.assertEqual(row["holdings"], 193000)
        self.assertAlmostEqual(row["avg_cost"], 31544)
        self.assertAlmostEqual(row["cost_bn"], 6.09)
        self.assertEqual(row["acquired"], 3000)
        self.assertEqual(row["acquired_from"], "2024-02-15")
        self.assertAlmostEqual(row["buy_px"], 51813)

    def test_million_cost_without_average(self):
        text = (
            "As of December 4, 2020, the Company holds approximately 40,824 bitcoins that were "
            "acquired at an aggregate purchase price of $475.0 million, inclusive of fees and expenses."
        )
        row = mstr.parse_text(text, "0001193125-20-310787", "2020-12-04")
        self.assertEqual(row["holdings"], 40824)
        self.assertAlmostEqual(row["cost_bn"], 0.475)
        self.assertIsNone(row["avg_cost"])

    def test_early_holds_sentence(self):
        text = (
            "acquired approximately 271 bitcoins for $10.0 million in cash, "
            "at an average price of approximately $43,663 per bitcoin, inclusive of fees and expenses. "
            "As of May 18, 2021, the Company holds approximately 92,079 bitcoins that were acquired "
            "at an aggregate purchase price of $2.251 billion and an average purchase price of "
            "approximately $24,450 per bitcoin."
        )
        row = mstr.parse_text(text, "0001193125-21-164617", "2021-05-18")
        self.assertEqual(row["asof"], "2021-05-18")
        self.assertEqual(row["holdings"], 92079)
        self.assertAlmostEqual(row["avg_cost"], 24450)
        self.assertAlmostEqual(row["cost_bn"], 2.251)
        self.assertEqual(row["acquired"], 271)
        self.assertAlmostEqual(row["buy_px"], 43663)

    def test_primary_skips_xbrl_cover(self):
        index = {"directory": {"item": [
            {"name": "R1.htm"},
            {"name": "0001193125-26-413164-index.html"},
            {"name": "mstr-20261005.htm"},
        ]}}
        self.assertEqual(mstr._primary_name(index), "mstr-20261005.htm")


class CheckonchainFinalTest(unittest.TestCase):
    def test_drops_generation_day(self):
        traces = [{
            "name": "LTH True Realised Price",
            "x": ["2026-10-04", "2026-10-05", "2026-10-06"],
            "y": [78027.0, 78020.4, 77855.0],
        }]
        pts = checkonchain.final_points(traces, "Tue, 06 Oct 2026 05:08:00 GMT")
        self.assertEqual(pts[-1][0], "2026-10-05")
        self.assertAlmostEqual(pts[-1][1], 78020.4)


class BasisDayTest(unittest.TestCase):
    def test_days_match_close_to_0800_utc(self):
        left = basis.days_to_expiry(date(2026, 10, 5), date(2026, 12, 25))
        self.assertAlmostEqual(left, 80 + 8 / 24, places=4)

    def test_front_contract(self):
        spot = [(date(2026, 10, 5), 85766.87)]
        far = [(date(2026, 10, 5), 87927.0)]
        near = [(date(2026, 10, 5), 86778.0)]
        rows = basis.build_series({
            "BTCUSDT_270326": (date(2027, 3, 26), far),
            "BTCUSDT_261225": (date(2026, 12, 25), near),
        }, spot)
        self.assertEqual(rows[0]["symbol"], "BTCUSDT_261225")
        self.assertAlmostEqual(float(rows[0]["ann_pct"]), 5.3565, places=2)


class MetricsParseTest(unittest.TestCase):
    def test_last_row(self):
        text = (
            "create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            "2026-09-26 00:00:00,BTCUSDT,1,100,1,1.5,1.1,1\n"
            "2026-09-26 12:00:00,BTCUSDT,2,250,1.2,1.8,1.4,1\n"
        )
        row = binance.parse_metrics_csv(text)
        self.assertEqual(row["date"], "2026-09-26")
        self.assertEqual(row["oi_usd"], 250)
        self.assertEqual(row["ls_ratio"], 1.4)
        self.assertAlmostEqual(row["ls_top_position"], 1.8)


class BuildShapeTest(unittest.TestCase):
    def test_index_fields_and_no_fill(self):
        day = date(2026, 10, 5)
        prev = day - timedelta(days=7)
        price = [(prev, 83000.0), (day, 85748.96)]
        src = {
            "coinbase": price,
            "bitview": {
                "price_close": [(date(2011, 1, 1), 1.0), (day, 86017.0)],
                "sth_realized_price": [(day, 73939.2)],
                "lth_realized_price": [(day, 49438.6)],
                "true_market_mean": [(day, 77389.8)],
                "realized_price": [(day, 53733.9)],
                "mvrv": [(prev, 1.555), (day, 1.6008)],
                "sth_mvrv": [(day, 1.163)],
                "lth_mvrv": [(day, 1.740)],
                "nupl": [(day, 0.3753)],
                "supply_in_profit_share": [(prev, 70.85), (day, 77.48)],
                "lth_supply": [(prev, 16_589_135.0), (day, 16_571_052.0)],
                "sth_supply": [(prev, 3_501_477.0), (day, 3_522_779.0)],
            },
            "lth_true": [(day - timedelta(days=1), 78027.0), (day, 78020.4)],
            "cm": {},
            "etf": [{"date": "2026-10-05", "total_usd_mn": "-89.8", "IBIT": "69.9"}],
            "funding": [
                {"time": "2026-10-05T00:00:00Z", "rate": "0.00003380"},
                {"time": "2026-10-05T08:00:00Z", "rate": "0.00003380"},
                {"time": "2026-10-05T16:00:00Z", "rate": "0.00003380"},
            ],
            "metrics": {"oi_usd": [], "ls_ratio": [], "ls_top_position": [], "time": {}},
            "basis": [(day, 5.36)],
            "mstr": [{"asof": "2026-10-04", "holdings": 848000, "avg_cost": 75440.7, "acquired": 334,
                      "acquired_from": "2026-10-01", "acquired_to": "2026-10-04", "filed": "2026-10-05"}],
            "miner": {
                "miner_cash_cost": {"label": "矿企现金成本", "value": 75500, "data_quarter": "2026Q2", "note": "现金"},
            },
            "urpd": {"lo": 84000, "hi": 86000, "supply": 1_399_000, "date": "2026-10-05"},
            "oi_network": [{"date": "2026-10-06", "fetched_at": "2026-10-06T11:15:00Z", "oi_usd_b": "43.34", "oi_mcap_pct": "2.51"}],
        }
        dash, files = build_dashboard(src, today=date(2026, 10, 6))
        self.assertEqual(dash["verdict"]["headline"][:1], "比")
        labels = {d["key"]: d["label"] for d in dash["dimensions"]}
        self.assertEqual(labels["price"], "中性")
        self.assertEqual(labels["profit"], "中等")
        self.assertIn("headline", dash["verdict"])
        # 重叠日用 Coinbase，不用 bitview 的 86017 去填
        series = {s["name"]: s["data"] for s in files["price"]["series"]}
        last_px = series["价格"][-1]
        self.assertEqual(last_px[0], "2026-10-05")
        self.assertAlmostEqual(last_px[1], 85748.96, places=2)
        self.assertNotIn(86017, [p[1] for p in series["价格"] if p[0] == "2026-10-05"])
        # 早期 bitview 还在
        self.assertEqual(series["价格"][0][0], "2011-01-01")
        names = [r["name"] for r in dash["sections"][0]["table"]]
        self.assertGreater(names.index("LTH 成本"), names.index("矿企现金成本"))
        self.assertTrue(any(r["name"] == "URPD 最密带" and "区间内" in r["gap"] for r in dash["sections"][0]["table"]))
        self.assertIn("mNAV", [k["name"] for g in dash["sections"][2]["groups"] for k in g["kpis"]])
        mnav = next(k for g in dash["sections"][2]["groups"] for k in g["kpis"] if k["name"] == "mNAV")
        self.assertEqual(mnav["text"], "未取到")


if __name__ == "__main__":
    unittest.main()
