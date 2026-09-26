"""Telegram Bot API。

節流 1.2 秒/則(單一聊天室建議 < 1 msg/sec)。預設 ``notify.style: digest`` 每天只發 1 則;
``cards`` 模式最多 1 + 10 = 11 則。仍要正確處理 429 的 ``retry_after``。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from ..config import NotifyCfg, ScoringCfg
from ..errors import NotifyFailed
from ..models import RunReport, ScoredJob
from .format import render_alert, render_job_card, render_summary, split_message, strip_tags

log = logging.getLogger(__name__)


class TelegramNotifier:
    """實作 ports.Notifier。"""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        notify: NotifyCfg | None = None,
        scoring: ScoringCfg | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Any = None,
    ) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.cfg = notify or NotifyCfg()
        self.scoring = scoring or ScoringCfg()
        self._client = client
        self._owns_client = client is None
        self._sleep = sleeper or asyncio.sleep
        self._last_sent_at: float | None = None

    # ── ports.Notifier ─────────────────────────────────────────────

    async def send_summary(self, report: RunReport) -> None:
        text = render_summary(
            report,
            threshold=self.scoring.threshold,
            max_rejected=(
                self.scoring.max_rejected_listed if self.scoring.list_rejected_in_summary else 0
            ),
            mode=self.scoring.mode,
            top_n=self.scoring.top_n,
            digest=self.cfg.style == "digest",
        )
        await self.send(text)

    async def send_job(self, scored: ScoredJob) -> None:
        text, keyboard = render_job_card(scored, threshold=self.scoring.threshold)
        await self.send(text, keyboard=keyboard)

    async def send_alert(self, title: str, lines: list[str]) -> None:
        await self.send(render_alert(title, lines))

    # ── 底層 ───────────────────────────────────────────────────────

    async def send(self, text: str, *, keyboard: dict | None = None) -> None:
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": self.cfg.parse_mode,
            }
            if self.cfg.disable_link_preview:
                payload["link_preview_options"] = {"is_disabled": True}
            # 按鈕只掛最後一段,不然每段都跳一組
            if keyboard and i == len(chunks) - 1:
                payload["reply_markup"] = keyboard
            await self._post("sendMessage", payload)

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        if self._last_sent_at is not None:
            wait = self.cfg.min_interval_seconds - (loop.time() - self._last_sent_at)
            if wait > 0:
                await self._sleep(wait)
        self._last_sent_at = loop.time()

    async def _post(self, method: str, payload: dict[str, Any], retries: int = 3) -> Any:
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            for _attempt in range(retries):
                await self._throttle()
                response = await client.post(f"{self.base}/{method}", json=payload)

                if response.status_code == 429:
                    retry_after = 5
                    with contextlib.suppress(Exception):
                        retry_after = int(
                            response.json().get("parameters", {}).get("retry_after", 5)
                        )
                    log.warning("Telegram 429,%s 秒後重試", retry_after)
                    await self._sleep(retry_after + 1)
                    continue

                if response.status_code == 400 and payload.get("parse_mode"):
                    # 多半是 HTML 沒跳脫乾淨。降級成純文字重送一次 ——
                    # 寧可醜也不要整則訊息消失。
                    log.warning(
                        "Telegram 400(HTML 解析失敗),降級為純文字重送:%s",
                        response.text[:200],
                    )
                    payload = dict(payload)
                    payload.pop("parse_mode", None)
                    payload["text"] = strip_tags(payload["text"])
                    continue

                if response.status_code >= 500:
                    await self._sleep(2)
                    continue

                if response.status_code != 200:
                    raise NotifyFailed(
                        f"Telegram {method} 失敗 {response.status_code}: {response.text[:300]}"
                    )
                return response.json()

            raise NotifyFailed(f"Telegram {method} 重試 {retries} 次仍失敗")
        finally:
            if self._owns_client:
                await client.aclose()


class ConsoleNotifier:
    """`--dry-run` 用。把訊息印到終端,並存一份 HTML 方便用瀏覽器預覽排版。"""

    def __init__(
        self,
        scoring: ScoringCfg | None = None,
        out_path: str | None = None,
        notify: NotifyCfg | None = None,
    ):
        self.scoring = scoring or ScoringCfg()
        self.cfg = notify or NotifyCfg()
        self.out_path = out_path
        self.messages: list[str] = []

    async def send_summary(self, report: RunReport) -> None:
        self._emit(
            render_summary(
                report,
                threshold=self.scoring.threshold,
                max_rejected=self.scoring.max_rejected_listed,
                mode=self.scoring.mode,
                top_n=self.scoring.top_n,
                digest=self.cfg.style == "digest",
            )
        )

    async def send_job(self, scored: ScoredJob) -> None:
        text, _ = render_job_card(scored, threshold=self.scoring.threshold)
        self._emit(text)

    async def send_alert(self, title: str, lines: list[str]) -> None:
        self._emit(render_alert(title, lines))

    def _emit(self, text: str) -> None:
        self.messages.append(text)
        print("\n" + "─" * 60)
        print(strip_tags(text))

    def save_html(self) -> str | None:
        if not self.out_path:
            return None
        body = "\n<hr>\n".join(f"<div class='msg'>{m}</div>" for m in self.messages)
        html_doc = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>職缺日報預覽</title>"
            "<style>body{font-family:system-ui,'Microsoft JhengHei',sans-serif;"
            "max-width:640px;margin:2rem auto;padding:0 1rem;line-height:1.6}"
            ".msg{white-space:pre-wrap;background:#f6f8fa;border-radius:12px;"
            "padding:1rem;margin:1rem 0}</style>" + body
        )
        from pathlib import Path

        Path(self.out_path).write_text(html_doc, encoding="utf-8")
        return self.out_path
