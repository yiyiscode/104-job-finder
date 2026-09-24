"""UI 用的職缺列。由 ``candidates.build_row`` 從 jobs.db 的原始 JSON 組出來。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .gates import GateResult
from .skill_match import SkillMatch


@dataclass
class JobRow:
    job_no: str
    company: str
    title: str
    title_group: str
    url: str
    area: str
    employees: int | None  # None = 104 沒提供(「暫不提供」)
    period: int | None  # 要求年資;0 = 不拘
    experience: str
    education: str
    salary_text: str
    salary_low_monthly: int | None  # 換算月薪;面議/無法換算 = None
    salary_high_monthly: int | None  # 「以上」型沒有上限 = None
    salary_open_ended: bool
    english: bool
    industry: str  # 產業格(groups.INDUSTRY_NAMES)
    industry_desc: str
    first_seen: date  # 台北日期
    pipeline_status: str  # jobs.status:new / filtered_out / screened_out / scored / notified
    has_detail: bool
    jd: str  # 閘門掃描用的文字:有全文用全文,沒有用列表摘要
    job_category: str  # 詳細頁的職務類別描述;沒有全文時為空
    summary_text: str  # 技能趨勢「摘要層」的文字
    detail_text: str | None  # 技能趨勢「全文層」的文字;沒有全文時為 None
    #: 104 原文排版:工作內容 +「條件要求」各欄位。沒有全文時為空
    jd_description: str = ""
    jd_conditions: list[tuple[str, str]] = field(default_factory=list)
    deep_score: int | None = None
    one_liner: str = ""
    hit_rate: float | None = None  # 0–1;None 時看 hit_rate_status
    hit_rate_status: str = "未計算"  # 已計算 / 未計算 / 無全文 / 無明列必備
    hit_requirements: list = field(default_factory=list)  # hitrate.compute.Requirement
    hit_model: str = ""  # 誰判讀的:OpenRouter 模型 id,或 claude-code(手動補算)
    hit_core_missed: tuple[str, ...] = ()  # 不是「符合」的核心條件 → 第 3 道不過
    #: 程式版命中率(技能詞典比對)。只顯示、不參與閘門
    prog_rate: float | None = None
    skill_match: SkillMatch | None = None
    gates: GateResult = field(default_factory=GateResult)
