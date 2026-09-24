"""命中率的 schema 與計算。純函式。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

MATCH_WEIGHTS = {"yes": 1.0, "partial": 0.5, "no": 0.0}
#: 條件拆太細會讓分母膨脹、命中率失真 —— prompt 要求合併同類,程式端再截斷一次
MAX_REQUIREMENTS = 12


class Requirement(BaseModel):
    item: str
    kind: Literal["required", "preferred"]
    match: Literal["yes", "partial", "no"]
    evidence: str


class HitRateCheck(BaseModel):
    requirements: list[Requirement]


@dataclass(frozen=True)
class HitRate:
    rate: float | None  # None = JD 沒有可辨識的必備條件
    required: int
    met: int
    partial: int


def hit_rate(requirements: list[Requirement]) -> HitRate:
    """命中率 =(符合 + 0.5 × 部分)÷ 必備條數。加分條件不進分母。"""
    required = [r for r in requirements[:MAX_REQUIREMENTS] if r.kind == "required"]
    met = sum(r.match == "yes" for r in required)
    partial = sum(r.match == "partial" for r in required)
    if not required:
        return HitRate(rate=None, required=0, met=0, partial=0)
    score = sum(MATCH_WEIGHTS[r.match] for r in required)
    return HitRate(rate=score / len(required), required=len(required), met=met, partial=partial)


def resume_hash(resume_text: str) -> str:
    """命中率綁定履歷版本 —— 履歷改了,舊結果自動視為未計算。"""
    return hashlib.sha256(resume_text.strip().encode("utf-8")).hexdigest()[:12]
