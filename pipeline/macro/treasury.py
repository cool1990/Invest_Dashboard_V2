"""用美国财政部收益率曲线补 FRED 还没跟上的交易日。

DGS2、DGS10、DFII10 走美联储 H.15，FRED 经常比财政部晚一个交易日；
同一天的 T10Y2Y、T10Y3M、T10YIE 却已经更新。这里只把本地文件最后一天之后的
观测补上，不改 FRED 已有的历史。利差按同一天的两条曲线相减，四舍五入到 0.01，
和 FRED 的两位小数一致。
"""

from __future__ import annotations

import csv
import io
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from ..fred import read_series, write_series
from ..series import Series

_BASE = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type={kind}"
    "&field_tdr_date_value={year}&page&_format=csv"
)
NOMINAL = "daily_treasury_yield_curve"
REAL = "daily_treasury_real_yield_curve"

# FRED 序列 → (曲线, 列名)。名义和实际曲线的列名大小写不一样。
LEVELS: dict[str, tuple[str, str]] = {
    "DGS2": (NOMINAL, "2 Yr"),
    "DGS10": (NOMINAL, "10 Yr"),
    "DFII10": (REAL, "10 YR"),
}
# 利差 = 被减列 − 减列，两条曲线都在名义或一实一名上。
SPREADS: dict[str, tuple[str, str, str, str]] = {
    "T10Y2Y": (NOMINAL, "10 Yr", NOMINAL, "2 Yr"),
    "T10Y3M": (NOMINAL, "10 Yr", NOMINAL, "3 Mo"),
    "T10YIE": (NOMINAL, "10 Yr", REAL, "10 YR"),
}


def _url(kind: str, year: int) -> str:
    return _BASE.format(year=year, kind=kind)


def parse_curve(text: str) -> dict[str, Series]:
    """财政部 CSV：日期 MM/DD/YYYY，第一列是 Date，空值跳过。"""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header or header[0].strip().lower() != "date":
        raise ValueError(f"不是财政部收益率曲线：{text[:80]!r}")
    cols = [h.strip() for h in header]
    by: dict[str, list] = {name: [] for name in cols[1:]}
    for row in reader:
        if not row or not row[0].strip():
            continue
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        for i, name in enumerate(cols[1:], start=1):
            raw = row[i].strip() if i < len(row) else ""
            if raw in {"", ".", "N/A", "NA"}:
                continue
            try:
                by[name].append((day, float(raw)))
            except ValueError:
                continue
    return {name: sorted(points) for name, points in by.items() if points}


def _spread(left: Series, right: Series) -> Series:
    right_at = dict(right)
    out = []
    for day, value in left:
        other = right_at.get(day)
        if other is None:
            continue
        out.append((day, round(value - other, 2)))
    return out


def extend_series(existing: Series, newer: Series) -> tuple[Series, str | None]:
    """只追加比 existing 最后一天更晚的点。返回 (序列, 新的最后一天或 None)。"""
    if not existing or not newer:
        return existing, None
    last = existing[-1][0]
    extra = [(d, v) for d, v in newer if d > last]
    if not extra:
        return existing, None
    return existing + extra, extra[-1][0].isoformat()


def apply(raw_dir: Path, curves: dict[str, dict[str, Series]]) -> dict[str, str]:
    """把曲线写进 data/raw/fred。返回本次补上的序列 → 新的最后观测日。"""
    filled: dict[str, str] = {}
    for sid, (kind, col) in LEVELS.items():
        series = (curves.get(kind) or {}).get(col) or []
        path = raw_dir / f"{sid}.csv"
        merged, added = extend_series(read_series(path), series)
        if added:
            write_series(path, merged)
            filled[sid] = added
    for sid, (lk, lc, rk, rc) in SPREADS.items():
        left = (curves.get(lk) or {}).get(lc) or []
        right = (curves.get(rk) or {}).get(rc) or []
        path = raw_dir / f"{sid}.csv"
        merged, added = extend_series(read_series(path), _spread(left, right))
        if added:
            write_series(path, merged)
            filled[sid] = added
    return filled


def _download(kind: str, year: int) -> str:
    req = urllib.request.Request(
        _url(kind, year),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def update(raw_dir: Path, today=None) -> tuple[dict[str, str], str | None]:
    """下载当年的名义、实际曲线并补丁。失败时不改文件。"""
    year = (today or datetime.now().date()).year
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            curves = {
                NOMINAL: parse_curve(_download(NOMINAL, year)),
                REAL: parse_curve(_download(REAL, year)),
            }
            if "10 Yr" not in curves[NOMINAL] or "10 YR" not in curves[REAL]:
                raise ValueError("财政部曲线缺 10 年期列")
            return apply(raw_dir, curves), None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    return {}, str(last)[:200]
