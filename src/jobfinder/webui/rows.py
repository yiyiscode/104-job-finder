"""UI 用的職缺列。由 ``candidates.build_row`` 從 jobs.db 的原始 JSON 組出來。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .gates import GateResult


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
    deep_score: int | None = None
    one_liner: str = ""
    hit_rate: float | None = None  # 第 3 道(P2)尚未實作
    gates: GateResult = field(default_factory=GateResult)
