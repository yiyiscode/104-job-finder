"""Stage 2 深評。

逐筆呼叫,拿完整 JD 給出可信的分數與人話理由。模型輸出一律再經過
:func:`rules.apply_guardrails` 校正 —— 便宜模型的算術與年資判斷都不可信。
"""

from __future__ import annotations

import logging

from ..config import LLMCfg, ScoringCfg
from ..models import JobDetail, JobSummary, ScoredJob
from .llm_client import OpenRouterClient
from .prompts import SYSTEM_DEEP, build_deep_user
from .resume import Resume
from .rules import apply_guardrails
from .schemas import DeepScore

log = logging.getLogger(__name__)


async def deep_score(
    client: OpenRouterClient,
    llm: LLMCfg,
    scoring: ScoringCfg,
    resume: Resume,
    job: JobSummary,
    detail: JobDetail | None,
) -> tuple[ScoredJob, float, str]:
    """回傳 (評分結果, 成本 USD, 原始回應)。"""
    raw, cost = await client.structured(
        model=llm.deep.model,
        system=SYSTEM_DEEP,
        user=build_deep_user(resume.full, job, detail),
        schema_model=DeepScore,
        schema_name="deep_score",
        temperature=llm.deep.temperature,
    )

    breakdown, total, verdict, adjustments = apply_guardrails(raw, job, detail, scoring)
    if adjustments:
        log.info("%s 套用程式端校正:%s", job.job_no, "; ".join(adjustments))

    scored = ScoredJob(
        summary=job,
        detail=detail,
        breakdown=breakdown,
        total=total,
        verdict=verdict,
        one_liner=raw.one_liner.strip(),
        highlights=[h.strip() for h in raw.highlights if h.strip()],
        red_flags=[r.strip() for r in raw.red_flags if r.strip()],
        resume_tip=raw.resume_tip.strip(),
        model_used=llm.deep.model,
        adjustments=adjustments,
    )
    return scored, cost, raw.model_dump_json()
