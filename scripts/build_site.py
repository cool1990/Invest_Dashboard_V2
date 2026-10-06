#!/usr/bin/env python3
"""把网页和页面要读的数据拷到 dist/。原始 CSV 不进 dist。

页面里引用的 assets/ 文件加上 ?v=<内容哈希>：改了脚本或样式之后网址跟着变，
浏览器不会拿缓存里的旧脚本去配新页面（旧脚本找不到新页面的元素会直接报错、整页空白）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PAGES = ["index.html", "macro.html", "semis.html", "us.html", "crypto.html"]
DATA_FILES = [
    "data/macro/dashboard.json",
    "data/semis/dashboard.json",
    "data/us/dashboard.json",
    "data/crypto/dashboard.json",
]
ASSET_REF = re.compile(r'(?P<attr>(?:src|href)=")(?P<path>assets/[^"?#]+)"')


def versioned(html: str) -> str:
    def sub(m: re.Match) -> str:
        f = ROOT / m["path"]
        if not f.is_file():
            return m[0]
        h = hashlib.sha256(f.read_bytes()).hexdigest()[:10]
        return f'{m["attr"]}{m["path"]}?v={h}"'

    return ASSET_REF.sub(sub, html)


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for p in PAGES:
        (DIST / p).write_text(versioned((ROOT / p).read_text(encoding="utf-8")), encoding="utf-8")
    shutil.copytree(ROOT / "assets", DIST / "assets")
    for rel in DATA_FILES:
        src = ROOT / rel
        if src.exists():
            (DIST / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, DIST / rel)
        else:
            print(f"缺少 {rel}，页面会提示数据还没生成")
    series = ROOT / "data" / "crypto" / "series"
    if series.is_dir():
        shutil.copytree(series, DIST / "data" / "crypto" / "series")
    (DIST / ".nojekyll").write_text("")
    (DIST / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    print(f"已构建到 {DIST}")


if __name__ == "__main__":
    main()
