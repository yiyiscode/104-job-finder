"""「已標投遞」頁的履歷修改建議。組合現有資料,不呼叫任何 API。

來源:
* 命中率逐條判定 —— 符合的是「要強調的」(附履歷證據),部分／不符的是「要補強的」
* 深評 —— resume_tip(履歷怎麼改)、highlights(亮點)、red_flags(面試要準備說明的)
* 程式版命中率 —— 技能詞典比對出 JD 有、履歷沒寫的技能

服務的是 SOP 的週二–週四:「逐條比對 + 改客製自傳段」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rows import JobRow


@dataclass
class Tailoring:
    emphasize: list[tuple[str, str]] = field(default_factory=list)  # (條件, 履歷證據)
    actions: list[str] = field(default_factory=list)  # 針對部分／不符的具體建議,核心排前面
    preferred_gaps: list[str] = field(default_factory=list)
    resume_tip: str = ""
    highlights: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.emphasize or self.actions or self.resume_tip or self.missing_skills)


def build_tailoring(row: JobRow) -> Tailoring:
    t = Tailoring(resume_tip=row.resume_tip, highlights=row.highlights, red_flags=row.red_flags)
    core_actions, other_actions = [], []
    for r in row.hit_requirements:
        if r.kind != "required":
            if r.match != "yes":
                t.preferred_gaps.append(r.item)
            continue
        if r.match == "yes":
            t.emphasize.append((r.item, r.evidence))
        elif r.match == "partial":
            text = f"「{r.item}」有相近經驗({r.evidence}):在自傳寫成等價能力,點明做過的是同一類事"
            (core_actions if r.core else other_actions).append(("⚠️ 核心 " if r.core else "") + text)
        else:
            text = f"「{r.item}」履歷沒有:投遞前補一個小作品,或準備面試時說明學習計畫"
            (core_actions if r.core else other_actions).append(("⚠️ 核心 " if r.core else "") + text)
    t.actions = core_actions + other_actions
    if row.skill_match is not None:
        covered = {item for item, _ in t.emphasize}
        t.missing_skills = [s for s in row.skill_match.missing if s not in covered]
    return t
