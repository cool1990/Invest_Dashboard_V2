"""bitview.space 日线。一个快照里价格、成本、盈利、供给、SOPR、算力对齐。

`start=0` 从第一条（约 2009-01）拉到最新。最后一天常常是当天未收盘的数，
读的时候丢掉「日期 ≥ 今天（UTC）」的行，不把它当成收盘。
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path

from ..csvio import read_csv, write_csv
from ..series import Series, clean
from .http import get_json

ROOT = "https://bitview.space/api/series/{slug}/day1?start={start}"
UA = {"User-Agent": "Mozilla/5.0 (InvestDashboard; bitview day1)"}

# 页面用到的序列。date 用来对齐，不写进列。
SLUGS = (
    "price_close",
    "sth_realized_price",
    "lth_realized_price",
    "true_market_mean",
    "realized_price",
    "utxos_under_10y_old_realized_price",
    "mvrv",
    "sth_mvrv",
    "lth_mvrv",
    "nupl",
    "supply_in_profit_share",
    "lth_supply",
    "sth_supply",
    "lth_sopr_24h",
    "sth_sopr_24h",
    "coindays_destroyed_sum_24h",
    "dormancy_24h",
    "hash_rate",
    "hash_rate_sma_1w",
    "puell_multiple",
)


def fetch_raw(slug: str, start: int = 0) -> dict:
    return get_json(ROOT.format(slug=slug, start=start), headers=UA, timeout=90)


def align(dates_payload: dict, values_payload: dict) -> dict[str, float]:
    """按绝对下标对齐。两边的 start 可以不同。None 和 NaN 丢掉。"""
    dmap: dict[int, str] = {}
    base = int(dates_payload.get("start") or 0)
    for i, d in enumerate(dates_payload.get("data") or []):
        if d:
            dmap[base + i] = str(d)[:10]
    out: dict[str, float] = {}
    vbase = int(values_payload.get("start") or 0)
    for i, v in enumerate(values_payload.get("data") or []):
        day = dmap.get(vbase + i)
        if not day or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            continue
        out[day] = float(v)
    return out


def rows_from_payloads(dates_payload: dict, series: dict[str, dict]) -> list[dict]:
    by_day: dict[str, dict] = {}
    for slug, payload in series.items():
        for day, val in align(dates_payload, payload).items():
            by_day.setdefault(day, {"date": day})[slug] = f"{val:.8g}"
    return [by_day[d] for d in sorted(by_day)]


def update(path: Path) -> tuple[list[dict], str | None]:
    try:
        dates = fetch_raw("date", 0)
        series = {slug: fetch_raw(slug, 0) for slug in SLUGS}
        rows = rows_from_payloads(dates, series)
    except Exception as exc:  # noqa: BLE001
        return read_csv(path), str(exc)
    if not rows:
        return read_csv(path), "bitview 没有数据"
    write_csv(path, rows, ["date", *SLUGS])
    stamp = dates.get("stamp") or ""
    return rows, None if stamp else None


def _closed(rows: list[dict], today: date | None) -> list[dict]:
    if today is None:
        today = datetime.now(timezone.utc).date()
    return [r for r in rows if r.get("date") and r["date"][:10] < today.isoformat()]


def load(path: Path, today: date | None = None) -> dict[str, Series]:
    """每个 slug 一条已收盘序列。当天未收盘的行不返回。"""
    out: dict[str, list] = {slug: [] for slug in SLUGS}
    for row in _closed(read_csv(path), today):
        try:
            day = date.fromisoformat(row["date"][:10])
        except ValueError:
            continue
        for slug in SLUGS:
            raw = row.get(slug)
            if raw in (None, ""):
                continue
            try:
                out[slug].append((day, float(raw)))
            except ValueError:
                continue
    return {slug: clean(pts) for slug, pts in out.items()}


def load_stamp_note(rows_n: int, first: str | None, last: str | None) -> str:
    return f"bitview 日线 {rows_n} 行，{first or '—'} 至 {last or '—'}"
