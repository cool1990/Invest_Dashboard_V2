"""bitview URPD：2000 美元一档，只去掉成本低于 1000 美元的早期币，取供给最大的一档。

最新一期就够画横带，不补历史。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .http import FetchError, get_json

UA = {"User-Agent": "Mozilla/5.0 (InvestDashboard; bitview urpd)"}
URL = "https://bitview.space/api/urpd/all"


def band(payload: dict) -> dict:
    """返回 lo/hi/supply/date/close。空桶或没有收盘价时抛错。"""
    close = float(payload.get("close") or 0)
    buckets = payload.get("buckets") or []
    if not buckets or not close:
        raise ValueError("空的 URPD")
    windows: dict[int, float] = defaultdict(float)
    for b in buckets:
        pf = float(b.get("price_floor") or 0)
        if pf < 1000:
            continue
        windows[int(pf // 2000) * 2000] += float(b.get("supply") or 0)
    if not windows:
        raise ValueError("剔除早期币后没有档")
    lo, supply = max(windows.items(), key=lambda kv: kv[1])
    return {
        "lo": lo, "hi": lo + 2000, "supply": supply,
        "date": str(payload.get("date") or "")[:10], "close": close,
    }


def _get(day: str | None) -> dict | None:
    url = URL if not day else f"{URL}/{day}"
    try:
        return get_json(url, headers=UA, timeout=90)
    except FetchError as exc:
        if "404" in str(exc):
            return None
        raise


def fetch(day: str) -> dict:
    """取 day 这一期；404 时退回最新一期，但最新一期晚于 day 则不用。"""
    payload = _get(day)
    if payload is None:
        payload = _get(None)
        if not payload:
            raise FetchError("URPD 没有数据")
        got = str(payload.get("date") or "")[:10]
        if got > day:
            raise FetchError(f"URPD {day} 不存在，最新是 {got}")
    out = band(payload)
    if not out["date"]:
        out["date"] = day
    prev_day = (date.fromisoformat(out["date"]) - timedelta(days=1)).isoformat()
    try:
        prev = _get(prev_day)
        if prev:
            pb = band(prev)
            out["prev_lo"] = pb["lo"]
            out["prev_hi"] = pb["hi"]
            out["prev_supply"] = pb["supply"]
            out["prev_date"] = pb["date"] or prev_day
    except (FetchError, ValueError):
        pass
    return out


def update(path: Path, day: str) -> tuple[dict | None, str | None]:
    try:
        got = fetch(day)
    except Exception as exc:  # noqa: BLE001
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return old, str(exc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return got, None


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
