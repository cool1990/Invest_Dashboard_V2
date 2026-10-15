"""Zillow ZORI（全国观察租金指数）。

Zillow Research 的公开 CSV，不需要 key。全国一行，月份列是月末日期；
存进本仓库时改成月初，和 FRED 的 CPI 用同一套月度日期。
指数是美元计价的典型租金（平滑、按租赁存量加权），同比和环比在 build.py 里算。
Apartment List 没有同样稳定的公开文件地址，这里不用。
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from datetime import date
from pathlib import Path

from ..fred import read_series, write_series
from ..series import Series

URL = "https://files.zillowstatic.com/research/public_csvs/zori/National_zori_uc_sfrcondomfr_sm_month.csv"
_MONTH = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_wide(text: str) -> Series:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if len(rows) < 2:
        raise ValueError("ZORI 文件是空的")
    header, body = rows[0], rows[1:]
    chosen = next((r for r in body if len(r) > 3 and r[2].strip() == "United States"), None)
    if chosen is None:
        raise ValueError("没有美国全国这一行")
    out: Series = []
    for i, name in enumerate(header):
        if not _MONTH.fullmatch(name.strip()) or i >= len(chosen):
            continue
        raw = chosen[i].strip()
        if raw in {"", ".", "NA", "ND"}:
            continue
        y, m, _ = name.split("-")
        out.append((date(int(y), int(m), 1), float(raw)))
    out.sort()
    if not out:
        raise ValueError("没有有效月份")
    return out


def read(path: Path) -> Series:
    return read_series(path)


def update(path: Path) -> tuple[Series, str | None]:
    """下载并覆盖本地文件。失败时沿用上次的文件，返回错误说明。"""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
        series = parse_wide(text)
        write_series(path, series)
        return series, None
    except Exception as exc:  # noqa: BLE001
        return read(path), str(exc)[:200]
