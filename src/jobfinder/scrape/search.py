"""搜尋列表抓取。

一個關鍵字一個關鍵字、一頁一頁,全程序列。**沒有任何併發** —— 見 SPEC.md 規則 3。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..errors import BudgetExhausted
from ..normalize import normalize_search_response
from ..ports import SummaryBatch
from .browser import check_page_not_blocked
from .budget import RequestBudget
from .interceptor import ResponseInterceptor
from .urls import build_search_page_url

log = logging.getLogger(__name__)


async def fetch_keyword(
    page: Any,
    interceptor: ResponseInterceptor,
    budget: RequestBudget,
    cfg: Config,
    keyword: str,
    batch: SummaryBatch,
    *,
    first_keyword: bool = False,
) -> None:
    """抓一個關鍵字的前 N 頁,結果併進 ``batch``。"""
    budget.start_keyword(keyword)

    for page_no in range(1, cfg.scrape.max_pages_per_keyword + 1):
        if not budget.has_page_quota():
            log.info("關鍵字「%s」達頁數或總預算上限,停在第 %d 頁", keyword, page_no - 1)
            batch.truncated_by_budget = True
            return

        url = build_search_page_url(cfg.search, keyword, page_no)
        interceptor.clear("search")

        # 第一頁之間隔久一點(換關鍵字像是新的一次搜尋),同關鍵字翻頁隔短一點
        delay = "keyword" if page_no == 1 and not first_keyword else "page"
        if first_keyword and page_no == 1:
            delay = "none"

        response = await budget.navigate(page, url, delay=delay, kind="search")
        # 被擋就丟 ChallengeBlocked,一路往上,絕不重試
        await check_page_not_blocked(page, response)

        payload = await interceptor.wait_for("search", cfg.scrape.api_wait_seconds)
        if payload is None:
            batch.drift.append(f"關鍵字「{keyword}」第 {page_no} 頁未攔截到搜尋 API 回應")
            return

        result = normalize_search_response(payload, keyword)
        batch.jobs.extend(result.jobs)
        batch.drift.extend(result.drift)
        log.info(
            "「%s」第 %d/%d 頁:%d 筆(總計 %d 筆符合)",
            keyword,
            page_no,
            result.total_page or 1,
            len(result.jobs),
            result.total_count,
        )

        if result.total_page and page_no >= result.total_page:
            return
        if not result.jobs:
            return


async def fetch_all_keywords(
    page: Any,
    interceptor: ResponseInterceptor,
    budget: RequestBudget,
    cfg: Config,
) -> SummaryBatch:
    batch = SummaryBatch()
    for i, keyword in enumerate(cfg.search.keywords):
        try:
            await fetch_keyword(
                page, interceptor, budget, cfg, keyword, batch, first_keyword=(i == 0)
            )
        except BudgetExhausted as exc:
            log.info("抓取預算用盡,停止後續關鍵字:%s", exc)
            batch.truncated_by_budget = True
            break
    batch.observed_urls = list(dict.fromkeys(interceptor.observed_urls))
    return batch
