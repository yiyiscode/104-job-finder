"""命中率的 schema 與計算。純函式。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

MATCH_WEIGHTS = {"yes": 1.0, "partial": 0.5, "no": 0.0}
#: 條件拆太細會讓分母膨脹、命中率失真 —— prompt 要求合併同類,程式端再截斷一次
MAX_REQUIREMENTS = 12
#: 核心條件上限。全部都標 core 等於沒標
MAX_CORE = 2


class Requirement(BaseModel):
    item: str
    kind: Literal["required", "preferred"]
    match: Literal["yes", "partial", "no"]
    evidence: str
    #: 這份工作的主要平台/工具 —— 不會它就做不了。只在 required 上有意義
    core: bool = False


class HitRateCheck(BaseModel):
    requirements: list[Requirement]


@dataclass(frozen=True)
class HitRate:
    rate: float | None  # None = JD 沒有可辨識的必備條件
    required: int
    met: int
    partial: int
    #: 不是「符合」的核心條件。非空 → 第 3 道不過,不論百分比多高
    core_missed: tuple[str, ...] = ()


def hit_rate(requirements: list[Requirement]) -> HitRate:
    """命中率 =(符合 + 0.5 × 部分)÷ 必備條數。加分條件不進分母。

    另外檢查核心條件:平均分數會把「卡在一個核心技術」稀釋掉(華碩 8vyka:AWS 資料服務
    只是 4 條必備之一,算出 88%,人工判定 ~61%)。所以核心條件只要不是「符合」,
    就記在 ``core_missed``,由閘門直接判不過。
    """
    required = [r for r in requirements[:MAX_REQUIREMENTS] if r.kind == "required"]
    if not required:
        return HitRate(rate=None, required=0, met=0, partial=0)
    met = sum(r.match == "yes" for r in required)
    partial = sum(r.match == "partial" for r in required)
    score = sum(MATCH_WEIGHTS[r.match] for r in required)
    cores = [r for r in required if r.core][:MAX_CORE]
    return HitRate(
        rate=score / len(required),
        required=len(required),
        met=met,
        partial=partial,
        core_missed=tuple(r.item for r in cores if r.match != "yes"),
    )


def resume_hash(resume_text: str) -> str:
    """命中率綁定履歷版本 —— 履歷改了,舊結果自動視為未計算。"""
    return hashlib.sha256(resume_text.strip().encode("utf-8")).hexdigest()[:12]
