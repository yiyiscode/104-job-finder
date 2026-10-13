"""投遞清單的深評 —— **不連 104**,只用 ``shortlist fetch`` 已經抓回來的詳細頁。

跟每日 pipeline 用同一個評分器(``Scorer.deep``):同一份履歷、同一個 prompt、
同一套程式端校正,所以分數跟候選清單裡其他職缺同一個口徑,可以直接比。

只評「不在 jobs.db」的:在 jobs.db 的那筆由 pipeline 自己評,UI 也以那邊為準。
結果寫 shortlist.db 的 ``scores``,**不寫 jobs.db**(pipeline 會整檔覆蓋它)。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..models import JobDetail, JobSummary
from ..normalize import detail_as_summary, normalize_detail_response, normalize_search_response
from ..ports import Scorer
from .store import Entry, ShortlistStore

#: 不 import scrape.urls:投遞清單這側除了 fetch.py 都不准碰 scrape(test_webui_boundary)
JOB_URL = "https://www.104.com.tw/job/"

log = logging.getLogger(__name__)


@dataclass
class ScoreReport:
    scored: list[tuple[str, int]] = field(default_factory=list)  # (detail_id, 總分)
    failed: list[tuple[str, str]] = field(default_factory=list)


def job_from_detail(detail_id: str, raw_detail: str) -> tuple[JobSummary, JobDetail]:
    """詳細頁原始 JSON → 評分器要的 (摘要, 全文)。詳細頁沒有 jobNo,用 detail_id 當鍵。"""
    payload = json.loads(raw_detail)
    shape = detail_as_summary(payload)
    shape["jobNo"] = detail_id
    shape["link"] = {**(shape.get("link") or {}), "job": JOB_URL + detail_id}
    page = normalize_search_response({"data": [shape]}, "")
    if not page.jobs:
        raise ValueError(f"{detail_id}: 詳細頁轉不出摘要({'; '.join(page.drift)})")
    return page.jobs[0], normalize_detail_response(payload, detail_id)


async def score_entries(
    entries: Iterable[Entry],
    store: ShortlistStore,
    scorer: Scorer,
    clock: Callable[[], datetime],
) -> ScoreReport:
    """逐筆評、逐筆存:中途失敗或中斷,已經評好的不會丟。"""
    report = ScoreReport()
    fetches = store.latest_fetches()
    for entry in entries:
        fetch = fetches.get(entry.detail_id)
        if fetch is None or not fetch.raw_detail:
            report.failed.append((entry.detail_id, "還沒有詳細頁全文"))
            continue
        try:
            summary, detail = job_from_detail(entry.detail_id, fetch.raw_detail)
            scored, _ = await scorer.deep(summary, detail)
        except Exception as exc:  # noqa: BLE001 —— 一筆壞掉不該擋住其他筆
            log.warning("%s 深評失敗:%s", entry.detail_id, exc)
            report.failed.append((entry.detail_id, str(exc)[:200]))
            continue
        # raw_responses 不在 Scorer 協定裡,但兩個實作都有
        raw = getattr(scorer, "raw_responses", {}).get(summary.job_no, "")
        store.record_score(entry.detail_id, scored, raw, clock())
        report.scored.append((entry.detail_id, scored.total))
    return report
