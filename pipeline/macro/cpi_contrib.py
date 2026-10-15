"""CPI 环比的绝对贡献（百分点）。

整体拆成食品、能源、核心。核心再拆成核心商品、住房、超级核心。

超级核心 = 核心服务剔除住房
    = BLS「Services less energy services」−「Shelter」。
没有单独的超级核心价格指数。对整体的贡献 = 服务除能源服务的贡献 − 住房的贡献。

食品、核心、核心商品、住房、服务除能源服务：
    对整体的贡献 = 上月相对重要性（占全部的百分数）× 分项季调环比（小数）。
对核心的贡献 = 对整体的贡献 / (上月核心相对重要性 / 100)。

能源不用「能源指数 × 比重」。食品、核心按上式算出之后，
能源 = 整体环比 − 食品 − 核心，使三项相加等于整体环比。
这是 BLS 新闻稿 Table 6 的可加总口径；公开指数四舍五入后，直接用能源指数
乘比重会留下大约 0.01 个百分点的残差。

相对重要性在 data/raw/cpi/relative_importance.csv。锚是 BLS 每年 12 月的
CPI-U 比重。解释某个月的环比时，用该月所属权重区间的锚，按
「比重变化 = 该分项指数相对整体指数的变化」推到上一个月
（1 月会用到上一年 12 月、但已经换算到本月这套权重上的比重）。
2025 年 10 月指数缺失时，跨过该月的环比留空，不插值。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..series import Series, _shift_month

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "cpi" / "relative_importance.csv"

# 成分 → FRED 上的季调指数。能源贡献用余项，不读能源指数。
INDEX = {
    "all": "CPIAUCSL",
    "food": "CPIUFDSL",
    "core": "CPILFESL",
    "core_goods": "CUSR0000SACL1E",
    "shelter": "CUSR0000SAH1",
    "services_ex_energy": "CUSR0000SASLE",
}

PARTS = ("food", "energy", "core", "core_goods", "shelter", "supercore")


@dataclass(frozen=True)
class Anchor:
    year_from: int
    year_to: int
    on: date
    ri: dict[str, float]
    note: str = ""


def load_anchors(path: Path | None = None) -> list[Anchor]:
    path = path or DEFAULT_PATH
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("anchor_date"):
                continue
            ri = {}
            for key in ("food", "energy", "core", "core_goods", "shelter", "services_ex_energy"):
                raw = (row.get(key) or "").strip()
                if raw:
                    ri[key] = float(raw)
            out.append(Anchor(int(row["year_from"]), int(row["year_to"]),
                              date.fromisoformat(row["anchor_date"]), ri, (row.get("note") or "").strip()))
    return out


def _anchor_for(anchors: list[Anchor], year: int) -> Anchor | None:
    matches = [a for a in anchors if a.year_from <= year <= a.year_to]
    return matches[-1] if matches else None


def ri_at(anchor: Anchor, levels: dict[str, dict[date, float]], component: str, when: date) -> float | None:
    """把锚上的相对重要性推到 when。锚当月直接用公布值，不再用指数回推。"""
    base = anchor.ri.get(component)
    if base is None:
        return None
    if when == anchor.on:
        return base
    all_a, all_w = levels["all"].get(anchor.on), levels["all"].get(when)
    comp_a, comp_w = levels[component].get(anchor.on), levels[component].get(when)
    if any(v is None or v == 0 for v in (all_a, all_w, comp_a)) or comp_w is None:
        return None
    return base * (comp_w / comp_a) / (all_w / all_a)


def _effect(anchor: Anchor, levels: dict[str, dict[date, float]], component: str,
            prev: date, current: date) -> float | None:
    """上月比重 × 本月环比，单位是百分点。"""
    ri = ri_at(anchor, levels, component, prev)
    old, new = levels[component].get(prev), levels[component].get(current)
    if ri is None or old is None or old == 0 or new is None:
        return None
    return ri * (new / old - 1)


def breakdown(raw: dict[str, Series], anchors: list[Anchor] | None = None) -> dict[str, Series]:
    """各月贡献。缺锚、缺指数或上月没有指数时，该月留空。"""
    empty = {k: [] for k in PARTS}
    anchors = load_anchors() if anchors is None else anchors
    if not anchors:
        return empty
    levels = {k: dict(raw.get(sid) or []) for k, sid in INDEX.items()}
    if not levels["all"]:
        return empty
    food, energy, core = [], [], []
    goods, shelter, supercore = [], [], []
    for current in sorted(levels["all"]):
        prev = _shift_month(current, -1)
        anchor = _anchor_for(anchors, current.year)
        all_prev, all_now = levels["all"].get(prev), levels["all"].get(current)
        if anchor is None or all_prev in (None, 0) or all_now is None:
            continue
        food_pp = _effect(anchor, levels, "food", prev, current)
        core_pp = _effect(anchor, levels, "core", prev, current)
        if food_pp is not None and core_pp is not None:
            headline = (all_now / all_prev - 1) * 100
            food.append((current, food_pp))
            core.append((current, core_pp))
            energy.append((current, headline - food_pp - core_pp))
        core_ri = ri_at(anchor, levels, "core", prev)
        goods_pp = _effect(anchor, levels, "core_goods", prev, current)
        shelter_pp = _effect(anchor, levels, "shelter", prev, current)
        services_pp = _effect(anchor, levels, "services_ex_energy", prev, current)
        if core_ri not in (None, 0) and goods_pp is not None and shelter_pp is not None and services_pp is not None:
            scale = core_ri / 100
            goods.append((current, goods_pp / scale))
            shelter.append((current, shelter_pp / scale))
            # 超级核心对整体的贡献 = 服务除能源服务 − 住房，再换算成对核心的百分点
            supercore.append((current, (services_pp - shelter_pp) / scale))
    return {"food": food, "energy": energy, "core": core,
            "core_goods": goods, "shelter": shelter, "supercore": supercore}
