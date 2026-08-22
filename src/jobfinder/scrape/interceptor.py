"""攔截頁面自己發出的 API 回應。

這是整個抓取策略的核心:我們**不解析 DOM**,而是讓真實瀏覽器去打 104 的內部 API
(Cloudflare 由瀏覽器自己過),我們只在旁邊讀它拿到的 JSON。既通過了挑戰,
又直接拿到結構化欄位。

攔不到目標 API 時,`observed_urls` 會記下該頁所有 XHR —— 104 改版時可以直接從
log 看出新路徑叫什麼(SPEC.md 風險 2)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

log = logging.getLogger(__name__)


class ResponseInterceptor:
    """掛在 page 上,把符合 pattern 的 JSON 回應收進佇列。

    務必在 ``goto()`` **之前** attach,不然回應早就飛過去了。
    """

    def __init__(self, page: Any, patterns: dict[str, str]) -> None:
        self.page = page
        self.patterns = patterns
        self.queues: dict[str, asyncio.Queue] = {name: asyncio.Queue() for name in patterns}
        self.observed_urls: list[str] = []
        self._tasks: set[asyncio.Task] = set()
        self._attached = False

    def attach(self) -> ResponseInterceptor:
        if not self._attached:
            self.page.on("response", self._on_response)
            self._attached = True
        return self

    def detach(self) -> None:
        if self._attached:
            with contextlib.suppress(Exception):
                self.page.remove_listener("response", self._on_response)
            self._attached = False
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def clear(self, name: str) -> None:
        """換頁前清掉上一頁殘留的回應,避免拿到過期資料。"""
        queue = self.queues.get(name)
        while queue is not None and not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()

    def _on_response(self, response: Any) -> None:
        url = str(getattr(response, "url", ""))
        self._record_url(response, url)

        for name, pattern in self.patterns.items():
            if pattern in url:
                task = asyncio.create_task(self._capture(name, response))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def _record_url(self, response: Any, url: str) -> None:
        with contextlib.suppress(Exception):
            if response.request.resource_type in ("xhr", "fetch"):
                self.observed_urls.append(url)

    async def _capture(self, name: str, response: Any) -> None:
        try:
            payload = await response.json()
        except Exception:
            log.debug("回應不是 JSON,略過:%s", getattr(response, "url", ""))
            return
        if isinstance(payload, dict):
            await self.queues[name].put(payload)

    async def wait_for(self, name: str, timeout: float) -> dict[str, Any] | None:
        """等下一個符合的 JSON 回應。逾時回 None(呼叫端決定是改版還是單純沒資料)。"""
        try:
            return await asyncio.wait_for(self.queues[name].get(), timeout)
        except TimeoutError:
            log.warning(
                "等待 %s 回應逾時 %.0fs。該頁觀察到的 XHR:%s",
                name,
                timeout,
                self.observed_urls[-10:] or "(無)",
            )
            return None
