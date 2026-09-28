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
    industry_code: str = ""  # 列表 coIndustry 原始代碼(第 2 道的 SI 訊號用)
    welfare: str = ""  # 詳細頁福利制度原文(第 2 道的新人培訓訊號用);沒有全文時為空
    deep_score: int | None = None
    one_liner: str = ""
    #: 深評給的履歷建議、亮點、紅旗(沒深評的職缺為空)
    resume_tip: str = ""
    highlights: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    #: 深評五個維度 (名稱, 得分, 滿分);沒深評為空
    score_parts: list[tuple[str, int, int]] = field(default_factory=list)
    company_url: str = ""  # 104 公司頁(「公司其他職缺」);列表沒給時為空
    hit_rate: float | None = None  # 0–1;None 時看 hit_rate_status
    hit_rate_status: str = "未計算"  # 已計算 / 未計算 / 無全文 / 無明列必備
    hit_requirements: list = field(default_factory=list)  # hitrate.compute.Requirement
    hit_model: str = ""  # 誰判讀的:OpenRouter 模型 id,或 claude-code(手動補算)
    hit_core_missed: tuple[str, ...] = ()  # 不是「符合」的核心條件 → 第 3 道不過
    #: 程式版命中率(技能詞典比對)。只顯示、不參與閘門
    prog_rate: float | None = None
    skill_match: SkillMatch | None = None
    gates: GateResult = field(default_factory=GateResult)
    #: 在投遞清單(shortlist.db)裡。不在 jobs.db 的那些 job_no 其實是 detail_id
    in_shortlist: bool = False
    #: 投遞清單補抓的狀態:「尚未抓取」/ 104 回的錯誤;抓到了或不需要抓時為空
    fetch_note: str = ""
