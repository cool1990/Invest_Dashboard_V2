"""ICE 美元指数（DXY）。

日报的主源是 TradingView TVC:DXY，回退 Yahoo Finance（DX-Y.NYB）和 CNBC。
这里用 Yahoo 的日线：同一只指数，能取到 2006 年以来的收盘，也接得进现有的 CSV 管道。
最近一个交易日若报价时间就落在这根 K 线上，用官方报价（regularMarketPrice）。
Yahoo 日线的 close 有时比现金指数收盘晚一截；这个报价与 TVC:DXY、CNBC 的 last 一致。
没有收盘的日期不写入，不沿用前值。
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..fred import read_series, write_series
from ..series import Series

SYMBOL = "DX-Y.NYB"
# 2006-01-01，和原来广义指数图的起点对齐；更早的年份这条管道用不到。
PERIOD1 = 1136073600
URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + SYMBOL
    + "?interval=1d&period1={start}&period2={end}"
)
_NY = ZoneInfo("America/New_York")


def _ny_date(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), _NY)


def parse_chart(payload: dict) -> Series:
    """日线收盘。close 为空的日子跳过；官方报价只覆盖已经有 K 线的那一天。"""
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"])[:200])
    result = (chart.get("result") or [None])[0]
    if not result:
        raise ValueError("Yahoo 没有返回 DXY")
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = quote.get("close") or []
    by: dict = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        by[_ny_date(ts).date()] = round(float(close), 4)
    meta = result.get("meta") or {}
    price, stamp = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if price is not None and stamp:
        day = _ny_date(stamp).date()
        if day in by:
            by[day] = round(float(price), 4)
    if not by:
        raise ValueError("没有有效收盘")
    return sorted(by.items())


def _download() -> Series:
    end = int(datetime.now(timezone.utc).timestamp()) + 2 * 86400
    req = urllib.request.Request(
        URL.format(start=PERIOD1, end=end),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return parse_chart(payload)


def read(path) -> Series:
    return read_series(path)


def update(path) -> tuple[Series, str | None]:
    """下载并覆盖本地文件。失败时沿用上次的文件，返回错误说明。"""
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            series = _download()
            write_series(path, series)
            return series, None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    return read(path), str(last)[:200]
