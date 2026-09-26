"""程式版命中率:技能詞典比對,零成本、每筆都算得出來。

跟 LLM 版(``jobfinder hitrate``)互補,**只顯示、不參與第 3 道判定**(使用者定案):
它分不出「核心」、沒有「部分符合」、也不懂等價經驗(自建排程 vs Airflow),
而沒有全文的職缺只能看 123 字的列表摘要。拿它擋人會誤殺。

必備 vs 加分:有全文時逐行判斷 —— 技能只出現在含「者佳／加分／優先…」的行 → 加分;
104 的「擅長工具」欄視為必備。沒有全文時,摘要裡出現的技能全部當必備。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .skills import match_skills, summary_skill_text

PREFERRED_LINE = re.compile(r"佳|加分|優先|尤佳|更好|preferred|nice to have|\bplus\b", re.I)


@dataclass(frozen=True)
class SkillMatch:
    rate: float | None  # None = 沒辨識到任何必備技能
    source: str  # 「全文」或「摘要」
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    preferred_missing: list[str] = field(default_factory=list)


def resume_skills(resume_text: str) -> frozenset[str]:
    return frozenset(match_skills(resume_text))


def _detail_required_preferred(detail: dict[str, Any]) -> tuple[set[str], set[str]]:
    job = detail.get("jobDetail") or {}
    cond = detail.get("condition") or {}
    lines = f"{job.get('jobDescription') or ''}\n{cond.get('other') or ''}".splitlines()
    required: set[str] = set()
    preferred: set[str] = set()
    for line in lines:
        (preferred if PREFERRED_LINE.search(line) else required).update(match_skills(line))
    for key in ("specialty", "skill"):
        tools = " ".join(
            x.get("description", "") for x in cond.get(key) or [] if isinstance(x, dict)
        )
        required |= match_skills(tools)
    return required, preferred - required


def skill_match(
    have: frozenset[str], summary: dict[str, Any], detail: dict[str, Any] | None
) -> SkillMatch:
    if detail:
        required, preferred = _detail_required_preferred(detail)
        source = "全文"
    else:
        required, preferred = match_skills(summary_skill_text(summary)), set()
        source = "摘要"
    matched = sorted(required & have)
    missing = sorted(required - have)
    rate = len(matched) / len(required) if required else None
    return SkillMatch(
        rate=rate,
        source=source,
        matched=matched,
        missing=missing,
        preferred_missing=sorted(preferred - have),
    )
