"""Coinbase Exchange 的 BTC-USD 日线收盘。公开接口，不用 key。

K 线时间是该日 00:00 UTC。当天这根还没收盘，丢掉。
更早的年份 Coinbase 没有，调用方用 bitview 的 price_close 接在上市日之前，重叠的日子只用 Coinbase。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..csvio import write_csv
from ..series import Series, clean
from .http import get_json

URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
# 单次最多约 300 根。
WINDOW = 280


def fetch(start: date | None = None, today: date | None = None) -> Series:
    start = start or date(2015, 1, 1)
    today = today or datetime.now(timezone.utc).date()
    pts: list[tuple[date, float]] = []
    cursor = start
    while cursor < today:
        end = min(cursor + timedelta(days=WINDOW), today + timedelta(days=1))
        q = (
            f"{URL}?granularity=86400"
            f"&start={cursor.isoformat()}T00:00:00Z&end={end.isoformat()}T00:00:00Z"
        )
        payload = get_json(q, headers={"User-Agent": "InvestDashboard", "Accept": "application/json"}, timeout=60)
        if not isinstance(payload, list) or not payload:
            break
        for row in payload:
            try:
                day = datetime.fromtimestamp(int(row[0]), timezone.utc).date()
                close = float(row[4])
            except (TypeError, ValueError, IndexError):
                continue
            if day >= today:
                continue
            pts.append((day, close))
        nxt = end
        if nxt <= cursor:
            break
        cursor = nxt
    return clean(pts)


def update(path: Path, today: date | None = None) -> tuple[Series, str | None]:
    try:
        pts = fetch(today=today)
    except Exception as exc:  # noqa: BLE001
        return load(path), str(exc)
    if not pts:
        return load(path), "Coinbase 没有日线"
    write_csv(path, [{"date": d.isoformat(), "close": f"{v:.2f}"} for d, v in pts], ["date", "close"])
    return pts, None


def load(path: Path) -> Series:
    pts = []
    if not path.exists():
        return []
    from ..csvio import read_csv
    for row in read_csv(path):
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["close"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)
