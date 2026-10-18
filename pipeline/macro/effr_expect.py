"""市场隐含 EFFR（下次会议 / 年底 / 明年底）。

年底、明年底来自每天早晨的笔记：cool1990/invest-dashboard_v1 把它们整理进
data/sentiment/series.csv。笔记用 CBOT 30 日联邦基金期货的具体月份合约
（不是连续合约），隐含利率 = 100 − 价格。年底是当年 12 月会议后的首个整月
（如 ZQF2027），明年底是下一年 12 月会议后的首个整月（如 ZQF2028）。

下次会议原来用 Investing.com 概率，按「目标上限减 12bp」折算，停在 2026-09-29。
笔记从 2026-10-02 起改成同一套 ZQ 口径，但 series_id 写成了「下次会议EFFR」，
这边只认 effr_next，所以没接上。这里把这条并回 effr_next，并且只留 ZQ 口径：
按期货交易日计，休市沿用前收的行不另记一天。Investing 的旧点不再并入。
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from pathlib import Path

SOURCE_URL = "https://raw.githubusercontent.com/cool1990/invest-dashboard_v1/main/data/sentiment/series.csv"
KEYS = ("effr_next", "effr_year", "effr_ny")
# 笔记改口径后，下次会议的 series_id 换成了中文名。
ALIASES = {"下次会议EFFR": "effr_next"}
FIELDS = ["date", "series_id", "value", "obs_date", "remark"]
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_REMARK_DATE = re.compile(r"日期\s*(\d{4}-\d{2}-\d{2})")
_CARRY_DATE = re.compile(r"使用最近价格\s*(\d{4}-\d{2}-\d{2})")
METHOD_MARK = "口径切换："
METHOD_NOTE = (
    "口径切换：本序列自 2026-10-02 起只用 CBOT 30 日联邦基金期货的具体月份合约，"
    "隐含利率 = 100 − 价格（下次会议取会后首个整月，与年底、明年底同一口径）。"
    "此前 Investing.com「目标上限减 12bp」已从本序列移除，不与 ZQ 混用，也不插值；没有收盘价的日期留空。"
)


def _is_zq(remark: str) -> bool:
    return "ZQ" in (remark or "")


def _price_date(note_date: str, obs_date: str, remark: str) -> str:
    """期货收盘落在哪一天。休市笔记会把前一天的价格记到自己的日期上，这里收回来。"""
    obs = (obs_date or "").strip()[:10]
    if _ISO.fullmatch(obs):
        return obs
    found = _REMARK_DATE.findall(remark or "")
    if found:
        return found[-1]
    carried = _CARRY_DATE.search(remark or "")
    if carried:
        return carried.group(1)
    return note_date


def _prefer(note_date: str, price_date: str, prev_note: str) -> bool:
    """同一交易日有多条笔记时，留当天那条；都是后来补记的，留最早一条。"""
    new_exact, old_exact = note_date == price_date, prev_note == price_date
    if new_exact != old_exact:
        return new_exact
    if new_exact:
        return note_date > prev_note
    return note_date < prev_note


def _collapse_next(cands: list[tuple[str, str, dict]]) -> list[dict]:
    chosen: dict[str, tuple[str, dict]] = {}
    for note_date, price_date, row in cands:
        prev = chosen.get(price_date)
        if prev is None or _prefer(note_date, price_date, prev[0]):
            chosen[price_date] = (note_date, row)
    return [chosen[k][1] for k in sorted(chosen)]


def parse_source(text: str) -> list[dict]:
    rows: list[dict] = []
    nxt: list[tuple[str, str, dict]] = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        raw_id = (r.get("series_id") or "").strip()
        sid = ALIASES.get(raw_id, raw_id)
        if sid not in KEYS:
            continue
        try:
            float(r.get("value") or "")
        except ValueError:
            continue
        remark = (r.get("remark") or "").strip()
        note_date = (r.get("date") or "").strip()[:10]
        if sid == "effr_next":
            # 旧的 Investing.com「目标上限减 12bp」不并进 ZQ 序列。
            if not _is_zq(remark):
                continue
            price_date = _price_date(note_date, r.get("obs_date") or "", remark)
            nxt.append((note_date, price_date, {
                "date": price_date, "series_id": "effr_next",
                "value": (r.get("value") or "").strip(),
                "obs_date": price_date, "remark": remark,
            }))
            continue
        rows.append({k: (r.get(k) or "").strip() for k in FIELDS})
    rows.extend(_collapse_next(nxt))
    return rows


def _drop_old_next(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("series_id") != "effr_next" or _is_zq(r.get("remark") or "")]


def annotate(rows: list[dict]) -> list[dict]:
    """在最早一条 ZQ 下次会议的备注里记下口径切换。年底、明年底的备注不动。"""
    nxt = [r for r in rows if r.get("series_id") == "effr_next"]
    if not nxt:
        return rows
    first = min(nxt, key=lambda r: r.get("date") or "")
    remark = first.get("remark") or ""
    if METHOD_MARK not in remark:
        first["remark"] = (remark + "；" if remark else "") + METHOD_NOTE
    return rows


def prepare(rows: list[dict]) -> list[dict]:
    return annotate(_drop_old_next(rows))


def read_local(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load(path: Path) -> list[dict]:
    """给看板用的记录：去掉 Investing 口径的下次会议，并补上口径说明。"""
    return prepare(read_local(path))


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    by_key = {(r["date"], r["series_id"]): r for r in old}
    for r in new:
        by_key[(r["date"], r["series_id"])] = r
    return [by_key[k] for k in sorted(by_key)]


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def update(path: Path) -> tuple[list[dict], str | None]:
    """返回合并后的全部记录，以及错误信息（成功时为 None）。"""
    old = read_local(path)
    try:
        with urllib.request.urlopen(SOURCE_URL, timeout=30) as resp:
            new = parse_source(resp.read().decode("utf-8-sig", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return prepare(old), str(exc)[:200]
    rows = prepare(merge(_drop_old_next(old), new))
    _write(path, rows)
    return rows, None
