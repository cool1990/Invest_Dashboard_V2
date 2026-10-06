"""CheckOnChain「LTH True Realised Price」。

图表每天重算一次。生成日当天那个点只含几小时，丢掉；更早的日子是收盘值。
失败时不拿标准 LTH 成本冒充这一条，调用方自己决定要不要另注。
"""

from __future__ import annotations

import base64
import email.utils
import json
import math
import struct
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from ..csvio import read_csv, write_csv
from ..series import Series, clean

CDN = "https://charts-cdn.checkonchain.com/btconchain/realised/lthsopr_indicator/lthsopr_indicator_light.html"
PAGE = "https://charts.checkonchain.com/btconchain/realised/lthsopr_indicator/lthsopr_indicator_light.html"
ALT = "https://charts-cdn.checkonchain.com/btconchain/unrealised/mvrv_lth/mvrv_lth_light.html"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
NAME_RX_TEXT = ("lth", "true", "reali")
DTYPES = {
    "f8": "d", "f4": "f", "i1": "b", "u1": "B", "i2": "h", "u2": "H", "i4": "i", "u4": "I",
    "i8": "q", "u8": "Q", "float64": "d", "float32": "f",
}


def fetch_html() -> tuple[str, str | None, str]:
    import re
    errs = []
    for url in (CDN, PAGE, ALT):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as resp:
                html = resp.read().decode("utf-8", "replace")
                lm = resp.headers.get("Last-Modified")
                final = resp.geturl()
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{url}: {exc}")
            continue
        if "Plotly.newPlot" in html:
            return html, lm, final
        m = re.search(r'<iframe[^>]+src="([^"]+)"', html)
        if not m:
            errs.append(f"{url}: 没有图")
            continue
        try:
            req = urllib.request.Request(m.group(1), headers=UA)
            with urllib.request.urlopen(req, timeout=90) as resp:
                html2 = resp.read().decode("utf-8", "replace")
                lm2 = resp.headers.get("Last-Modified")
            if "Plotly.newPlot" in html2:
                return html2, lm2, m.group(1)
        except Exception as exc:  # noqa: BLE001
            errs.append(str(exc))
    raise RuntimeError("；".join(errs) or "CheckOnChain 没有响应")


def decode_arr(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict) and "bdata" in v:
        code = DTYPES.get(str(v.get("dtype", "f8")).lstrip("<>|="))
        if code is None:
            raise ValueError(f"未知 dtype {v.get('dtype')}")
        raw = base64.b64decode(v["bdata"])
        n = len(raw) // struct.calcsize(code)
        return list(struct.unpack(f"<{n}{code}", raw[: n * struct.calcsize(code)]))
    raise ValueError("不认识的数组")


def parse_traces(html: str) -> list:
    i = html.find("Plotly.newPlot")
    if i < 0:
        raise RuntimeError("没有 Plotly.newPlot")
    j = html.find("[", i)
    traces, _ = json.JSONDecoder().raw_decode(html[j:])
    return traces


def to_date(x) -> str:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")
    return str(x)[:10]


def _valid(y) -> bool:
    return y is not None and not (isinstance(y, float) and (math.isnan(y) or math.isinf(y)))


def generated_day(last_modified: str | None) -> str | None:
    if not last_modified:
        return None
    return email.utils.parsedate_to_datetime(last_modified).astimezone(timezone.utc).strftime("%Y-%m-%d")


def final_points(traces: list, last_modified: str | None) -> list[tuple[str, float]]:
    """生成日当天的点是盘中值，丢掉。没有 Last-Modified 时丢掉最后一天，避免把盘中当收盘。"""
    import re
    rx = re.compile(r"lth.*true.*reali[sz]ed.*price", re.I)
    tr = next((t for t in traces if t.get("name") and rx.search(t["name"])), None)
    if tr is None:
        names = [t.get("name") for t in traces]
        raise RuntimeError(f"没有 LTH True Realised Price，图里是 {names}")
    pts = [(to_date(x), float(y)) for x, y in zip(decode_arr(tr.get("x")), decode_arr(tr.get("y"))) if _valid(y)]
    if len(pts) < 2:
        raise RuntimeError("有效点少于 2")
    gen = generated_day(last_modified)
    if gen:
        pts = [p for p in pts if p[0] < gen]
    elif pts:
        pts = pts[:-1]
    return pts


def update(path: Path) -> tuple[list[dict], str | None]:
    try:
        html, lm, url = fetch_html()
        pts = final_points(parse_traces(html), lm)
    except Exception as exc:  # noqa: BLE001
        return read_csv(path), str(exc)
    rows = [{"date": d, "value": f"{v:.6f}", "source": url, "generated": generated_day(lm) or ""} for d, v in pts]
    if not rows:
        return read_csv(path), "没有收盘点"
    write_csv(path, rows, ["date", "value", "source", "generated"])
    return rows, None


def load(path: Path) -> Series:
    pts = []
    for row in read_csv(path):
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["value"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)
