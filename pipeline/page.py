"""各板块页面共用的图表、依据指标与序列打包。

dashboard.json 里的 charts / metrics 结构由这里决定，宏观和半导体两页的前端读同一种格式。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .series import Series

DISPLAY_START = date(1990, 1, 1)


def _r(v: float) -> float:
    if v == 0 or not math.isfinite(v):
        return 0.0
    a = abs(v)
    if a >= 1000:
        return round(v, 1)
    if a >= 10:
        return round(v, 2)
    return round(v, 4)


def _pack(s: Series, start: date = DISPLAY_START) -> list[list]:
    return [[d.isoformat(), _r(v)] for d, v in s if d >= start]


# ---------------------------------------------------------------------------
# 图表、依据指标


@dataclass
class Line:
    name: str
    data: Series
    kind: str = "line"  # line | bar
    dash: bool = False


@dataclass
class Chart:
    id: str
    title: str
    unit: str
    lines: list[Line]
    kind: str = "time"  # time | path
    stacked: str | None = None  # None | bar | area
    note: str = ""
    start: date = DISPLAY_START
    categories: list[str] = field(default_factory=list)
    core: bool = False
    digits: int | None = None  # 数据表小数位；不填则前端用 2 位

    def to_json(self) -> dict | None:
        lines = [ln for ln in self.lines if ln.data]
        if not lines:
            return None
        out = {"id": self.id, "title": self.title, "unit": self.unit, "kind": self.kind,
               "stacked": self.stacked, "note": self.note, "core": self.core, "series": []}
        if self.digits is not None:
            out["digits"] = self.digits
        if self.kind == "path":
            out["categories"] = self.categories
            for ln in lines:
                out["series"].append({"name": ln.name, "type": ln.kind, "dash": ln.dash,
                                      "data": [None if v is None else _r(v) for v in ln.data]})
            return out
        for ln in lines:
            out["series"].append({"name": ln.name, "type": ln.kind, "dash": ln.dash,
                                  "data": _pack(ln.data, self.start)})
        out["series"] = [s for s in out["series"] if s["data"]]
        if not out["series"]:
            return None
        out["last_date"] = max(s["data"][-1][0] for s in out["series"])
        return out


# 「较上期」往回数几个观测。周频和日频单期太吵，用 4 周前 / 约 1 个月前；
# 稀疏的手工记录（市场隐含 EFFR、Nowcast）直接比上一次记录。
FREQ = {
    "M": (1, "上月"),
    "Q": (1, "上季度"),
    "W": (4, "4 周前"),
    "D": (21, "约 1 个月前"),
    "DW": (7, "约 1 周前"),
    "O": (1, "上次记录"),
}


@dataclass
class Metric:
    """第二层每维的一个数：只放决定状态那句话的指标。

    id 与 interpret.py 里各维度状态返回的 anchors 对应，锚点文字从那里取，阈值只写一处。
    data 就是规则用的那个数（非农用 3 个月均值），单月之类的补充放 note。
    """

    id: str
    name: str
    data: Series
    unit: str
    fmt: str
    freq: str = "M"
    chart: str | None = None
    note: str = ""
    model: bool = False  # 模型预测，不进判断
    ref: bool = False  # 已公布的数据，但只作参考，不进判断
    about: str = ""  # 这个指标判断什么、怎么判断、为什么有效（几句话）
    surprise: bool = False  # 这一行要展示与最新值同一期的预期、以及是否超预期
    expect: float | None = None
    verdict: str = ""
    verdict_dir: str = ""  # hot 超预期 / cool 低于预期 / flat 符合预期


def signed(fmt: str) -> str:
    return fmt if "+" in fmt else fmt.replace("{:", "{:+", 1)



def period_text(d: date, freq: str) -> str:
    if freq == "Q":
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    if freq == "M":
        return f"{d.year}-{d.month:02d}"
    return d.isoformat()


def metric_json(m: Metric, anchors: dict[str, str], charts: dict) -> dict | None:
    """第二层的一个数：最新值、较上期、对照。chart 只在那张图真的输出了时才链接。
    变动（较上期变化）和状态（当前水平）由各板块的 build 再补上。"""
    if not m.data:
        return None
    d, v = m.data[-1]
    n, label = FREQ[m.freq]
    chg = None
    if len(m.data) > n:
        base = m.data[-1 - n][1]
        txt = signed(m.fmt).format(v - base)
        flat = float(txt.replace(",", "").replace("+", "")) == 0  # 按显示精度看是否为 0
        chg = {"label": label, "text": "持平" if flat else txt, "base": m.fmt.format(base),
               "dir": "flat" if flat else "up" if v > base else "down"}
    out = {"id": m.id, "name": m.name, "text": m.fmt.format(v), "unit": m.unit,
           "date": period_text(d, m.freq), "chg": chg, "anchor": anchors.get(m.id, ""),
           "note": m.note, "chart": m.chart if m.chart in charts else None, "model": m.model,
           "ref": m.ref, **({"about": m.about} if m.about else {})}
    if m.surprise:
        out["expect"] = None if m.expect is None else m.fmt.format(m.expect)
        out["verdict"] = {"key": m.verdict_dir, "label": m.verdict} if m.verdict else None
    return out
