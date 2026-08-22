"""實作 ports.JobSource:用真實瀏覽器抓 104。

刻意只有薄薄一層 —— 護欄在 :mod:`budget` / :mod:`blocking`,解析在 :mod:`normalize`,
這裡只負責把它們串起來並管好瀏覽器的生命週期。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..config import Config
from ..models import JobSummary
from ..ports import DetailBatch, SummaryBatch
from .browser import browser_context
from .budget import RequestBudget
from .detail import fetch_details
from .interceptor import ResponseInterceptor
from .search import fetch_all_keywords

log = logging.getLogger(__name__)


class PlaywrightJobSource:
    def __init__(self, cfg: Config, budget: RequestBudget, page: Any) -> None:
        self.cfg = cfg
        self.budget = budget
        self.page = page
        self.interceptor = ResponseInterceptor(
            page,
            {
                "search": cfg.scrape.search_api_pattern,
                "detail": cfg.scrape.detail_api_pattern,
            },
        ).attach()
        #: 預算中途用盡時,pipeline 靠它拿到已經抓到的部分
        self.partial_batch: SummaryBatch | None = None

    @property
    def requests_used(self) -> int:
        return self.budget.navigations_used

    async def fetch_summaries(self) -> SummaryBatch:
        batch = await fetch_all_keywords(self.page, self.interceptor, self.budget, self.cfg)
        self.partial_batch = batch
        return batch

    async def fetch_details(self, jobs: list[JobSummary]) -> DetailBatch:
        return await fetch_details(self.page, self.interceptor, self.budget, self.cfg, jobs)


@asynccontextmanager
async def playwright_source(
    cfg: Config,
    *,
    data_dir: str | Path,
    volume: Any = None,
    headless: bool | None = None,
) -> AsyncIterator[PlaywrightJobSource]:
    """開瀏覽器 → warmup → 確認匿名 → 產生 source。"""
    budget = RequestBudget(cfg.scrape)
    async with browser_context(
        cfg, budget, data_dir=data_dir, volume=volume, headless=headless
    ) as context:
        page = await context.new_page()
        page.set_default_timeout(cfg.scrape.page_timeout_ms)
        source = PlaywrightJobSource(cfg, budget, page)
        try:
            yield source
        finally:
            source.interceptor.detach()
            with contextlib.suppress(Exception):
                await page.close()
            log.info(
                "本次共 %d 次導覽(上限 %d)",
                budget.navigations_used,
                cfg.scrape.max_navigations_per_run,
            )
