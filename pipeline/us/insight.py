"""标普 500 盈利周报。

断点 2026-08-15 FactSet→LSEG（不可改回 FactSet，不可重写断点前历史）：
- 2026-08-07 及之前：FactSet《Earnings Insight》
- 2026-08-15 起：LSEG I/B/E/S《This Week in Earnings》
两套口径不同；EPS Surprise / 超预期 / 增速等断点前后不可直接对比，
调用方不要把断口当成真实跳变，也不要跨源做周环比。

笔记是 Obsidian 里的 markdown，指标在文首 frontmatter。仓库里落成
data/raw/us/earnings_insight.csv。把新的笔记放进 data/raw/us/insight/ 后，
更新脚本会按日期合并进这张表。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..csvio import read_csv, write_csv

# 断点 2026-08-15 FactSet→LSEG。此前最后一篇 FactSet=2026-08-07；此后只收 LSEG，不回写旧史。
BREAK = date(2026, 8, 15)

FIELDS = [
    "date", "source", "quarter",
    "q_growth", "y_growth", "q_guide", "q_net", "q_pos", "y_guide", "y_net", "y_pos",
    "reported", "reported_kind", "eps_above", "eps_surprise", "forward_pe",
    "spx_chg", "fwd_eps_chg", "rev_up", "rev_down", "theme", "judgment",
]
_NUM = {
    "q_growth", "y_growth", "q_guide", "q_net", "q_pos", "y_guide", "y_net", "y_pos",
    "reported", "eps_above", "eps_surprise", "forward_pe", "spx_chg", "fwd_eps_chg",
    "rev_up", "rev_down",
}
_KEYS = {
    "跟踪季度": "quarter",
    "季度EPS growth": "q_growth",
    "年度EPS growth": "y_growth",
    "季度指引": "q_guide",
    "季度Net": "q_net",
    "季度Positive %": "q_pos",
    "年度指引": "y_guide",
    "年度Net": "y_net",
    "年度Positive %": "y_pos",
    "实际披露": "reported",
    "EPS Above %": "eps_above",
    "EPS Surprise %": "eps_surprise",
    "Forward P/E": "forward_pe",
    "SPX price change": "spx_chg",
    "Forward EPS change": "fwd_eps_chg",
    "本周主线": "theme",
    "综合判断": "judgment",
}
_REV = re.compile(r"(\d+)\s*上\s*/\s*(\d+)\s*下")


def _unquote(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def _num(s: str) -> float | None:
    s = _unquote(s).replace(",", "").replace("%", "").replace("＋", "+").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cell(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.10g}"
    return str(v)


def frontmatter(text: str) -> dict[str, str]:
    """只取文首 --- 之间的键。值可以带引号；续行拼回同一个键。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in text[3:end].splitlines():
        m = re.match(r"^([^:#\s][^:]*):\s*(.*)$", line)
        if m:
            if key is not None:
                out[key] = "\n".join(buf).strip()
            key = m.group(1).strip()
            buf = [m.group(2)]
        elif key is not None:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out


def _source(when: date) -> str:
    return "LSEG" if when >= BREAK else "FactSet"


def revision_counts(theme: str) -> tuple[float | None, float | None]:
    """主线里的「220上/246下」。没有这种写法就返回空。"""
    m = _REV.search(theme or "")
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


def parse_note(text: str, when: date | None = None) -> dict | None:
    meta = frontmatter(text)
    raw_date = _unquote(meta.get("报告日期") or meta.get("date") or "")
    if when is None:
        if not raw_date:
            return None
        when = date.fromisoformat(raw_date[:10])
    row: dict = {"date": when.isoformat(), "source": _source(when), "reported_kind": ""}
    for src, dst in _KEYS.items():
        raw = meta.get(src, "")
        if dst == "reported":
            shown = _unquote(raw)
            row["reported"] = _num(shown)
            if not shown:
                row["reported_kind"] = ""
            elif "%" in raw or "%" in shown:
                row["reported_kind"] = "pct"
            else:
                # 早期笔记写的是已披露家数（19、33、65），不是百分比。
                row["reported_kind"] = "count"
        elif dst in _NUM:
            row[dst] = _num(raw)
        else:
            row[dst] = _unquote(raw)
    row["rev_up"], row["rev_down"] = revision_counts(row.get("theme") or "")
    return row


def parse_notes(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.md")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        when = date.fromisoformat(m.group(1)) if m else None
        row = parse_note(path.read_text(encoding="utf-8"), when)
        if row:
            rows.append(row)
    return rows


def _typed(raw: dict) -> dict:
    row = {"date": (raw.get("date") or "")[:10], "source": raw.get("source") or "",
           "quarter": raw.get("quarter") or "", "reported_kind": raw.get("reported_kind") or "",
           "theme": raw.get("theme") or "", "judgment": raw.get("judgment") or ""}
    if row["date"] and not row["source"]:
        row["source"] = _source(date.fromisoformat(row["date"]))
    for k in _NUM:
        row[k] = _num(raw.get(k) or "")
    if row["reported"] is None:
        row["reported_kind"] = ""
    return row


def load(path: Path) -> list[dict]:
    rows = [_typed(r) for r in read_csv(path) if (r.get("date") or "")[:10]]
    rows.sort(key=lambda r: r["date"])
    return rows


def write(path: Path, rows: list[dict]) -> None:
    out = [{k: _cell(r.get(k)) for k in FIELDS} for r in sorted(rows, key=lambda r: r["date"])]
    write_csv(path, out, FIELDS)


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """按日期合并，新笔记覆盖同一天的旧行。"""
    by = {r["date"]: r for r in old}
    for r in new:
        by[r["date"]] = r
    return [by[k] for k in sorted(by)]
