"""把詳細頁 JSON 排成 104 頁面上的樣子:「工作內容」+「條件要求」。

欄位順序與標題照 104 職缺頁,讓使用者對照原站時不用重新找。空的欄位不顯示。
"""

from __future__ import annotations

from typing import Any

ABILITY_LABELS = (("listening", "聽"), ("speaking", "說"), ("reading", "讀"), ("writing", "寫"))


def _descriptions(items: Any, key: str = "description") -> list[str]:
    out = []
    for item in items or []:
        text = item.get(key) if isinstance(item, dict) else item
        if text and str(text).strip():
            out.append(str(text).strip())
    return out


def _languages(items: Any) -> str:
    lines = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("language"):
            continue
        ability = item.get("ability")
        if isinstance(ability, dict):
            levels = "、".join(
                f"{label} /{ability[k]}" for k, label in ABILITY_LABELS if ability.get(k)
            )
            lines.append(f"{item['language']} -- {levels}" if levels else item["language"])
        else:
            lines.append(f"{item['language']} -- {ability}" if ability else item["language"])
    return "\n".join(lines)


def condition_sections(condition: dict[str, Any]) -> list[tuple[str, str]]:
    roles = _descriptions((condition.get("acceptRole") or {}).get("role"))
    sections = [
        ("接受身份", "、".join(filter(None, roles))),
        ("工作經歷", condition.get("workExp") or ""),
        ("學歷要求", condition.get("edu") or ""),
        ("科系要求", "、".join(_descriptions(condition.get("major")))),
        ("語文條件", _languages(condition.get("language"))),
        ("擅長工具", "、".join(_descriptions(condition.get("specialty")))),
        ("工作技能", "、".join(_descriptions(condition.get("skill")))),
        ("具備證照", "、".join(_descriptions(condition.get("certificate"), key="name"))),
        ("駕駛執照", "、".join(_descriptions(condition.get("driverLicense")))),
        ("其他條件", (condition.get("other") or "").strip()),
    ]
    return [(title, text) for title, text in sections if text]


def detail_sections(detail: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """回傳 (工作內容, 條件要求的 [(欄位, 內容)])。``detail`` 是 ``data`` 那一層。"""
    job = detail.get("jobDetail") or {}
    return (job.get("jobDescription") or "").strip(), condition_sections(
        detail.get("condition") or {}
    )
