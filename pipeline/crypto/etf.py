"""美国现货 ETF 净流入，来自 Farside 公开页。

比特币：https://farside.co.uk/btc/ ；以太坊：https://farside.co.uk/eth/ 。
表内单位是百万美元，括号表示流出。合计列是每一行最后一个数。
页面改版或被拦时解析会得到空表，不覆盖已有文件；可以改 data/crypto/manual.csv。
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from html import unescape
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .http import get_bytes

# /btc/ 和 /eth/ 只放最近两三周。20 个交易日的合计要用全历史页。
BTC_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
ETH_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_TABLE = re.compile(r'<table class="etf">(.*?)</table>', re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"^(\d{1,2}) ([A-Za-z]{3}) (\d{4})$")
FIELDS = ["date", "total_usd_mn"]


def _num(text: str) -> float | None:
    s = text.strip()
    if s in {"", "-", "—", "–"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


def _cell_text(html: str) -> str:
    return unescape(_TAG.sub("", html)).replace("\xa0", " ").strip()


def _headers(table_html: str) -> list[str]:
    """第一行表头。空的、Date 丢掉名字，其余保留（IBIT、Total …）。"""
    rows = _ROW.findall(table_html)
    if not rows:
        return []
    cells = [_cell_text(c) for c in _CELL.findall(rows[0])]
    return cells


def parse_html(html: str) -> list[dict]:
    """每日合计，以及各发行商列（有的话）。日期不是「11 Jan 2024」的行丢掉。"""
    found = _TABLE.search(html)
    if not found:
        return []
    table = found.group(1)
    headers = _headers(table)
    total_idx = None
    for i, name in enumerate(headers):
        if name.strip().lower() == "total":
            total_idx = i
    out = []
    for row_html in _ROW.findall(table):
        cells = [_cell_text(c) for c in _CELL.findall(row_html)]
        if not cells or not _DATE.match(cells[0]):
            continue
        idx = total_idx if total_idx is not None and total_idx < len(cells) else len(cells) - 1
        total = _num(cells[idx])
        if total is None:
            continue
        try:
            day = datetime.strptime(cells[0], "%d %b %Y").date()
        except ValueError:
            continue
        row = {"date": day.isoformat(), "total_usd_mn": f"{total:.4f}"}
        for i, name in enumerate(headers):
            if i == 0 or i == idx or i >= len(cells):
                continue
            key = name.strip()
            if not key or key.lower() in {"date", "total", "fee"}:
                continue
            val = _num(cells[i])
            if val is None:
                continue
            row[key] = f"{val:.4f}"
        out.append(row)
    return out


def fields_of(rows: list[dict]) -> list[str]:
    extra: list[str] = []
    for row in rows:
        for key in row:
            if key not in FIELDS and key not in extra:
                extra.append(key)
    return [*FIELDS, *extra]


def _load(path: Path) -> list[dict]:
    return read_csv(path)


def update(path: Path, url: str) -> tuple[list[dict], str | None]:
    try:
        html = get_bytes(url, headers=HEADERS, timeout=60).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return _load(path), str(exc)
    rows = parse_html(html)
    if not rows:
        return _load(path), "页面里没有 ETF 表"
    merged = merge(_load(path), rows, lambda r: (r["date"],))
    write_csv(path, merged, fields_of(merged))
    return merged, None


def load(path: Path) -> list[tuple]:
    from datetime import date

    from ..series import clean
    if not path.exists():
        return []
    pts = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                pts.append((date.fromisoformat(row["date"][:10]), float(row["total_usd_mn"])))
            except (KeyError, ValueError):
                continue
    return clean(pts)


def apply_manual(series_rows: list[dict], manual: list[dict], key: str) -> list[dict]:
    """手工行只覆盖同一天的合计，发行商列留着。value 单位同样是百万美元。"""
    by_date = {r["date"]: dict(r) for r in series_rows if r.get("date")}
    for row in manual:
        if row.get("key") != key or not row.get("date") or row.get("value") in (None, ""):
            continue
        try:
            total = f"{float(row['value']):.4f}"
        except ValueError:
            continue
        day = row["date"][:10]
        got = by_date.get(day, {"date": day})
        got["total_usd_mn"] = total
        by_date[day] = got
    return [by_date[d] for d in sorted(by_date)]
