"""純 HTTP 的職缺來源 —— 預設走這條。

104 的搜尋 API **沒有 Cloudflare**(已實測)。擋人的是 origin 層一條 ``Referer``
白名單規則:不帶 Referer 回 403 且 body 為 0 bytes,帶了就 200。連 User-Agent 都不用偽裝。

所以這條路不需要瀏覽器、不需要 warmup、不需要 profile、不需要處理挑戰。
單次請求約 1 秒,而不是十幾秒。

護欄一條都沒少:每個請求一樣先過 :meth:`RequestBudget.acquire`,
所以節流、預算、路徑黑名單、robots.txt 禁用參數的檢查全部照舊。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .config import Config
from .errors import ChallengeBlocked, SourceMisconfigured, TransientScrapeError
from .models import JobSummary
from .normalize import normalize_detail_response, normalize_search_response
from .ports import DetailBatch, SummaryBatch
from .scrape.blocking import classify_403, detect_block
from .scrape.budget import RequestBudget
from .scrape.urls import build_detail_api_url, build_job_url, build_search_api_url

log = logging.getLogger(__name__)


class HttpJobSource:
    """實作 ports.JobSource。"""

    def __init__(
        self,
        cfg: Config,
        budget: RequestBudget,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.cfg = cfg
        self.budget = budget
        self._client = client
        self._owns_client = client is None
        #: 預算中途用盡時,pipeline 靠它拿到已經抓到的部分
        self.partial_batch: SummaryBatch | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.cfg.scrape.api_wait_seconds,
                follow_redirects=False,
                headers={"User-Agent": self.cfg.scrape.user_agent or "JobFinder/0.1"},
            )
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def requests_used(self) -> int:
        return self.budget.navigations_used

    # ── 單一請求 ────────────────────────────────────────────────────

    async def _get_json(self, url: str, referer: str) -> dict[str, Any]:
        """發一個請求並把三種 403 分流。呼叫端保證已經過 budget。"""
        response = await self._get_client().get(url, headers={"Referer": referer})
        body = response.text

        if response.status_code == 403 and classify_403(body) == "misconfigured":
            # body 空的 403 = origin 的 Referer 規則擋的,是我方請求少了東西。
            # 這不該熔斷 —— 冷卻 24 小時對設定錯誤毫無幫助。
            raise SourceMisconfigured(
                f"104 回 403 但沒有挑戰頁特徵(body 為空),通常代表 Referer 設定不對。"
                f" 送出的 Referer:{referer!r}。URL:{url}"
            )

        reason = detect_block(status=response.status_code, body=body)
        if reason:
            raise ChallengeBlocked(reason, url=url, page_title=body[:120])

        if response.status_code != 200:
            raise TransientScrapeError(f"HTTP {response.status_code}:{url}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise TransientScrapeError(f"回應不是 JSON:{url}") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    # ── ports.JobSource ────────────────────────────────────────────

    async def fetch_summaries(self) -> SummaryBatch:
        from .errors import BudgetExhausted

        batch = SummaryBatch()
        search = self.cfg.search
        referer = self.cfg.scrape.referer

        for i, keyword in enumerate(search.keywords):
            self.budget.start_keyword(keyword)
            try:
                await self._fetch_keyword(keyword, referer, batch, first=(i == 0))
            except BudgetExhausted as exc:
                log.info("抓取預算用盡,停止後續關鍵字:%s", exc)
                batch.truncated_by_budget = True
                break

        self.partial_batch = batch
        return batch

    async def _fetch_keyword(
        self, keyword: str, referer: str, batch: SummaryBatch, *, first: bool
    ) -> None:
        for page_no in range(1, self.cfg.scrape.max_pages_per_keyword + 1):
            if not self.budget.has_page_quota():
                batch.truncated_by_budget = True
                return

            url = build_search_api_url(self.cfg.search, keyword, page_no)
            delay = "none" if (first and page_no == 1) else ("keyword" if page_no == 1 else "page")

            await self.budget.acquire(url, delay=delay, kind="search")
            batch.observed_urls.append(url)
            payload = await self._get_json(url, referer)

            result = normalize_search_response(payload, keyword)
            batch.jobs.extend(result.jobs)
            batch.drift.extend(result.drift)
            log.info(
                "「%s」第 %d/%d 頁:%d 筆(符合條件共 %d 筆)",
                keyword,
                page_no,
                result.total_page or 1,
                len(result.jobs),
                result.total_count,
            )

            if not result.jobs or (result.total_page and page_no >= result.total_page):
                return

    async def fetch_details(self, jobs: list[JobSummary]) -> DetailBatch:
        from .errors import BudgetExhausted

        batch = DetailBatch()
        for job in jobs:
            if not self.budget.has_detail_quota():
                log.info("詳細頁預算用盡,已抓 %d 筆", len(batch.details))
                batch.truncated_by_budget = True
                break

            url = build_detail_api_url(job.detail_id)
            try:
                # Referer 必須是「該職缺自己的頁面」,不是搜尋頁
                await self.budget.acquire(url, delay="detail", kind="detail")
                payload = await self._get_json(url, build_job_url(job.detail_id))
            except BudgetExhausted:
                batch.truncated_by_budget = True
                break
            except TransientScrapeError as exc:
                # 單筆詳細頁失敗不該中斷整輪,深評會退回只用列表資訊
                batch.drift.append(f"{job.job_no}: 詳細頁取得失敗 —— {exc}")
                continue

            batch.details[job.job_no] = normalize_detail_response(payload, job.job_no, batch.drift)

        return batch


@asynccontextmanager
async def http_source(cfg: Config, **_ignored: Any):
    """與 ``playwright_source`` 相同的介面,好讓 pipeline 兩邊都能注入。"""
    budget = RequestBudget(cfg.scrape)
    source = HttpJobSource(cfg, budget)
    try:
        yield source
    finally:
        await source.aclose()
        log.info(
            "本次共 %d 次請求(上限 %d)",
            budget.navigations_used,
            cfg.scrape.max_navigations_per_run,
        )
