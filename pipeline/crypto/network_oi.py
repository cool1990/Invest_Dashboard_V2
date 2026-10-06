"""全网未平仓。Coinfuty 公开页只有当前值，按 UTC 日追加，不回补历史。

数值带抓取时间。页面上要写成实时，不能写成某个收盘日。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .http import get_bytes

URL = "https://www.coinfuty.com/bitcoin-open-interest-market-cap-ratio"
RX = re.compile(r"Today\s+([\d.]+)%\s*\(\$([\d.]+)\s*B\s+open interest\)", re.I)
FIELDS = ["date", "fetched_at", "oi_usd_b", "oi_mcap_pct"]


def parse(html: str) -> tuple[float, float]:
    m = RX.search(html)
    if not m:
        raise ValueError("页面上没有全网未平仓")
    return float(m.group(2)), float(m.group(1))


def update(path: Path, now: datetime | None = None) -> tuple[list[dict], str | None]:
    now = now or datetime.now(timezone.utc)
    try:
        html = get_bytes(URL, headers={"User-Agent": "Mozilla/5.0 (InvestDashboard)"}, timeout=45).decode("utf-8", "replace")
        oi_b, pct = parse(html)
    except Exception as exc:  # noqa: BLE001
        return read_csv(path), str(exc)
    row = {
        "date": now.date().isoformat(),
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oi_usd_b": f"{oi_b:.4f}",
        "oi_mcap_pct": f"{pct:.4f}",
    }
    rows = merge(read_csv(path), [row], lambda r: (r.get("date") or "",))
    write_csv(path, rows, FIELDS)
    return rows, None


def load(path: Path) -> list[dict]:
    return read_csv(path)
