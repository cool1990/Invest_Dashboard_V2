"""MicroStrategy / Strategy 的比特币持仓与均价，来自 SEC 8-K。

只保留正文里能对上的「As of … Aggregate BTC Holdings」。对不上的文件跳过，不估。
已经解析过的 accession 缓存在本地，每天只拉新的 8-K。
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from datetime import datetime
from pathlib import Path

from .http import get_bytes, get_json

CIK = "0001050446"
SUBMISSIONS = f"https://data.sec.gov/submissions/CIK{CIK}.json"
UA = {"User-Agent": "InvestDashboard research contact@example.com", "Accept": "application/json,text/html"}
# 比特币购入从 2020-08 开始。更早的 8-K 与持仓无关。
START = "2020-08-01"

_ASOF = re.compile(
    r"As of ([A-Z][a-z]+ \d{1,2}, \d{4})\*?\s+Aggregate BTC Holdings\s+"
    r"Aggregate Purchase Price \(in billions\)(?:\s*\(\d+\))?\s+"
    r"Average Purchase Price(?:\s*\(\d+\))?\s+"
    r"([\d,]+)\s+\$([\d,.]+)\s+\$([\d,.]+)",
    re.I,
)
_PERIOD = re.compile(
    r"During Period ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})\*?\s+"
    r"BTC Acquired(?:\s*\(\d+\))?\s+"
    r"Aggregate Purchase Price \(in millions\)(?:\s*\(\d+\))?\s+"
    r"Average Purchase Price(?:\s*\(\d+\))?\s+"
    r"([\d,]+|[-–—])\s+\$?([\d,.]+|[-–—])\s+\$?([\d,.]+|[-–—])",
    re.I,
)
# 散文有两套，数字都是公司写明的，不另算均价。
# 2021：「holds approximately 92,079 bitcoins that were acquired at an aggregate purchase price of $2.251 billion」
# 2024：「held an aggregate of approximately 193,000 bitcoins, which were acquired … approximately $6.09 billion」
_HELD = re.compile(
    r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}), .{0,240}?"
    r"(?:held an aggregate of|holds|held) approximately ([\d,]+) bitcoins,?\s+"
    r"(?:which|that) were acquired at an aggregate purchase price of (?:approximately )?\$([\d,.]+) (billion|million)"
    r"(?: and an average purchase price of approximately \$([\d,]+))?",
    re.I,
)
# 2020-09-15 只有累计购买，没有单列均价。均价留空。
_TOTAL = re.compile(
    r"On ([A-Z][a-z]+ \d{1,2}, \d{4}), the Company completed its acquisition of ([\d,]+) additional bitcoins"
    r" at an aggregate purchase price of \$([\d,.]+) million.{0,400}?"
    r"purchased a total of ([\d,]+) bitcoins at an aggregate purchase price of \$([\d,.]+) million",
    re.I,
)
_SPAN = re.compile(
    r"during the period between ([A-Z][a-z]+ \d{1,2}, \d{4}) and ([A-Z][a-z]+ \d{1,2}, \d{4})",
    re.I,
)
_BOUGHT = re.compile(
    r"acquired approximately ([\d,]+) bitcoins for (?:approximately )?\$([\d,.]+) million in cash"
    r".{0,180}?at an average price of approximately \$([\d,]+)",
    re.I,
)


def _day(text: str) -> str | None:
    try:
        return datetime.strptime(text, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _num(text: str) -> float | None:
    s = text.strip().replace(",", "")
    if s in {"", "-", "–", "—"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def plain(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>", " ", html)
    text = re.sub(r"(?is)<style[\s\S]*?</style>", " ", text)
    text = re.sub(r"(?i)<[^>]+>", " ", text)
    text = html_lib.unescape(text.replace("&nbsp;", " ").replace("&#160;", " "))
    return re.sub(r"\s+", " ", text)


def parse_text(text: str, accession: str = "", filed: str = "") -> dict | None:
    """取 BTC Update 段里最后一条 As-of。没有表时，试散文里的 held N bitcoins（均价经常对不上，就留空）。"""
    chunk = text
    i = text.lower().find("btc update")
    if i >= 0:
        chunk = text[i:i + 2500]
    hits = list(_ASOF.finditer(chunk))
    if hits:
        m = hits[-1]
        asof = _day(m.group(1))
        if not asof:
            return None
        periods = list(_PERIOD.finditer(chunk))
        acquired = acquired_from = acquired_to = buy_px = None
        if periods:
            p = periods[-1]
            acquired = _num(p.group(3))
            acquired_from = _day(p.group(1))
            acquired_to = _day(p.group(2))
            buy_px = _num(p.group(5))
        return {
            "accession": accession, "filed": filed, "asof": asof,
            "holdings": _num(m.group(2)), "cost_bn": _num(m.group(3)),
            "avg_cost": _num(m.group(4)), "acquired": acquired,
            "acquired_from": acquired_from, "acquired_to": acquired_to,
            "buy_px": buy_px,
        }
    prose = list(_HELD.finditer(text))
    if prose:
        m = prose[-1]
        asof = _day(m.group(1))
        holdings = _num(m.group(2))
        if asof and holdings is not None:
            window = text[max(0, m.start() - 900):m.start()]
            bought = _BOUGHT.search(window)
            span = _SPAN.search(window)
            cost = _num(m.group(3) or "")
            if cost is not None and (m.group(4) or "").lower() == "million":
                cost = cost / 1000
            return {
                "accession": accession, "filed": filed, "asof": asof,
                "holdings": holdings, "cost_bn": cost,
                "avg_cost": _num(m.group(5) or ""),
                "acquired": _num(bought.group(1)) if bought else None,
                "acquired_from": _day(span.group(1)) if span else None,
                "acquired_to": _day(span.group(2)) if span else None,
                "buy_px": _num(bought.group(3)) if bought else None,
            }
    total = _TOTAL.search(text)
    if not total:
        return None
    asof = _day(total.group(1))
    holdings = _num(total.group(4))
    cost_mn = _num(total.group(5))
    if not asof or holdings is None:
        return None
    return {
        "accession": accession, "filed": filed, "asof": asof,
        "holdings": holdings, "cost_bn": None if cost_mn is None else cost_mn / 1000,
        "avg_cost": None, "acquired": _num(total.group(2)),
        "acquired_from": None, "acquired_to": asof, "buy_px": None,
    }


def _index_url(accession: str) -> str:
    nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{nodash}/index.json"


def _doc_url(accession: str, name: str) -> str:
    nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{nodash}/{name}"


def _primary_name(index: dict) -> str | None:
    names = [it.get("name") or "" for it in (index.get("directory") or {}).get("item") or []]
    htms = []
    for name in names:
        low = name.lower()
        if not low.endswith((".htm", ".html")):
            continue
        base = low.rsplit("/", 1)[-1]
        if "index" in base or re.fullmatch(r"r\d+\.html?", base):
            continue
        htms.append(name)
    if not htms:
        return None
    # R1.htm 是 XBRL 封面，没有持仓。正文一般叫 d######d8k.htm 或 mstr-日期.htm。
    def rank(name: str) -> tuple:
        low = name.lower()
        pref = 0 if ("8k" in low or "8-k" in low) else 1
        return (pref, len(name))

    return sorted(htms, key=rank)[0]


def list_8k(payload: dict) -> list[tuple[str, str]]:
    """(accession, filingDate)，从新到旧，只要 2020-08 之后的 8-K。"""
    out = []

    def take(block: dict) -> None:
        forms = block.get("form") or []
        acc = block.get("accessionNumber") or []
        dates = block.get("filingDate") or []
        for form, a, d in zip(forms, acc, dates):
            if form == "8-K" and d >= START:
                out.append((a, d))

    take(payload.get("filings", {}).get("recent") or {})
    return out


def _older_8k() -> list[tuple[str, str]]:
    try:
        payload = get_json(SUBMISSIONS, headers=UA, timeout=60)
    except Exception:  # noqa: BLE001
        return []
    files = (payload.get("filings") or {}).get("files") or []
    out = []
    for item in files:
        name = item.get("name")
        if not name:
            continue
        if (item.get("filingTo") or "") < START:
            continue
        try:
            extra = get_json(f"https://data.sec.gov/submissions/{name}", headers=UA, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        out.extend(list_8k({"filings": {"recent": extra}}))
        time.sleep(0.15)
    return out


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"records": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"records": []}


def update(path: Path, seed: list[dict] | None = None) -> tuple[list[dict], str | None]:
    cache = _load_cache(path)
    have = {r.get("accession") for r in cache.get("records") or [] if r.get("accession")}
    notes = []
    try:
        payload = get_json(SUBMISSIONS, headers=UA, timeout=60)
        filings = list_8k(payload) + _older_8k()
    except Exception as exc:  # noqa: BLE001
        filings = []
        notes.append(str(exc))
    fresh = [(a, d) for a, d in filings if a not in have]
    # 同一 accession 只留一次
    seen = set()
    ordered = []
    for a, d in fresh:
        if a in seen:
            continue
        seen.add(a)
        ordered.append((a, d))
    parsed = list(cache.get("records") or [])
    errors = 0
    for acc, filed in ordered:
        try:
            index = get_json(_index_url(acc), headers=UA, timeout=40)
            name = _primary_name(index)
            if not name:
                time.sleep(0.12)
                continue
            html = get_bytes(_doc_url(acc, name), headers=UA, timeout=40).decode("utf-8", "replace")
            row = parse_text(plain(html), acc, filed)
            if row and row.get("holdings"):
                parsed.append(row)
        except Exception:  # noqa: BLE001
            errors += 1
        time.sleep(0.12)
    if seed:
        got_acc = {r.get("accession") for r in parsed}
        for row in seed:
            if row.get("accession") and row["accession"] not in got_acc and row.get("holdings"):
                parsed.append(row)
    # 同一 asof 留申报日更晚的那份。
    by_asof: dict[str, dict] = {}
    for row in sorted(parsed, key=lambda r: (r.get("asof") or "", r.get("filed") or "")):
        if row.get("asof"):
            by_asof[row["asof"]] = row
    records = [by_asof[k] for k in sorted(by_asof)]
    if not records and notes:
        return [], "；".join(notes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    note = None
    if notes and not fresh:
        note = "；".join(notes)
    elif errors and not records:
        note = f"{errors} 份 8-K 没解析成"
    return records, note


def load(path: Path) -> list[dict]:
    return _load_cache(path).get("records") or []
