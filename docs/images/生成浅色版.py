#!/usr/bin/env python3
"""把手写的深色 SVG 机械地换成浅色版。

**深色 SVG 是唯一的源。**浅色版不手改——改了下次生成就被覆盖。
这么做是为了不维护两份图：README 用 `<picture>` 按读者的 GitHub 主题切换，
但两份图要是各写各的，迟早对不上。

色板照 GitHub Primer：深色版用的就是 GitHub 深色（默认）主题的画布色 `#0d1117`，
所以深色主题下图和页面无缝；浅色版换成 Primer 的浅色对应值，浅色主题下同样无缝。

    python3 docs/images/生成浅色版.py          # 生成 *-light.svg
    python3 docs/images/生成浅色版.py --png    # 顺带渲染位图（需要 rsvg-convert）
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 深色（GitHub dark default）→ 浅色（GitHub light default）。
# 左边每一个值都必须真的出现在某张深色图里，右边是 Primer 的对应角色色。
# 加新颜色时左右都要填，缺了会在校验那一步报出来，不会静默漏掉。
PALETTE: dict[str, str] = {
    # ── 画布与分隔 ──
    "#0d1117": "#ffffff",   # canvas.default
    "#161b22": "#f6f8fa",   # canvas.subtle（面板）
    "#30363d": "#d1d9e0",   # border.default
    "#21262d": "#eaeef2",   # border.muted（行分隔线）
    "#3d444d": "#d1d9e0",   # 芯片描边
    # ── 文字 ──
    "#e6edf3": "#1f2328",   # fg.default（标题）
    "#c9d1d9": "#1f2328",   # fg.default（正文）
    "#adbac7": "#424a53",   # fg.muted 偏重
    "#8b949e": "#59636e",   # fg.muted
    "#6e7781": "#656d76",   # fg.subtle
    "#484f58": "#8c959f",   # fg.subtle 更淡（页脚、箭头）
    # ── 强调色 ──
    "#58a6ff": "#0969da",   # accent.fg
    "#79c0ff": "#0969da",   # accent.fg（等宽字里的蓝）
    "#1f6feb": "#0969da",   # accent.emphasis（描边）
    "#4b83c4": "#3b7dd8",   # 次级蓝（产出的第二行）
    "#3fb950": "#1a7f37",   # success.fg
    "#f85149": "#cf222e",   # danger.fg
    "#d2a8ff": "#8250df",   # done.fg
    "#a371f7": "#8250df",   # done.fg
    "#8957e5": "#8250df",   # done.emphasis（描边）
    "#f0883e": "#bc4c00",   # severe.fg
    "#e3a06a": "#bc4c00",   # severe.fg（文字）
}

HEX = re.compile(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?\b")


def to_light(svg: str, source: str) -> str:
    """换色。带 alpha 后缀的（如 #8957e555）按基色查表，alpha 原样保留。"""
    missing: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        base = "#" + m.group(1).lower()
        alpha = m.group(2) or ""
        if base not in PALETTE:
            missing.add(base)
            return m.group(0)
        return PALETTE[base] + alpha

    out = HEX.sub(sub, svg)
    if missing:
        raise SystemExit(
            f"✗ {source} 里有色板没覆盖的颜色：{'、'.join(sorted(missing))}\n"
            f"  在 PALETTE 里补上对应的浅色值再跑。"
        )
    return out


# (文件名主干, 渲染位图时的宽度)；宽度只影响 PNG，SVG 本身是矢量的
FIGURES = [
    ("workflow", 1920),
    ("architecture", 1920),
]


def main() -> None:
    want_png = "--png" in sys.argv[1:]
    for stem, width in FIGURES:
        dark = HERE / f"{stem}-dark.svg"
        light = HERE / f"{stem}-light.svg"
        if not dark.exists():
            raise SystemExit(f"✗ 找不到深色源文件 {dark}")
        light.write_text(to_light(dark.read_text(encoding="utf-8"), dark.name), encoding="utf-8")
        print(f"✓ {light.name}")
        if not want_png:
            continue
        for variant in ("dark", "light"):
            src = HERE / f"{stem}-{variant}.svg"
            dst = HERE / f"{stem}-{variant}.png"
            subprocess.run(["rsvg-convert", "-w", str(width), str(src), "-o", str(dst)], check=True)
            print(f"  ↳ {dst.name}")


if __name__ == "__main__":
    main()
