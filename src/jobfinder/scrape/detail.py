"""職缺詳細頁抓取。

詳細內容的 ajax 端點要求 ``Referer`` 是該職缺自己的頁面,所以一定要真的導覽到
``/job/{jobNo}``,再攔截頁面自己發出的 ``/job/ajax/content/`` 回應。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..errors import BudgetExhausted
from ..models import JobSummary
from ..normalize import normalize_detail_response
from ..ports import DetailBatch
from .browser import check_page_not_blocked
from .budget import RequestBudget
from .interceptor import ResponseInterceptor
from .urls import build_job_url

log = logging.getLogger(__name__)


async def fetch_details(
    page: Any,
    interceptor: ResponseInterceptor,
    budget: RequestBudget,
    cfg: Config,
    jobs: list[JobSummary],
) -> DetailBatch:
    """逐筆抓取。序列、節流,並受 ``max_details_per_run`` 硬上限約束。"""
    batch = DetailBatch()

    for job in jobs:
        job_no, detail_id = job.job_no, job.detail_id
        if not budget.has_detail_quota():
            log.info("詳細頁預算用盡,已抓 %d 筆", len(batch.details))
            batch.truncated_by_budget = True
            break

        interceptor.clear("detail")
        try:
            # ⚠️ 用 detail_id 不是 job_no —— job_no 打詳細頁會 404(已實測)
            response = await budget.navigate(
                page, build_job_url(detail_id), delay="detail", kind="detail"
            )
        except BudgetExhausted:
            batch.truncated_by_budget = True
            break

        await check_page_not_blocked(page, response)

        payload = await interceptor.wait_for("detail", cfg.scrape.api_wait_seconds)
        if payload is None:
            batch.drift.append(f"{job_no}: 未攔截到詳細頁 API 回應")
            continue

        batch.details[job_no] = normalize_detail_response(payload, job_no, batch.drift)

    return batch
