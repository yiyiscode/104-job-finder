"""兩階段評分。實作 ports.Scorer。"""

from __future__ import annotations

import logging

from ..config import LLMCfg, ScoringCfg
from ..models import JobDetail, JobSummary, ScoredJob
from ..ports import ScreenOutcome
from .llm_client import CostTracker, OpenRouterClient
from .resume import Resume, load_resume
from .rules import (
    apply_guardrails,
    min_years_required,
    required_years,
    select_for_notification,
)
from .schemas import DeepScore, ScreenResult, to_strict_schema
from .stage1 import screen as _screen
from .stage2 import deep_score as _deep_score

log = logging.getLogger(__name__)


class TwoStageScorer:
    """粗篩(批次、便宜)→ 深評(逐筆、對照完整履歷)。"""

    def __init__(
        self,
        client: OpenRouterClient,
        llm: LLMCfg,
        scoring: ScoringCfg,
        resume: Resume,
    ) -> None:
        self.client = client
        self.llm = llm
        self.scoring = scoring
        self.resume = resume
        #: 原始回應留著存進 DB,任何覺得怪的分數都能回頭看模型當初怎麼想的
        self.raw_responses: dict[str, str] = {}

    async def screen(self, jobs: list[JobSummary]) -> ScreenOutcome:
        return await _screen(self.client, self.llm, self.scoring, self.resume, jobs)

    async def deep(self, job: JobSummary, detail: JobDetail | None) -> tuple[ScoredJob, float]:
        scored, cost, raw = await _deep_score(
            self.client, self.llm, self.scoring, self.resume, job, detail
        )
        self.raw_responses[job.job_no] = raw
        return scored, cost

    @property
    def cost_so_far(self) -> float:
        return self.client.tracker.total

    @property
    def budget_exceeded(self) -> bool:
        return self.client.tracker.exceeded


__all__ = [
    "CostTracker",
    "DeepScore",
    "OpenRouterClient",
    "Resume",
    "ScreenResult",
    "TwoStageScorer",
    "apply_guardrails",
    "load_resume",
    "min_years_required",
    "required_years",
    "select_for_notification",
    "to_strict_schema",
]
