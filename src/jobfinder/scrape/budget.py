"""請求預算 —— 所有導覽的唯一入口。

SPEC.md 規則 3 與規則 4 的實作。設計上刻意讓 `RequestBudget.navigate()` 成為
**唯一**能開頁面的方法:節流與計數就不可能被「忘記呼叫」繞過去。

抓取層不應該有任何直接呼叫 `page.goto()` 的地方。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Literal, Protocol

from ..config import ScrapeCfg
from ..errors import BudgetExhausted
from .urls import assert_path_allowed

log = logging.getLogger(__name__)

DelayKind = Literal["page", "keyword", "detail", "none"]


class _Page(Protocol):
    """只用到 goto,讓測試能餵假的 page 物件進來。"""

    async def goto(self, url: str, **kwargs: Any) -> Any: ...


class RequestBudget:
    """全域請求預算 + 強制節流。

    用法::

        budget = RequestBudget(cfg.scrape)
        budget.start_keyword("AI工程師")
        await budget.navigate(page, url, delay="keyword")

    預算耗盡時丟 :class:`BudgetExhausted`。**那不是錯誤** —— pipeline 應該拿
    已抓到的資料正常往下走,也不觸發熔斷。
    """

    def __init__(
        self,
        cfg: ScrapeCfg,
        *,
        sleeper: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self._sleep = sleeper or asyncio.sleep
        self._rng = rng or random.Random()

        self.navigations_used = 0
        self.details_used = 0
        self.pages_this_keyword = 0
        self.current_keyword: str | None = None
        self._navigated_once = False

    # ── 查詢 ────────────────────────────────────────────────────────

    @property
    def navigations_remaining(self) -> int:
        return max(0, self.cfg.max_navigations_per_run - self.navigations_used)

    @property
    def details_remaining(self) -> int:
        return max(0, self.cfg.max_details_per_run - self.details_used)

    def has_page_quota(self) -> bool:
        """還能不能再抓同一關鍵字的下一頁。"""
        return (
            self.pages_this_keyword < self.cfg.max_pages_per_keyword
            and self.navigations_remaining > 0
        )

    def has_detail_quota(self) -> bool:
        return self.details_remaining > 0 and self.navigations_remaining > 0

    # ── 狀態轉換 ────────────────────────────────────────────────────

    def start_keyword(self, keyword: str) -> None:
        self.current_keyword = keyword
        self.pages_this_keyword = 0

    # ── 唯一的導覽入口 ──────────────────────────────────────────────

    async def acquire(
        self,
        url: str,
        *,
        delay: DelayKind = "page",
        kind: Literal["search", "detail"] = "search",
    ) -> None:
        """發出任何請求**之前**都必須先過這裡。

        黑名單檢查 → 預算檢查 → 強制節流 → 計數。

        HTTP 模式與瀏覽器模式共用這個守門員,所以護欄的不變式不會因為多一條路而被繞過。
        呼叫端拿到控制權之後才去實際發請求。

        Raises:
            ForbiddenPath: URL 命中路徑黑名單或 robots.txt 禁用參數(SPEC.md 規則 10)。
            BudgetExhausted: 預算用完,呼叫端應停止抓取但正常收尾。
        """
        assert_path_allowed(url)

        if self.navigations_remaining <= 0:
            raise BudgetExhausted(
                f"已用完本次執行的導覽預算({self.cfg.max_navigations_per_run} 次)"
            )
        if kind == "detail" and self.details_remaining <= 0:
            raise BudgetExhausted(f"已用完詳細頁預算({self.cfg.max_details_per_run} 頁)")
        if kind == "search" and self.pages_this_keyword >= self.cfg.max_pages_per_keyword:
            raise BudgetExhausted(
                f"關鍵字「{self.current_keyword}」已達頁數上限({self.cfg.max_pages_per_keyword} 頁)"
            )

        await self._throttle(delay)

        # 先計數再放行:即使接下來的請求拋例外,這次嘗試也已經送出過封包,
        # 必須算進預算裡。少算會讓實際請求量超出上限。
        self.navigations_used += 1
        if kind == "detail":
            self.details_used += 1
        else:
            self.pages_this_keyword += 1
        self._navigated_once = True

        log.info(
            "request [%d/%d] %s",
            self.navigations_used,
            self.cfg.max_navigations_per_run,
            url,
        )

    async def navigate(
        self,
        page: _Page,
        url: str,
        *,
        delay: DelayKind = "page",
        kind: Literal["search", "detail"] = "search",
    ) -> Any:
        """瀏覽器模式的導覽 —— 就是 :meth:`acquire` 之後加一次 ``page.goto()``。"""
        await self.acquire(url, delay=delay, kind=kind)
        return await page.goto(url, wait_until="domcontentloaded", timeout=self.cfg.page_timeout_ms)

    # ── 節流 ────────────────────────────────────────────────────────

    async def _throttle(self, kind: DelayKind) -> None:
        """帶 jitter 的延遲。固定間隔本身就是機器人指紋,所以一律隨機。"""
        if kind == "none" or not self._navigated_once:
            # 整次執行的第一個請求不需要等
            return
        lo, hi = {
            "page": self.cfg.page_delay_range,
            "keyword": self.cfg.keyword_delay_range,
            "detail": self.cfg.detail_delay_range,
        }[kind]
        seconds = self._rng.uniform(lo, hi)
        log.debug("throttle %.1fs (%s)", seconds, kind)
        await self._sleep(seconds)
