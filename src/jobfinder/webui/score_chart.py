"""深評各維度的雷達圖(design-demos/2026-10-27-評分維度圖.html 方案 C,上圖下表)。

純函式:回傳 SVG 字串與表格列,不 import streamlit。

- 滿分不同(技術 35、年資 25…)→ 一律畫**得分率**,旁邊標實際分數;直接畫原始分數
  會讓技術永遠最大,看不出哪裡凹下去
- 比較基準 = 全部 Claude 深評的平均。Claude 分數整體比 Gemini 低 18 分,混在一起
  平均會失真,所以有 Claude 的就只用 Claude 的
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from html import escape

from .rows import JobRow

#: SCORE_PARTS 的短標籤 → 圖上顯示的名稱
DISPLAY = {
    "技術": "技術匹配",
    "年資": "年資可行",
    "領域": "領域",
    "成長": "成長價值",
    "實務": "實務條件",
}

#: 驗證過的參考色盤(dataviz palette.md)slot 1 與中性灰;亮/暗主題各一組
COLORS = {
    False: {"series": "#2a78d6", "fill": "rgba(42,120,214,.16)", "avg": "#8a8984",
            "grid": "#e9e8e3", "text": "#52514e", "value": "#0b0b0b", "surface": "#ffffff"},
    True: {"series": "#3987e5", "fill": "rgba(57,135,229,.22)", "avg": "#8f8e86",
           "grid": "#34332f", "text": "#c3c2b7", "value": "#f0efec", "surface": "#0e1117"},
}  # fmt: skip


def averages(rows: Iterable[JobRow]) -> tuple[dict[str, float], int]:
    """(各維度平均, 樣本數)。有 Claude 評的就只用 Claude 的。"""
    scored = [r for r in rows if r.score_parts]
    claude = [r for r in scored if r.score_model.startswith("claude-cli")]
    pool = claude or scored
    if not pool:
        return {}, 0
    sums: dict[str, float] = {}
    for r in pool:
        for label, got, _ in r.score_parts:
            sums[label] = sums.get(label, 0) + got
    return {k: v / len(pool) for k, v in sums.items()}, len(pool)


def breakdown_rows(
    parts: list[tuple[str, int, int]], avg: dict[str, float]
) -> list[dict[str, object]]:
    rows = []
    for label, got, full in parts:
        mean = avg.get(label)
        rows.append(
            {
                "維度": DISPLAY.get(label, label),
                "得分": f"{got}/{full}",
                "得分率": round(got / full * 100),
                "vs 平均": "—"
                if mean is None
                else f"{'▲ +' if got >= mean else '▼ '}{got - mean:.1f}",
            }
        )
    return rows


def radar_svg(
    parts: list[tuple[str, int, int]], avg: dict[str, float], *, dark: bool, size: int = 340
) -> str:
    c = COLORS[dark]
    n = len(parts)
    cx, cy, radius = size / 2, size / 2 + 4, size / 2 - 58

    def pt(i: int, r: float) -> tuple[float, float]:
        a = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * math.cos(a), cy + r * math.sin(a)

    def poly(points: list[tuple[float, float]]) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    out = []
    for f in (0.25, 0.5, 0.75, 1):
        ring = poly([pt(i, radius * f) for i in range(n)])
        out.append(f'<polygon points="{ring}" fill="none" stroke="{c["grid"]}" stroke-width="1"/>')
    for i in range(n):
        x, y = pt(i, radius)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{c["grid"]}"/>')
    if avg:
        mean_pts = [
            pt(i, radius * avg.get(label, 0) / full) for i, (label, _, full) in enumerate(parts)
        ]
        out.append(
            f'<polygon points="{poly(mean_pts)}" fill="none" stroke="{c["avg"]}" '
            'stroke-width="1.5" stroke-dasharray="4 3"/>'
        )
    pts = [pt(i, radius * got / full) for i, (_, got, full) in enumerate(parts)]
    out.append(
        f'<polygon points="{poly(pts)}" fill="{c["fill"]}" stroke="{c["series"]}" '
        'stroke-width="2" stroke-linejoin="round"/>'
    )
    for i, (label, got, full) in enumerate(parts):
        x, y = pts[i]
        lx, ly = pt(i, radius + 28)
        anchor = "middle" if abs(lx - cx) < 4 else ("start" if lx > cx else "end")
        name = escape(DISPLAY.get(label, label))
        mean = avg.get(label)
        tip = f"{name}:{got}/{full}({got / full:.0%})" + (
            f" · 平均 {mean:.1f}" if mean is not None else ""
        )
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{c["series"]}" '
            f'stroke="{c["surface"]}" stroke-width="2"><title>{tip}</title></circle>'
        )
        out.append(
            f'<text x="{lx:.1f}" y="{ly - 6:.1f}" text-anchor="{anchor}" font-size="12" '
            f'fill="{c["text"]}">{name}</text>'
            f'<text x="{lx:.1f}" y="{ly + 9:.1f}" text-anchor="{anchor}" font-size="12" '
            f'font-weight="600" fill="{c["value"]}">{got}/{full}</text>'
        )
    legend_y = size + 2
    out.append(
        f'<line x1="8" y1="{legend_y}" x2="24" y2="{legend_y}" '
        f'stroke="{c["series"]}" stroke-width="3"/>'
        f'<text x="30" y="{legend_y + 4}" font-size="12" fill="{c["text"]}">這筆職缺</text>'
        f'<line x1="100" y1="{legend_y}" x2="116" y2="{legend_y}" stroke="{c["avg"]}" '
        'stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<text x="122" y="{legend_y + 4}" font-size="12" fill="{c["text"]}">全部深評平均</text>'
    )
    pad = 48  # 左右兩側的標籤(實務條件、年資可行)會超出半徑範圍
    return (
        f'<svg viewBox="{-pad} 0 {size + 2 * pad} {size + 12}" width="100%" '
        f'style="max-width:{size + 2 * pad}px;display:block;margin:auto" role="img" '
        f'aria-label="深評各維度得分率雷達圖">{"".join(out)}</svg>'
    )
