"""領域模型。

抓取層吐 raw dict,`normalize.py` 把它們轉成這裡的型別,之後所有模組只認這些型別。
這層邊界的價值:104 改版時只有 `normalize.py` 要改。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["strong_apply", "apply", "maybe", "skip"]
RunStatus = Literal["success", "partial", "blocked", "failed", "skipped"]


class JobSummary(BaseModel):
    """搜尋列表中的一筆職缺。欄位少但足以做粗篩。"""

    model_config = ConfigDict(extra="ignore")

    #: 104 的 `jobNo`(數字字串)。穩定,當去重主鍵用。
    job_no: str
    #: 職缺網址的尾段代碼(如 `94234`)。
    #:
    #: ⚠️ **與 `job_no` 是不同的東西。** 詳細頁 API `/job/ajax/content/{id}` 只吃這個 ——
    #: 拿 `job_no` 去打會回 404「職務不存在」(已實測)。
    detail_id: str
    job_name: str
    cust_name: str
    cust_no: str | None = None
    job_url: str
    area_desc: str | None = None
    salary_desc: str | None = None  # 104 列表沒有這欄,由 salary_low/high/type 自行組出
    salary_low: int | None = None
    salary_high: int | None = None
    #: 10=面議 20=時薪 30=日薪 40=論件計酬 50=月薪 60=年薪。
    #: ⚠️ 不看這個就會把年薪 567,000 寫成「月薪 56 萬」。
    salary_type: int | None = None
    #: 換算成月薪基準後的下限,供「薪資是否達標」公平比較。面議時為 None。
    monthly_low: int | None = None
    #: 要求的最低年資,0 = 不拘。由 `period`(= 年資 + 1)換算而來,見 normalize.period_to_years
    min_years: int | None = None
    period_desc: str | None = None  # 由 min_years 轉成的人話
    #: 學歷代碼 3=專科 4=大學 5=碩士 6=博士(已實測對照)
    edu_codes: list[int] = Field(default_factory=list)
    edu_desc: str | None = None
    appear_date: str | None = None  # 104 給的 YYYYMMDD
    desc_snippet: str | None = None
    apply_cnt: int | None = None
    tags: list[str] = Field(default_factory=list)

    # ── 列表就給的額外訊號,評分時有用 ──────────────────────────
    industry: str | None = None
    #: 104 的產業代碼(``coIndustry``)。階層式:``1001006`` = 半導體業,
    #: 底下 ``1001006001`` IC設計 / ``1001006002`` 半導體製造 / ``1001006003`` 其他半導體。
    #: 產業過濾一律比對這個而不是中文名稱 —— 104 改名稱時代碼不會變。
    industry_code: str | None = None
    employee_count: int | None = None
    remote_work_type: int | None = None
    #: 雇主回覆行為的百分位(0–1)。越高代表這家 HR 越常回覆應徵者。
    hr_response_pr: float | None = None

    #: 哪些搜尋關鍵字命中了這筆(多關鍵字合併去重後可能有多個)
    matched_keywords: list[str] = Field(default_factory=list)
    #: 原始 JSON,存進 DB 供 --replay 與改版時 diff
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    def content_hash(self) -> str:
        """偵測職缺內容是否實質變動(而非只是雇主刷新上架日期)。"""
        material = "|".join(
            str(x)
            for x in (
                self.job_name,
                self.cust_name,
                self.salary_desc,
                self.period_desc,
                self.edu_desc,
                self.desc_snippet,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_screen_block(self) -> str:
        """壓成 4 行餵給粗篩 prompt。刻意精簡:粗篩是批次的,每筆 token 都要省。"""
        tags = ",".join(self.tags[:8]) if self.tags else "—"
        # 摘要裡有換行。批次粗篩靠「每筆固定四行」讓模型分辨筆與筆的邊界,
        # 換行漏進去會把結構打散 —— 一次 15 筆時模型就開始張冠李戴。
        snippet = " ".join((self.desc_snippet or "—").split())[:120]
        return (
            f"[job_no] {self.job_no}\n"
            f"職缺: {self.job_name} | 公司: {self.cust_name} | 地點: {self.area_desc or '—'}\n"
            f"薪資: {self.salary_desc or '未揭露'} | 年資: {self.period_desc or '—'}"
            f" | 學歷: {self.edu_desc or '—'} | 產業: {self.industry or '—'}\n"
            f"摘要: {snippet} | 標籤: {tags}"
        )


class JobDetail(BaseModel):
    """職缺詳細頁。深評階段才需要,所以只對粗篩通過者抓。"""

    model_config = ConfigDict(extra="ignore")

    job_no: str
    job_description: str | None = None
    job_category: list[str] = Field(default_factory=list)
    salary: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    work_exp: str | None = None  # 條件中的經歷要求原文,例:「3年以上」
    edu: str | None = None
    major: list[str] = Field(default_factory=list)
    specialty: list[str] = Field(default_factory=list)  # 技能標籤
    skill: list[str] = Field(default_factory=list)
    other: str | None = None  # 其他條件
    welfare_tags: list[str] = Field(default_factory=list)
    welfare_text: str | None = None
    industry: str | None = None
    address: str | None = None
    employment_type: str | None = None  # 正職 / 派遣 / 兼職

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)


class ScoreBreakdown(BaseModel):
    """五個維度的分數。總分由程式重算,不信任模型的加法(SPEC.md §6.4)。"""

    tech_fit: int = Field(ge=0, le=35)
    exp_fit: int = Field(ge=0, le=25)
    domain_fit: int = Field(ge=0, le=15)
    growth_fit: int = Field(ge=0, le=15)
    practical_fit: int = Field(ge=0, le=10)

    @property
    def total(self) -> int:
        return self.tech_fit + self.exp_fit + self.domain_fit + self.growth_fit + self.practical_fit


class ScoredJob(BaseModel):
    """一則評分完成的職缺 —— 通知層唯一需要認識的型別。"""

    summary: JobSummary
    detail: JobDetail | None = None

    breakdown: ScoreBreakdown
    total: int
    verdict: Verdict
    one_liner: str
    highlights: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    resume_tip: str = ""

    model_used: str = ""
    #: 程式端套用的修正紀錄(算術幻覺、verdict 降級、年資 hard rule)
    adjustments: list[str] = Field(default_factory=list)

    @property
    def job_no(self) -> str:
        return self.summary.job_no


class RejectedJob(BaseModel):
    """未達標的職缺。只在摘要中一行列出,不發卡片。"""

    job_no: str
    job_name: str
    cust_name: str
    total: int
    reason: str


class RunReport(BaseModel):
    """一次執行的完整結果。存進 `runs` 表,也用來組摘要訊息。"""

    run_id: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = "success"

    jobs_fetched: int = 0
    jobs_new: int = 0
    jobs_screened_in: int = 0
    jobs_deep_scored: int = 0

    notified: list[ScoredJob] = Field(default_factory=list)
    rejected: list[RejectedJob] = Field(default_factory=list)

    #: 實際用掉的 HTTP 導覽數。稽核用 —— 這個數字失控代表護欄有漏洞。
    #: 被產業／規模規則層濾掉的筆數。稽核用:這個數字等於 jobs_new 時
    #: 代表過濾條件太窄(或 104 的欄位變了),日報會靜默歸零。
    jobs_filtered_out: int = 0

    requests_used: int = 0
    llm_cost_usd: float = 0.0
    schema_drift_count: int = 0

    error_kind: str | None = None
    error_detail: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if not self.finished_at:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()
