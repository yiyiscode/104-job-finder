"""深評維度雷達圖(webui/score_chart.py):得分率、平均口徑、SVG 內容。"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET

from webui_helpers import row

from jobfinder.webui import score_chart

PARTS = [("技術", 28, 35), ("年資", 20, 25), ("領域", 6, 15), ("成長", 9, 15), ("實務", 5, 10)]


def scored(model: str, parts=PARTS):
    return dataclasses.replace(row(), score_parts=parts, score_model=model)


def test_averages_prefer_claude_scores():
    """Claude 分數整體比 Gemini 低 18 分,混在一起的平均會失真。"""
    rows = [
        scored("claude-cli:sonnet", [(n, 10, f) for n, _, f in PARTS]),
        scored("google/gemini", [(n, 30, f) for n, _, f in PARTS]),
        dataclasses.replace(row(), score_parts=[]),
    ]
    avg, n = score_chart.averages(rows)
    assert n == 1 and avg["技術"] == 10


def test_averages_fall_back_to_all_when_no_claude():
    avg, n = score_chart.averages([scored("gemini"), scored("gemini")])
    assert n == 2 and avg["年資"] == 20
    assert score_chart.averages([]) == ({}, 0)


def test_breakdown_rows_show_rate_and_diff_against_average():
    rows = score_chart.breakdown_rows(PARTS, {"技術": 17.4, "年資": 21.0})
    assert rows[0] == {"維度": "技術匹配", "得分": "28/35", "得分率": 80, "vs 平均": "▲ +10.6"}
    assert rows[1]["vs 平均"] == "▼ -1.0"
    assert rows[2]["vs 平均"] == "—"  # 沒有平均資料


def test_radar_svg_is_valid_and_labels_every_dimension():
    for dark in (False, True):
        svg = score_chart.radar_svg(PARTS, {"技術": 17.4}, dark=dark)
        root = ET.fromstring(svg)  # 不是合法 XML 的話 st.html 會整塊不顯示
        text = "".join(root.itertext())
        for name in (
            "技術匹配",
            "年資可行",
            "領域",
            "成長價值",
            "實務條件",
            "28/35",
            "全部深評平均",
        ):
            assert name in text
    assert score_chart.COLORS[True]["series"] in score_chart.radar_svg(PARTS, {}, dark=True)
