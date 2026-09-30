"""投遞清單補抓詳細頁 —— **會連 104,只有 CLI 可以 import。**

護欄跟每日 pipeline 一樣,一條都沒少:

* 緊急開關、熔斷器都由呼叫端(CLI)在發請求之前檢查
* 每個請求都走 ``HttpJobSource.fetch_details`` → ``RequestBudget.acquire()``
  (節流、計數、路徑黑名單),Referer 是該職缺自己的頁面
* 抓成功的、104 說「職務不存在」的,都不會再抓第二次
* 封鎖訊號(``ChallengeBlocked`` 等)**原樣往上丟**,不重試 —— 呼叫端負責熔斷

**不寫 jobs.db 的 runs 表**:``has_run_today`` 會把它算成今天的執行,擋掉 08:00 的日報。
稽核紀錄在 shortlist.db 的 ``fetches``(每次嘗試一列)。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..models import JobSummary
from ..normalize import DETAIL_NOT_FOUND, detail_error
from ..ports import JobSource
from ..scrape.urls import build_job_url
from .store import Entry, ShortlistStore


@dataclass
class FetchReport:
    fetched: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # 104 說職務不存在(多半已關閉)
    failed: list[tuple[str, str]] = field(default_factory=list)  # 暫時失敗,下次再抓
    stopped_by_budget: bool = False


async def fetch_entries(
    entries: Iterable[Entry],
    source: JobSource,
    store: ShortlistStore,
    clock: Callable[[], datetime],
) -> FetchReport:
    """逐筆抓、逐筆存:中途被擋時,已經抓到的不會丟。"""
    report = FetchReport()
    for entry in entries:
        summary = JobSummary(
            job_no=entry.detail_id,  # 詳細頁 API 沒有 jobNo,這裡只當批次內的鍵
            detail_id=entry.detail_id,
            job_name=entry.title,
            cust_name=entry.company,
            job_url=build_job_url(entry.detail_id),
        )
        batch = await source.fetch_details([summary])
        detail = batch.details.get(entry.detail_id)
        if detail is None:
            if batch.truncated_by_budget:
                report.stopped_by_budget = True
                break
            reason = "; ".join(batch.drift) or "詳細頁取得失敗"
            store.record_fetch(entry.detail_id, clock(), error=reason)
            report.failed.append((entry.detail_id, reason))
            continue

        if (err := detail_error(detail.raw)) is not None:
            code, message = err
            gone = code == DETAIL_NOT_FOUND
            store.record_fetch(entry.detail_id, clock(), error=f"{code} {message}", gone=gone)
            if gone:
                report.gone.append(entry.detail_id)
            else:
                report.failed.append((entry.detail_id, message))
            continue

        store.record_fetch(
            entry.detail_id, clock(), raw_detail=json.dumps(detail.raw, ensure_ascii=False)
        )
        report.fetched.append(entry.detail_id)
    return report
