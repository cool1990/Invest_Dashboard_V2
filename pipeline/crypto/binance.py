"""币安 BTCUSDT 永续的资金费率和未平仓，以及现货日线（算以太坊/比特币）。

fapi.binance.com 在部分网络（包括美国的 GitHub Actions）会回 451。
费率改读 data.binance.vision 的月度压缩包，未平仓改读每日 metrics 压缩包，
现货日线改读 data-api.binance.vision。都是币安同一本 BTCUSDT / ETHUSDT 的公开数据。
"""

from __future__ import annotations

import csv
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from ..series import Series, clean
from .http import FetchError, get_bytes, get_json

VISION = "https://data.binance.vision/data/futures/um"
SPOT = "https://data-api.binance.vision/api/v3/klines"
FAPI = "https://fapi.binance.com"
# fapi.binance.com 在部分网络回 451。www.binance.com 上的同一路径仍是公开的资金费率。
FUND_HOSTS = (FAPI, "https://www.binance.com")
FUND_START = date(2019, 9, 1)
OI_BACKFILL_DAYS = 180
WORKERS = 6


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_funding_csv(text: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_t, raw_r = row.get("calc_time"), row.get("last_funding_rate")
        if not raw_t or raw_r in (None, ""):
            continue
        try:
            stamp = _utc(int(float(raw_t))).strftime("%Y-%m-%dT%H:%M:%SZ")
            rate = float(raw_r)
        except ValueError:
            continue
        out.append({"time": stamp, "rate": f"{rate:.8f}"})
    return out


def _f(row: dict, key: str) -> float | None:
    raw = row.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_metrics_csv(text: str) -> dict | None:
    """每日 metrics 里取最后一行。时间原样留下，不改成收盘日。"""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return None
    last = rows[-1]
    try:
        out = {
            "date": last["create_time"][:10],
            "time": last["create_time"],
            "oi_btc": float(last["sum_open_interest"]),
            "oi_usd": float(last["sum_open_interest_value"]),
            "ls_ratio": float(last["count_long_short_ratio"]),
        }
    except (KeyError, ValueError):
        return None
    top_pos = _f(last, "sum_toptrader_long_short_ratio")
    top_acc = _f(last, "count_toptrader_long_short_ratio")
    if top_pos is not None:
        out["ls_top_position"] = top_pos
    if top_acc is not None:
        out["ls_top_account"] = top_acc
    # 同一天第一行，用来算当天持仓变化（不是插值）。
    first = rows[0]
    oi0 = _f(first, "sum_open_interest_value")
    if oi0 is not None:
        out["oi_usd_open"] = oi0
        out["time_open"] = first.get("create_time") or ""
    return out


def annualize_funding(rate: float) -> float:
    """8 小时资金费率（小数，0.0001 = 0.01%）换成年化百分数。一年按 3×365 次。"""
    return rate * 3 * 365 * 100


def funding_annualized(rows: list[dict]) -> tuple[Series, Series]:
    """每个 UTC 日：当天各次结算的平均年化，以及含当天在内往前 7 个日历日的结算平均年化。

    某一天没有结算就不产这个点，也不用别的天去填。
    """
    by_day: dict[date, list[float]] = {}
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            by_day.setdefault(stamp.date(), []).append(float(row["rate"]))
        except (KeyError, ValueError):
            continue
    daily: Series = []
    for d in sorted(by_day):
        vals = by_day[d]
        daily.append((d, annualize_funding(sum(vals) / len(vals))))
    week: Series = []
    days = sorted(by_day)
    for d in days:
        bucket: list[float] = []
        for prev in days:
            if d - timedelta(days=6) <= prev <= d:
                bucket.extend(by_day[prev])
        if bucket:
            week.append((d, annualize_funding(sum(bucket) / len(bucket))))
    return daily, week


def parse_klines(payload) -> Series:
    pts = []
    for row in payload or []:
        try:
            pts.append((_utc(int(row[0])).date(), float(row[4])))
        except (TypeError, ValueError, IndexError):
            continue
    return clean(pts)


def mean_recent(rows: list[dict], days: int = 7) -> float | None:
    """最近 days 天的 8 小时费率均值，返回百分数（0.01 表示 0.01%）。"""
    pts = []
    for row in rows:
        try:
            pts.append((datetime.fromisoformat(row["time"].replace("Z", "+00:00")), float(row["rate"])))
        except (KeyError, ValueError):
            continue
    if not pts:
        return None
    last = max(t for t, _ in pts)
    cutoff = last - timedelta(days=days)
    vals = [rate for t, rate in pts if cutoff < t <= last]
    if not vals:
        return None
    return sum(vals) / len(vals) * 100


def _zip_text(url: str) -> str | None:
    try:
        blob = get_bytes(url, retries=2, timeout=40)
    except FetchError:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = zf.namelist()[0]
            return zf.read(name).decode("utf-8")
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


def _funding_month_url(day: date) -> str:
    stamp = f"{day.year:04d}-{day.month:02d}"
    return f"{VISION}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{stamp}.zip"


def _funding_day_url(day: date) -> str:
    stamp = day.isoformat()
    return f"{VISION}/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{stamp}.zip"


def fetch_funding_vision(today: date, have: set[str] | None = None) -> list[dict]:
    """已结束的月份用月包；当月用日包。404 的月份跳过。本地已经有的旧月份不再重复下载。"""
    have = have or set()
    months = _months(FUND_START, today.replace(day=1) - timedelta(days=1))
    refresh_after = today.replace(day=1) - timedelta(days=40)
    needed = [m for m in months if m >= refresh_after or not any(t.startswith(f"{m.year:04d}-{m.month:02d}") for t in have)]
    rows: list[dict] = []
    if needed:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for text in pool.map(_zip_text, (_funding_month_url(d) for d in needed)):
                if text:
                    rows.extend(parse_funding_csv(text))
    daily = []
    d = today.replace(day=1)
    while d <= today:
        if not any(t.startswith(d.isoformat()) for t in have):
            daily.append(d)
        d += timedelta(days=1)
    if daily:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for text in pool.map(_zip_text, (_funding_day_url(d) for d in daily)):
                if text:
                    rows.extend(parse_funding_csv(text))
    return rows


def _funding_pages(host: str, earliest_ms: int) -> list[dict]:
    """按 endTime 往回翻。接口有时把 limit=1000 截成 500，所以不用条数判断是否到头。"""
    rows: list[dict] = []
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    while end > earliest_ms:
        payload = get_json(f"{host}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&endTime={end}")
        if not isinstance(payload, list) or not payload:
            break
        times: list[int] = []
        batch: list[dict] = []
        for row in payload:
            try:
                ms = int(float(row["fundingTime"]))
                times.append(ms)
                if ms < earliest_ms:
                    continue
                stamp = _utc(ms).strftime("%Y-%m-%dT%H:%M:%SZ")
                batch.append({"time": stamp, "rate": f"{float(row['fundingRate']):.8f}"})
            except (KeyError, TypeError, ValueError):
                continue
        if not times:
            break
        rows.extend(batch)
        first = min(times)
        if first >= end or first <= earliest_ms:
            break
        end = first - 1
    return rows


def fetch_funding_since(start: datetime) -> tuple[list[dict], str | None]:
    """补月包还没覆盖的结算。fapi 被 451 时换 www.binance.com 上的同一接口。"""
    earliest = int(start.timestamp() * 1000)
    errors: list[str] = []
    for host in FUND_HOSTS:
        try:
            rows = _funding_pages(host, earliest)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}：{exc}")
            continue
        if rows:
            return rows, None
        errors.append(f"{host}：空")
    return [], "；".join(errors)


def update_funding(path: Path, today: date) -> tuple[list[dict], str | None]:
    note = None
    have_rows = read_csv(path)
    have = {r["time"] for r in have_rows if r.get("time")}
    try:
        fresh = fetch_funding_vision(today, have)
    except Exception as exc:  # noqa: BLE001
        fresh = []
        note = f"vision：{exc}"
    merged = merge(have_rows, fresh, lambda r: (r["time"],))
    latest = max((r["time"] for r in merged), default="")
    # 月包不含当月，日包这个品种也不提供。缺口用公开 fundingRate 补到昨天。
    cutoff = (today - timedelta(days=1)).isoformat()
    if not latest or latest[:10] < cutoff:
        start_s = latest[:10] if latest else FUND_START.isoformat()
        start = datetime.fromisoformat(start_s).replace(tzinfo=timezone.utc) - timedelta(days=1)
        extra, err = fetch_funding_since(start)
        if extra:
            merged = merge(merged, extra, lambda r: (r["time"],))
        else:
            note = err or note
            try:
                from .bgeometrics import fetch_funding
                bg = fetch_funding(start_s)
                if bg:
                    merged = merge(merged, bg, lambda r: (r["time"],))
            except Exception as exc:  # noqa: BLE001
                note = f"{note or ''}；当月费率没补上：{exc}".strip("；")
    if not merged:
        return [], note or "没有资金费率"
    latest = max(r["time"] for r in merged)
    if latest[:10] < cutoff:
        note = note or f"资金费率只到 {latest[:10]}"
    else:
        note = None
    write_csv(path, merged, ["time", "rate"])
    return merged, note


def _metrics_url(day: date) -> str:
    stamp = day.isoformat()
    return f"{VISION}/daily/metrics/BTCUSDT/BTCUSDT-metrics-{stamp}.zip"


def fetch_oi_days(days: list[date]) -> list[dict]:
    found = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for text in pool.map(_zip_text, (_metrics_url(d) for d in days)):
            if not text:
                continue
            row = parse_metrics_csv(text)
            if row:
                found.append({
                    "date": row["date"],
                    "oi_btc": f"{row['oi_btc']:.4f}",
                    "oi_usd": f"{row['oi_usd']:.4f}",
                    "ls_ratio": f"{row['ls_ratio']:.6f}",
                })
    return found


def update_oi(path: Path, today: date) -> tuple[list[dict], str | None]:
    old = read_csv(path)
    have = {r["date"] for r in old}
    start = today - timedelta(days=OI_BACKFILL_DAYS if len(old) < 30 else 14)
    days = []
    d = start
    while d <= today:
        if d.isoformat() not in have:
            days.append(d)
        d += timedelta(days=1)
    fresh = fetch_oi_days(days) if days else []
    merged = merge(old, fresh, lambda r: (r["date"],))
    if merged:
        write_csv(path, merged, ["date", "oi_btc", "oi_usd", "ls_ratio"])
    if not merged:
        return [], "没有未平仓"
    return merged, None if fresh or old else "没有未平仓"


def fetch_klines(symbol: str, start: date) -> Series:
    pts: Series = []
    cursor = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cursor < end:
        payload = get_json(f"{SPOT}?symbol={symbol}&interval=1d&limit=1000&startTime={cursor}")
        batch = parse_klines(payload)
        if not batch:
            break
        pts.extend(batch)
        last_ms = int(payload[-1][0])
        nxt = last_ms + 86_400_000
        if nxt <= cursor or len(payload) < 1000:
            break
        cursor = nxt
    return clean(pts)


def update_klines(path: Path, symbol: str, start: date) -> tuple[Series, str | None]:
    try:
        pts = fetch_klines(symbol, start)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not pts:
        return [], "没有日线"
    write_csv(path, [{"date": d.isoformat(), "close": v} for d, v in pts], ["date", "close"])
    return pts, None


def load_funding(path: Path) -> list[dict]:
    return read_csv(path)


def load_oi(path: Path) -> tuple[Series, Series]:
    """未平仓名义美元、全账户多空比。"""
    oi, ls = [], []
    for row in read_csv(path):
        try:
            d = date.fromisoformat(row["date"][:10])
            oi.append((d, float(row["oi_usd"])))
            ls.append((d, float(row["ls_ratio"])))
        except (KeyError, ValueError):
            continue
    return clean(oi), clean(ls)


METRIC_FIELDS = ["date", "time", "oi_btc", "oi_usd", "oi_usd_open", "time_open",
                 "ls_ratio", "ls_top_position", "ls_top_account"]


def _metrics_exists(day: date) -> bool:
    return _zip_text(_metrics_url(day)) is not None


def _first_metrics_day(today: date) -> date | None:
    """按月二分，找到最早有 metrics 日包的那个月的 1 号附近。"""
    lo = date(2020, 1, 1)
    hi = today.replace(day=1)
    found: date | None = None
    while lo <= hi:
        mid_ord = (lo.toordinal() + hi.toordinal()) // 2
        mid = date.fromordinal(mid_ord).replace(day=1)
        if _metrics_exists(mid):
            found = mid
            hi = mid - timedelta(days=1)
            hi = hi.replace(day=1)
        else:
            nxt = mid + timedelta(days=32)
            lo = nxt.replace(day=1)
        if lo > today:
            break
    if found is None:
        return None
    # 月初那天可能还没有文件（序列从上旬中间开始）。再往前看最多 45 天。
    d = found
    for _ in range(45):
        prev = d - timedelta(days=1)
        if prev.year < 2019 or not _metrics_exists(prev):
            break
        d = prev
    return d


def update_metrics_history(path: Path, today: date) -> tuple[list[dict], str | None]:
    """币安 BTCUSDT 永续的未平仓和大户多空比。日包能回溯到接口上线的那一天，之后每天只补新的。"""
    old = read_csv(path)
    have = {r["date"] for r in old if r.get("date")}
    if len(have) < 30:
        start = _first_metrics_day(today)
        if start is None:
            start = today - timedelta(days=OI_BACKFILL_DAYS)
    else:
        start = today - timedelta(days=5)
    days = []
    d = start
    while d <= today:
        if d.isoformat() not in have:
            days.append(d)
        d += timedelta(days=1)
    fresh_rows = []
    if days:
        print(f"  币安 metrics 补 {len(days)} 天（从 {days[0]}）", flush=True)
        texts = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            texts = list(pool.map(_zip_text, (_metrics_url(day) for day in days)))
        for text in texts:
            if not text:
                continue
            row = parse_metrics_csv(text)
            if not row:
                continue
            fresh_rows.append({
                "date": row["date"],
                "time": row.get("time") or "",
                "oi_btc": f"{row['oi_btc']:.4f}",
                "oi_usd": f"{row['oi_usd']:.4f}",
                "oi_usd_open": "" if row.get("oi_usd_open") is None else f"{row['oi_usd_open']:.4f}",
                "time_open": row.get("time_open") or "",
                "ls_ratio": f"{row['ls_ratio']:.6f}",
                "ls_top_position": "" if row.get("ls_top_position") is None else f"{row['ls_top_position']:.6f}",
                "ls_top_account": "" if row.get("ls_top_account") is None else f"{row['ls_top_account']:.6f}",
            })
    merged = merge(old, fresh_rows, lambda r: (r["date"],))
    if not merged:
        return [], "没有币安未平仓日包"
    write_csv(path, merged, METRIC_FIELDS)
    # 今天的包经常还没有，只要历史在就不算失败
    return merged, None


def load_metrics(path: Path) -> dict[str, Series]:
    keys = ("oi_usd", "ls_ratio", "ls_top_position", "ls_top_account")
    out: dict[str, list] = {k: [] for k in keys}
    times: dict[str, str] = {}
    for row in read_csv(path):
        try:
            d = date.fromisoformat(row["date"][:10])
        except (KeyError, ValueError):
            continue
        if row.get("time"):
            times[row["date"][:10]] = row["time"]
        for k in keys:
            raw = row.get(k)
            if raw in (None, ""):
                continue
            try:
                out[k].append((d, float(raw)))
            except ValueError:
                continue
    return {k: clean(v) for k, v in out.items()} | {"time": times}


def load_close(path: Path) -> Series:
    pts = []
    for row in read_csv(path):
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["close"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)
