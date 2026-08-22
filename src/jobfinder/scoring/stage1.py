"""Stage 1 粗篩。

**一定要批次。** 履歷 context 約 800 token 是每個 request 的固定成本;一次帶 15 筆
攤提後每筆邊際成本只剩約 60 token。逐筆呼叫等於把履歷付 15 次。

批次的代價是便宜模型會漏回某些 job_no,所以回來一定要對集合、缺的補一輪。
"""

from __future__ import annotations

import asyncio
import logging

from ..config import LLMCfg, ScoringCfg
from ..models import JobSummary, RejectedJob
from ..ports import ScreenOutcome
from .llm_client import OpenRouterClient
from .prompts import SYSTEM_SCREEN, build_screen_user
from .resume import Resume
from .schemas import ScreenItem, ScreenResult

log = logging.getLogger(__name__)


def _chunks(jobs: list[JobSummary], size: int) -> list[list[JobSummary]]:
    return [jobs[i : i + size] for i in range(0, len(jobs), size)]


async def screen(
    client: OpenRouterClient,
    llm: LLMCfg,
    scoring: ScoringCfg,
    resume: Resume,
    jobs: list[JobSummary],
) -> ScreenOutcome:
    if not jobs:
        return ScreenOutcome()

    outcome = ScreenOutcome()
    items: dict[str, ScreenItem] = {}
    semaphore = asyncio.Semaphore(llm.screen.max_concurrent)

    async def run_batch(batch: list[JobSummary]) -> tuple[list[ScreenItem], float]:
        async with semaphore:
            result, cost = await client.structured(
                model=llm.screen.model,
                system=SYSTEM_SCREEN,
                user=build_screen_user(resume.brief, batch),
                schema_model=ScreenResult,
                schema_name="screen_result",
                temperature=llm.screen.temperature,
            )
            return result.results, cost

    batches = _chunks(jobs, llm.screen.batch_size)
    for result in await asyncio.gather(*(run_batch(b) for b in batches), return_exceptions=True):
        if isinstance(result, BaseException):
            log.warning("粗篩批次失敗:%s", result)
            outcome.warnings.append(f"粗篩批次失敗:{result}")
            continue
        batch_items, cost = result
        outcome.cost_usd += cost
        for item in batch_items:
            items[item.job_no] = item

    # 便宜模型批次時會漏回幾筆 —— 對集合,缺的補一輪
    missing = [j for j in jobs if j.job_no not in items]
    if missing:
        log.info("粗篩漏回 %d 筆,補跑一輪", len(missing))
        for result in await asyncio.gather(
            *(run_batch(b) for b in _chunks(missing, llm.screen.batch_size)),
            return_exceptions=True,
        ):
            if isinstance(result, BaseException):
                continue
            batch_items, cost = result
            outcome.cost_usd += cost
            for item in batch_items:
                items[item.job_no] = item

    for job in jobs:
        item = items.get(job.job_no)
        if item is None:
            # 補跑後仍漏 —— 寧可多花一點深評成本,也不要靜默弄丟一個可能的好缺。
            # 深評有 max_details_per_run 兜著,不會失控。
            outcome.warnings.append(f"{job.job_no} 粗篩未回應,保守保留進深評")
            outcome.keep.append(job)
            continue

        if item.keep and item.rough >= llm.screen.rough_threshold:
            outcome.keep.append(job)
        else:
            outcome.dropped.append(
                RejectedJob(
                    job_no=job.job_no,
                    job_name=job.job_name,
                    cust_name=job.cust_name,
                    total=item.rough,
                    reason=item.reason or "粗篩判定不適合",
                )
            )

    log.info("粗篩:%d 筆進 → 留 %d 筆", len(jobs), len(outcome.keep))
    return outcome
