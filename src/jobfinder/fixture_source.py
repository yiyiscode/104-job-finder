"""從錄好的 JSON 讀職缺,完全不碰網路。

這是 `--from-fixtures` 背後的實作,也是 SPEC.md 規則 6 的落實:錄一次真實回應之後,
normalize、去重、評分 prompt、Telegram 排版全部可以離線反覆迭代。

它同時是風險 1 備案 ④ 的雛型 —— 證明 ``JobSource`` 這個介面確實能換掉抓取來源。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from .models import JobSummary
from .normalize import normalize_detail_response, normalize_search_response
from .ports import DetailBatch, SummaryBatch

log = logging.getLogger(__name__)


class FixtureJobSource:
    """實作 ports.JobSource。"""

    def __init__(self, fixture_dir: str | Path, keyword: str = "AI工程師") -> None:
        self.dir = Path(fixture_dir)
        self.keyword = keyword

    @property
    def requests_used(self) -> int:
        return 0  # 一次網路請求都沒有,這正是重點

    async def fetch_summaries(self) -> SummaryBatch:
        batch = SummaryBatch()
        pages = sorted(self.dir.glob("search_page*.json"))
        if not pages:
            log.warning("在 %s 找不到 search_page*.json", self.dir)
            return batch

        for path in pages:
            payload = json.loads(path.read_text(encoding="utf-8"))
            page = normalize_search_response(payload, self.keyword)
            batch.jobs.extend(page.jobs)
            batch.drift.extend(page.drift)
        return batch

    async def fetch_details(self, jobs: list[JobSummary]) -> DetailBatch:
        """嚴格按 ``detail_{detail_id}.json`` 對應。

        檔名用 ``detail_id`` 而不是 ``job_no``,因為真實 API 就是用它當 key ——
        fixture 的命名跟著真實世界走,才不會養出「本機會過、上線就 404」的假象。

        對不到就**不給 detail**,而不是隨便套一份別的 —— 套錯會讓「8年以上」的
        資深缺配到「經歷不拘」的詳細頁,預覽出來的分數完全是假的,反而誤導。
        評分端本來就有「沒有詳細內容時保守評估」的路徑,走那條才誠實。
        """
        batch = DetailBatch()
        for job in jobs:
            path = self.dir / f"detail_{job.detail_id}.json"
            if not path.exists():
                # 刻意**不記進 drift**:drift 是用來偵測「104 改版了」的訊號,
                # 而「本機只錄了幾份 detail」是完全預期的事。混在一起會讓真正的
                # 改版警告被雜訊淹沒。
                log.debug("%s: fixture 無對應 detail,只用列表資訊評分", job.job_no)
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            batch.details[job.job_no] = normalize_detail_response(payload, job.job_no, batch.drift)
        return batch


@asynccontextmanager
async def fixture_source(fixture_dir: str | Path, keyword: str = "AI工程師"):
    yield FixtureJobSource(fixture_dir, keyword)


class ReplayJobSource:
    """從 DB 裡某次 run 的原始 JSON 重播(`--replay`)。

    調 prompt 的神器:拿昨天真實抓到的資料反覆重跑評分,完全不需要再碰 104。
    """

    def __init__(self, pairs) -> None:
        self._pairs = pairs

    @property
    def requests_used(self) -> int:
        return 0

    async def fetch_summaries(self) -> SummaryBatch:
        return SummaryBatch(jobs=[summary for summary, _ in self._pairs])

    async def fetch_details(self, jobs: list[JobSummary]) -> DetailBatch:
        wanted = {j.job_no for j in jobs}
        return DetailBatch(
            details={
                summary.job_no: detail
                for summary, detail in self._pairs
                if detail is not None and summary.job_no in wanted
            }
        )


@asynccontextmanager
async def replay_source(pairs):
    yield ReplayJobSource(pairs)
