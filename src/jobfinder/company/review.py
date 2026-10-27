"""逐家問 ``claude -p``(開 WebSearch/WebFetch)→ 清洗 → 存 companies.db。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..scoring.claude_cli import ClaudeCliClient, ClaudeQuotaExhausted
from .schema import CompanyReview, sanitize
from .store import CompanyStore

log = logging.getLogger(__name__)

#: 權限層封鎖,不靠 prompt:CLI 直接拒絕這些呼叫
DENIED_TOOLS = ("WebFetch(domain:104.com.tw)", "WebFetch(domain:www.104.com.tw)")
WEB_TOOLS = ("WebSearch", "WebFetch")
#: 連續失敗這麼多家就停 —— 多半是 CLI 沒登入或網搜壞了,繼續只是空轉
MAX_CONSECUTIVE_FAILURES = 2

SYSTEM_COMPANY = """你是台灣求職者的公司調查助理。
用網路搜尋整理一家公司的「員工評價」與「徵才官網」。

規則:
1. 只根據你這次實際搜尋/讀到的內容寫,不要用印象補。找不到就寫「資料不足」,tone 給 unknown。
2. 評價來源優先:面試趣(interview.tw)、PTT(Tech_Job、Soft_Job、job)、Dcard 工作板、Glassdoor、
   1111 企業評價、新聞。**不要讀或引用 104.com.tw 的任何頁面**(那是職缺,不是評價)。
3. 公司有多個事業部或子公司時,以使用者給的職缺部門為主;評論講的是別的部門要在摘要裡說明。
4. 舊評論(3 年以上)要在摘要裡註明年份,不要當成現況。
5. sources 列出你實際引用的頁面(網址 + 標題 + YYYY-MM;看不出日期寫「不明」),
   每個面向至少要有一個來源,不然那個面向就是「資料不足」。
6. career_url 是**公司自己的**招募/徵才頁(例如 careers.xxx.com、官網的「人才招募」頁),
   用 WebFetch 確認它存在。
   104、1111、LinkedIn、CakeResume 等人力銀行或求職平台的頁面不算 —— 只有這些的話給空字串,
   並在 career_note 寫「只在人力銀行刊登」。
7. career_note 寫怎麼投:官網系統要另建履歷、是否有對應職缺、只收 104 等。
8. 一律台灣繁體中文。
   tone:good = 多數正面、mixed = 好壞參半或依部門而定、bad = 多數負面、unknown = 資料不足。"""


def build_company_user(company: str, job_titles: list[str]) -> str:
    jobs = "\n".join(f"- {t}" for t in job_titles[:8]) or "- (無)"
    return f"公司:{company}\n\n使用者在考慮的職缺(看部門用):\n{jobs}"


class StructuredClient(Protocol):
    async def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_model: type[Any],
        schema_name: str,
        temperature: float = 0.2,
    ) -> tuple[Any, float]: ...


def web_client(timeout_seconds: float) -> ClaudeCliClient:
    return ClaudeCliClient(
        timeout_seconds=timeout_seconds, max_concurrent=1, tools=WEB_TOOLS, denied=DENIED_TOOLS
    )


@dataclass
class ReviewReport:
    candidates: int = 0
    done: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped_reason: str = ""

    def summary_line(self) -> str:
        line = (
            f"公司評價:待查 {self.candidates} 家 → 完成 {len(self.done)} 家、"
            f"失敗 {len(self.failed)} 家"
        )
        return line + (f" · 中止:{self.stopped_reason}" if self.stopped_reason else "")


async def run_reviews(
    targets: list[tuple[str, list[str]]],
    *,
    store: CompanyStore,
    client: StructuredClient,
    model: str,
    now_fn,
) -> ReviewReport:
    """targets = [(公司名, 職缺標題們)]。逐家序列查,一家約 1–3 分鐘。"""
    report = ReviewReport(candidates=len(targets))
    consecutive = 0
    for name, titles in targets:
        store.mark_running(name, now_fn())
        try:
            review, _ = await client.structured(
                model=model,
                system=SYSTEM_COMPANY,
                user=build_company_user(name, titles),
                schema_model=CompanyReview,
                schema_name="company_review",
            )
        except ClaudeQuotaExhausted as exc:
            store.save_error(name, f"額度用完:{exc}", now_fn())
            report.failed.append((name, str(exc)))
            report.stopped_reason = "Claude 額度用完"
            break
        except Exception as exc:  # noqa: BLE001 —— 單家失敗不拖垮整批
            store.save_error(name, f"{type(exc).__name__}: {exc}", now_fn())
            report.failed.append((name, str(exc)))
            consecutive += 1
            log.warning("%s 公司評價失敗:%s", name, exc)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                report.stopped_reason = f"連續失敗 {consecutive} 家"
                break
            continue
        consecutive = 0
        store.save_ok(name, sanitize(review), model, now_fn())
        report.done.append(name)
        log.info("%s 公司評價完成:%s", name, review.overall)
    return report
