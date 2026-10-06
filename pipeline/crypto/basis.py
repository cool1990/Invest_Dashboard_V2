"""当季合约基差年化。币安 USD-M 季度合约日线（data.binance.vision）对同一所现货日线。

年化 = (期货收盘 / 现货收盘 − 1) × 365 / 剩余天数。
剩余天数从该日 K 线结束（次日 00:00 UTC）算到到期日 08:00 UTC。
每天只留剩余天数最短、且仍为正的那张合约。
"""

from __future__ import annotations

import calendar
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..csvio import read_csv, write_csv
from ..series import Series, clean
from .binance import fetch_klines
from .http import FetchError, get_bytes

VISION = "https://data.binance.vision/data/futures/um/monthly/klines"
DAILY = "https://data.binance.vision/data/futures/um/daily/klines"
FIELDS = ["date", "symbol", "future", "spot", "days", "ann_pct"]


def last_friday(year: int, month: int) -> date:
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def quarter_symbols(start_year: int, end_year: int) -> list[tuple[str, date]]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in (3, 6, 9, 12):
            exp = last_friday(y, m)
            out.append((f"BTCUSDT_{exp.strftime('%y%m%d')}", exp))
    return out


def parse_kline_csv(text: str) -> list[tuple[date, float]]:
    pts = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 5 or not parts[0][:1].isdigit():
            continue
        try:
            day = datetime.fromtimestamp(int(float(parts[0])) / 1000, timezone.utc).date()
            pts.append((day, float(parts[4])))
        except ValueError:
            continue
    return pts


def _zip_text(url: str) -> str | None:
    try:
        blob = get_bytes(url, retries=2, timeout=40)
    except FetchError:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8")
    except (zipfile.BadZipFile, IndexError, UnicodeError):
        return None


def _months(start: date, end: date) -> list[date]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fetch_future(symbol: str, expiry: date, today: date | None = None) -> list[tuple[date, float]]:
    """合约大约提前两个季度挂出来。月包只到上个完整月，当月改下日线包。"""
    begin = expiry - timedelta(days=200)
    today = today or datetime.now(timezone.utc).date()
    pts: list[tuple[date, float]] = []
    for month in _months(begin, expiry):
        stamp = f"{month.year:04d}-{month.month:02d}"
        url = f"{VISION}/{symbol}/1d/{symbol}-1d-{stamp}.zip"
        text = _zip_text(url)
        if text:
            pts.extend(parse_kline_csv(text))
    have = {d for d, _ in pts}
    end = min(expiry, today)
    cursor = (max(have) + timedelta(days=1)) if have else begin
    # 没有月包的旧合约不再逐日去猜。只补已经有月线、但月底之后还缺的日子。
    if have and cursor <= end:
        while cursor <= end:
            url = f"{DAILY}/{symbol}/1d/{symbol}-1d-{cursor.isoformat()}.zip"
            text = _zip_text(url)
            if text:
                pts.extend(parse_kline_csv(text))
            cursor += timedelta(days=1)
    return pts


def days_to_expiry(candle: date, expiry: date) -> float:
    delivery = datetime(expiry.year, expiry.month, expiry.day, 8, tzinfo=timezone.utc)
    close_ts = datetime(candle.year, candle.month, candle.day, tzinfo=timezone.utc) + timedelta(days=1)
    return (delivery - close_ts).total_seconds() / 86400


def build_series(futures: dict[str, tuple[date, list[tuple[date, float]]]], spot: Series) -> list[dict]:
    spot_map = dict(spot)
    # 每个 (date, symbol) 的年化，再按日选最近的一张
    chosen: dict[date, dict] = {}
    for symbol, (expiry, pts) in futures.items():
        for day, fut in pts:
            if day not in spot_map or spot_map[day] <= 0 or fut <= 0:
                continue
            left = days_to_expiry(day, expiry)
            if left <= 1:
                continue
            ann = (fut / spot_map[day] - 1) * 365 / left * 100
            row = {
                "date": day.isoformat(), "symbol": symbol,
                "future": f"{fut:.4f}", "spot": f"{spot_map[day]:.4f}",
                "days": f"{left:.4f}", "ann_pct": f"{ann:.4f}",
            }
            prev = chosen.get(day)
            if prev is None or left < float(prev["days"]):
                chosen[day] = row
    return [chosen[d] for d in sorted(chosen)]


def update(path: Path, today: date | None = None) -> tuple[list[dict], str | None]:
    today = today or datetime.now(timezone.utc).date()
    try:
        spot = fetch_klines("BTCUSDT", date(2019, 9, 1))
    except Exception as exc:  # noqa: BLE001
        return read_csv(path), f"现货日线：{exc}"
    if not spot:
        return read_csv(path), "没有币安现货日线，基差算不了"
    futures: dict[str, tuple[date, list[tuple[date, float]]]] = {}
    errors = []
    symbols = [
        (symbol, expiry) for symbol, expiry in quarter_symbols(2020, today.year + 1)
        if expiry >= date(2020, 6, 1) and expiry <= today + timedelta(days=200)
    ]
    print(f"  季度合约 {len(symbols)} 张", flush=True)
    for symbol, expiry in symbols:
        try:
            pts = fetch_future(symbol, expiry, today)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}:{exc}")
            continue
        if pts:
            futures[symbol] = (expiry, pts)
    rows = build_series(futures, spot)
    if not rows:
        return read_csv(path), "没有季度合约日线" + (("；" + "；".join(errors[:3])) if errors else "")
    write_csv(path, rows, FIELDS)
    return rows, None


def load(path: Path) -> Series:
    pts = []
    for row in read_csv(path):
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["ann_pct"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)


def load_rows(path: Path) -> list[dict]:
    return read_csv(path)
