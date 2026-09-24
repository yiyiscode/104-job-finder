"""``--fake-llm`` 用的假 client:用技能詞典粗估,不打 OpenRouter、不花錢。

只為了離線開發與 UI 預覽,數字**不可信** —— 它分不出必備與加分(JD 裡出現的技能全當必備)。
"""

from __future__ import annotations

import re
from typing import Any

from ..webui.skills import match_skills
from .compute import HitRateCheck, Requirement

_SECTION = re.compile(r"# 工作內容與條件\n(.*)", re.S)
_RESUME = re.compile(r"# 求職者履歷\n(.*?)\n# 職缺", re.S)


class FakeHitRateClient:
    async def structured(self, *, user: str, **_: Any) -> tuple[HitRateCheck, float]:
        resume = _RESUME.search(user)
        jd = _SECTION.search(user)
        have = match_skills(resume.group(1) if resume else "")
        wanted = sorted(match_skills(jd.group(1) if jd else ""))[:12]
        reqs = [
            Requirement(
                item=skill,
                kind="required",
                match="yes" if skill in have else "no",
                evidence="(假評分)履歷技能表" if skill in have else "履歷無相關經驗",
            )
            for skill in wanted
        ]
        return HitRateCheck(requirements=reqs), 0.0
