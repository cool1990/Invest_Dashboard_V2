#!/usr/bin/env python3
"""更新加密货币板块：各来源分开抓，一个失败不影响别的，然后生成页面数据。

  python3 scripts/update_crypto.py
  python3 scripts/update_crypto.py --offline
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import statelog  # noqa: E402
from pipeline.crypto import basis, binance, bitview, checkonchain, coinbase, coinmetrics, etf, mstr, network_oi, urpd  # noqa: E402
from pipeline.crypto.build import build_dashboard  # noqa: E402
from pipeline.crypto.etf import apply_manual  # noqa: E402
from pipeline.csvio import read_csv  # noqa: E402
from pipeline.fred import today  # noqa: E402

RAW = ROOT / "data" / "raw" / "crypto"
OUT = ROOT / "data" / "crypto"
SERIES = OUT / "series"
STATUS = OUT / "status.json"
LOG = OUT / "state_log.csv"

# 和样稿对照的 2026-10-05。差一档以内算过，差得多就打印出来。
EXPECT = {
    "price": 85749,
    "sth": 73939,
    "lth_true": 78020,
    "tmmp": 77390,
    "mstr": 75441,
    "cash": 75500,
    "u10": 65360,
    "rp": 53734,
    "lth": 49439,
    "energy": 43385,
    "allin": 130800,
    "mvrv": 1.601,
    "nupl": 0.375,
    "share": 77.48,
}


def _run(errors: dict, key: str, fn):
    print(f"{key}…", flush=True)
    try:
        value, err = fn()
    except Exception as exc:  # noqa: BLE001
        errors[key] = f"{exc}"
        traceback.print_exc()
        return None
    errors[key] = err
    if err:
        print(f"  {key}：{err}", flush=True)
    return value


def _load_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _audit(dash: dict) -> None:
    """把 2026-10-05 能对上的数打出来。不因此让脚本失败。"""
    sections = {s["id"]: s for s in dash.get("sections") or []}
    price = sections.get("price") or {}
    print("—— 10-05 对照（页面用各自的真实日期，这里只核对样稿那天）——")
    for row in price.get("table") or []:
        print(f"  {row.get('name')}: {row.get('text')}  {row.get('gap')}  {row.get('date')}")
    for sec in dash.get("sections") or []:
        kpis = list(sec.get("kpis") or [])
        for g in sec.get("groups") or []:
            kpis.extend(g.get("kpis") or [])
        for k in kpis:
            if k.get("date", "").startswith("2026-10-05") or k.get("name") in {"全网 OI", "资金费率 当日", "季度基差"}:
                print(f"  {k.get('name')}: {k.get('text')} {k.get('unit')} @ {k.get('date')} {k.get('chg') or ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    day = today()
    errors: dict[str, str | None] = {}

    if not args.offline:
        _run(errors, "bitview", lambda: bitview.update(RAW / "bitview.csv"))
        _run(errors, "checkonchain", lambda: checkonchain.update(RAW / "lth_true.csv"))
        _run(errors, "coinbase", lambda: coinbase.update(RAW / "coinbase_btc.csv", day))
        _run(errors, "coinmetrics", lambda: coinmetrics.update_exchange(RAW / "coinmetrics_ex.csv"))
        _run(errors, "farside", lambda: etf.update(RAW / "etf_btc.csv", etf.BTC_URL))
        _run(errors, "funding", lambda: binance.update_funding(RAW / "binance_funding.csv", day))
        _run(errors, "metrics", lambda: binance.update_metrics_history(RAW / "binance_metrics.csv", day))
        _run(errors, "basis", lambda: basis.update(RAW / "basis.csv", day))
        _run(errors, "urpd", lambda: urpd.update(RAW / "urpd.json", (day - timedelta(days=1)).isoformat()))
        _run(errors, "oi_network", lambda: network_oi.update(RAW / "oi_network.csv"))
        seed = _load_json(OUT / "mstr_seed.json", {}).get("records") or []
        _run(errors, "mstr", lambda: mstr.update(RAW / "mstr.json", seed))

    bv = bitview.load(RAW / "bitview.csv", day)
    cm = coinmetrics.load_exchange(RAW / "coinmetrics_ex.csv")
    manual = read_csv(OUT / "manual.csv")
    etf_rows = apply_manual(read_csv(RAW / "etf_btc.csv"), manual, "etf_btc_usd_mn")
    metrics = binance.load_metrics(RAW / "binance_metrics.csv")
    miner = _load_json(OUT / "miner_costs.json", {})
    src = {
        "bitview": bv,
        "lth_true": checkonchain.load(RAW / "lth_true.csv"),
        "coinbase": coinbase.load(RAW / "coinbase_btc.csv"),
        "cm": cm,
        "etf": etf_rows,
        "funding": binance.load_funding(RAW / "binance_funding.csv"),
        "metrics": metrics,
        "basis": basis.load(RAW / "basis.csv"),
        "mstr": mstr.load(RAW / "mstr.json"),
        "miner": miner,
        "urpd": urpd.load(RAW / "urpd.json"),
        "oi_network": network_oi.load(RAW / "oi_network.csv"),
    }
    now = datetime.now(timezone.utc)
    dash, files = build_dashboard(src, day, now)

    def obs_date(s):
        return s[-1][0].isoformat() if s else None

    sources = [
        ("bitview", "bitview 日线", obs_date(bv.get("mvrv") or []), errors.get("bitview")),
        ("checkonchain", "CheckOnChain LTH 真实成本", obs_date(src["lth_true"]), errors.get("checkonchain")),
        ("coinbase", "Coinbase BTC-USD", obs_date(src["coinbase"]), errors.get("coinbase")),
        ("coinmetrics", "Coin Metrics 交易所", obs_date(src["cm"].get("SplyExNtv") or []), errors.get("coinmetrics")),
        ("farside", "Farside ETF", etf_rows[-1]["date"][:10] if etf_rows else None, errors.get("farside")),
        ("funding", "币安资金费率", (src["funding"][-1]["time"][:10] if src["funding"] else None), errors.get("funding")),
        ("metrics", "币安未平仓与多空比", obs_date(metrics.get("oi_usd") or []), errors.get("metrics")),
        ("basis", "季度基差", obs_date(src["basis"]), errors.get("basis")),
        ("urpd", "URPD", (src["urpd"] or {}).get("date"), errors.get("urpd")),
        ("mstr", "SEC 8-K MSTR", (src["mstr"][-1]["asof"] if src["mstr"] else None), errors.get("mstr")),
        ("oi_network", "全网未平仓（Coinfuty 快照）",
         (src["oi_network"][-1].get("fetched_at") if src["oi_network"] else None), errors.get("oi_network")),
        ("miner", "矿企成本（本地常量）", "2026Q2" if miner else None, None),
    ]
    status_sources = []
    for key, name, last_obs, err in sources:
        status_sources.append({
            "key": key, "name": name, "last_obs": last_obs, "ok": not err,
            "note": err or ("" if last_obs else "还没有数据"),
        })

    old = _load_json(STATUS, {})
    updated = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    updated_at = old.get("updated_at", updated) if args.offline else updated
    dash["status"] = {"updated_at": updated_at, "failed": [s["key"] for s in status_sources if not s["ok"]],
                      "sources": status_sources}

    current = [(d["key"], d["name"], d["label"], d["head"]) for d in dash["dimensions"]]
    current.append(("overall", "比特币", dash["verdict"]["name"], dash["verdict"]["headline"]))
    recent = [f"{s['name']} 到 {s['last_obs']}" for s in status_sources if s["last_obs"]]
    dash["state_log"] = statelog.update(
        LOG, dash["asof"], current, lambda _k: "；".join(recent[:6]), write=not args.offline)

    OUT.mkdir(parents=True, exist_ok=True)
    SERIES.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"updated_at": updated_at, "errors": errors}, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    (OUT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, separators=(",", ":")) + "\n",
                                        encoding="utf-8")
    keep = set()
    for spec in files.values():
        name = f"{spec['id']}.json"
        keep.add(name)
        (SERIES / name).write_text(json.dumps(spec, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    for old_file in SERIES.glob("*.json"):
        if old_file.name not in keep:
            old_file.unlink()

    print(f"图 {len(files)} 张；{dash['verdict']['headline']}")
    print(dash["verdict"]["synthesis"])
    _audit(dash)
    for s in status_sources:
        if not s["ok"]:
            print(f"失败 {s['name']}：{s['note']}")
    missing = dash.get("missing") or []
    if missing:
        print("未取到：" + "、".join(missing))
    return 0 if any(s["last_obs"] for s in status_sources) or args.offline else 1


if __name__ == "__main__":
    raise SystemExit(main())
