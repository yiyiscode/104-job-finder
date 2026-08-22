"""不打 OpenRouter 的假評分器(`--fake-llm`)。

存在的理由是 SPEC.md 規則 6 的同一件事:**開發期不要為了看排版而反覆呼叫外部服務**。
用它可以零成本、離線、可重現地把整條 pipeline 跑完並預覽日報長相。

刻意做成有規則的啟發式而非隨機,這樣調排版時每次結果一致。
"""

from __future__ import annotations

from ..config import ScoringCfg
from ..models import (
    JobDetail,
    JobSummary,
    RejectedJob,
    ScoreBreakdown,
    ScoredJob,
)
from ..ports import ScreenOutcome
from .rules import required_years

_TECH_WEIGHTS = {
    "llm": 12,
    "rag": 10,
    "langchain": 10,
    "gpt": 8,
    "nlp": 8,
    "bert": 8,
    "pytorch": 7,
    "深度學習": 7,
    "機器學習": 7,
    "yolo": 5,
    "opencv": 5,
    "電腦視覺": 5,
    "資料科學": 6,
    "mlops": 4,
    "kubernetes": 2,
    "python": 4,
}
_DOMAIN_WEIGHTS = {"金融": 12, "銀行": 12, "資安": 14, "保險": 11, "半導體": 9, "製造": 8}
#: 職稱裡出現就代表不是 AI 開發職。只比對**職稱**,不比對描述 ——
#: 「與業務單位協作」是很多好職缺的 JD 都會寫的話,拿它當否決條件會誤殺。
_NEGATIVE_TITLE = ("業務", "標註", "韌體", "硬體", "銷售", "客服", "工讀", "行銷", "專員")
#: 雇用型態,整段文字都要看
_NEGATIVE_ANY = ("派遣", "約聘")


def _haystack(job: JobSummary, detail: JobDetail | None) -> str:
    parts = [job.job_name, job.cust_name, job.desc_snippet or "", " ".join(job.tags)]
    if detail:
        parts += [
            detail.job_description or "",
            " ".join(detail.specialty),
            detail.industry or "",
        ]
    return " ".join(parts).lower()


class FakeScorer:
    """實作 ports.Scorer,行為與真實評分器一致但不連外。"""

    def __init__(self, scoring: ScoringCfg | None = None) -> None:
        self.scoring = scoring or ScoringCfg()
        self.raw_responses: dict[str, str] = {}

    async def screen(self, jobs: list[JobSummary]) -> ScreenOutcome:
        outcome = ScreenOutcome()
        for job in jobs:
            title = job.job_name
            text = _haystack(job, None)
            if any(w in title for w in _NEGATIVE_TITLE) or any(w in text for w in _NEGATIVE_ANY):
                outcome.dropped.append(
                    RejectedJob(
                        job_no=job.job_no,
                        job_name=job.job_name,
                        cust_name=job.cust_name,
                        total=25,
                        reason="[fake] 非 AI 技術開發職缺",
                    )
                )
            else:
                outcome.keep.append(job)
        return outcome

    async def deep(self, job: JobSummary, detail: JobDetail | None) -> tuple[ScoredJob, float]:
        text = _haystack(job, detail)

        tech = min(35, 8 + sum(w for k, w in _TECH_WEIGHTS.items() if k in text))
        domain = min(15, 4 + sum(w for k, w in _DOMAIN_WEIGHTS.items() if k in text))

        years = required_years(job, detail)
        if years is None:
            exp = 14
        elif years == 0:
            exp = 24
        elif years <= 2:
            exp = 19
        elif years < self.scoring.senior_years_cutoff:
            exp = 12
        else:
            exp = self.scoring.senior_exp_fit_cap

        growth = 12 if detail and detail.welfare_tags else 9
        practical = 4
        # 用換算後的月薪比較 —— 拿年薪的原始數字去比 45000 會讓每個年薪職缺都過關
        if job.monthly_low and job.monthly_low >= 45000:
            practical += 4
        if "派遣" not in text and "約聘" not in text:
            practical += 2

        breakdown = ScoreBreakdown(
            tech_fit=tech,
            exp_fit=exp,
            domain_fit=domain,
            growth_fit=growth,
            practical_fit=min(10, practical),
        )
        total = breakdown.total
        verdict = (
            "strong_apply"
            if total >= 85
            else "apply"
            if total >= self.scoring.threshold
            else "maybe"
            if total >= 55
            else "skip"
        )

        scored = ScoredJob(
            summary=job,
            detail=detail,
            breakdown=breakdown,
            total=total,
            verdict=verdict,
            one_liner=f"[fake] {job.job_name} —— 依關鍵字啟發式估算的示範評分。",
            highlights=[
                f"[fake] 職缺文字命中技術關鍵字,tech_fit 估 {tech}/35",
                f"[fake] 需求年資解析為 {years if years is not None else '未知'},"
                f"exp_fit 估 {exp}/25",
            ],
            red_flags=([] if exp >= 15 else [f"[fake] 年資要求偏高(解析為 {years} 年)"]),
            resume_tip="[fake] 這是假評分,實際建議請用真實模型產生。",
            model_used="fake",
        )
        self.raw_responses[job.job_no] = '{"fake": true}'
        return scored, 0.0

    @property
    def cost_so_far(self) -> float:
        return 0.0

    @property
    def budget_exceeded(self) -> bool:
        return False
