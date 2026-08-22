"""模組之間的介面。

這些 Protocol 不只是為了測試好寫。`JobSource` 尤其是**風險備案的支點**:
若 Modal 的資料中心 IP 過不了 Cloudflare,只要換一個「從本機餵 raw JSON 進來」的
實作,`pipeline.py` 一行都不用改(見 SPEC.md 風險 1 的備案 ④)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .models import JobDetail, JobSummary, RejectedJob, RunReport, ScoredJob


@dataclass
class SummaryBatch:
    jobs: list[JobSummary] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    #: 攔截到的 XHR URL。攔不到目標 API 時靠這個看出 104 把路徑改成什麼了。
    observed_urls: list[str] = field(default_factory=list)
    truncated_by_budget: bool = False


@dataclass
class DetailBatch:
    details: dict[str, JobDetail] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    truncated_by_budget: bool = False


@dataclass
class ScreenOutcome:
    keep: list[JobSummary] = field(default_factory=list)
    dropped: list[RejectedJob] = field(default_factory=list)
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class JobSource(Protocol):
    """職缺來源。真實實作是 Playwright;測試與 --from-fixtures 用檔案實作。"""

    async def fetch_summaries(self) -> SummaryBatch: ...

    async def fetch_details(self, jobs: list[JobSummary]) -> DetailBatch:
        """傳整個 JobSummary 而不是 job_no。

        因為詳細頁 API 只吃 ``detail_id``(連結尾段),拿 ``job_no`` 去打會 404 ——
        來源端需要同時看得到兩個 ID。回傳的 dict 仍以 ``job_no`` 為 key。
        """
        ...

    @property
    def requests_used(self) -> int:
        """實際用掉的 HTTP 導覽數。寫進 runs 表稽核 —— 這個數字失控代表護欄有漏洞。"""
        ...


@runtime_checkable
class Scorer(Protocol):
    """兩階段 LLM 評分。"""

    async def screen(self, jobs: list[JobSummary]) -> ScreenOutcome: ...

    async def deep(self, job: JobSummary, detail: JobDetail | None) -> tuple[ScoredJob, float]:
        """回傳 (評分結果, 這次呼叫的成本 USD)。"""
        ...


@runtime_checkable
class Notifier(Protocol):
    """通知管道。刻意與 104 完全無關,所以被擋時告警一定送得出去。"""

    async def send_summary(self, report: RunReport) -> None: ...

    async def send_job(self, scored: ScoredJob) -> None: ...

    async def send_alert(self, title: str, lines: list[str]) -> None: ...
