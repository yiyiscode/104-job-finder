"""科系要求 → 命中率的一條必備條件。程式端比對,不交給 LLM(使用者定案)。

為什麼不給 LLM:校準時模型曾把「電機電子相關科系」誤標成核心條件判不符。
科系是固定的標籤比對,規則寫死、可測試,而且不必重算已有的命中率 ——
這一條在載入時附加到逐條判定後面,再由程式重算百分比。

學歷依 profile-de.md:臺科大資訊管理研究所 M.S.、臺北大學統計學系 B.S.
104 的科系要求是「符合其一即可」,所以多個科系取最好的判定。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..hitrate.compute import Requirement

EVIDENCE = "臺科大資訊管理研究所 M.S.、臺北大學統計學系 B.S."

#: 對得上學位本身(資管、統計)或其上層學類
YES = ("資訊管理", "統計", "數學及電算機科學", "應用數學", "一般數學", "商業及管理學科類")
#: 相鄰領域 —— 多數資料職缺實務上接受,但不是同一科系
PARTIAL = (
    "資訊工程",
    "電機電子工程",
    "工程學科類",
    "通信",
    "工業工程",
    "其他工程",
    "自然科學學科類",
    "物理",
    "其他相關科系",
    "一般商業",
)

_RANK = {"no": 0, "partial": 1, "yes": 2}


def judge_major(major: str) -> str:
    if any(k in major for k in YES):
        return "yes"
    if any(k in major for k in PARTIAL):
        return "partial"
    return "no"


def major_requirement(majors: Iterable[str]) -> Requirement | None:
    """沒有科系要求 → None(不加條件,不影響命中率)。"""
    listed = [m.strip() for m in majors if m and m.strip()]
    if not listed:
        return None
    best = max((judge_major(m) for m in listed), key=_RANK.__getitem__)
    return Requirement(
        item=f"科系:{'／'.join(listed)}",
        kind="required",
        match=best,
        evidence=EVIDENCE if best != "no" else "學歷為資管、統計,不在要求科系內",
        core=False,
    )
