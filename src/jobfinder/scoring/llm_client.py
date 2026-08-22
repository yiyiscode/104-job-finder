"""OpenRouter 客戶端。

結構化輸出的**三層保險**(便宜模型常常不乖乖吐 JSON):

1. ``response_format: json_schema`` + ``provider.require_parameters`` —— 只路由到
   真的支援 structured output 的 provider
2. 容錯解析 —— 剝掉 ``` 圍籬、抓首尾大括號
3. 修復輪 —— 把原始輸出丟回去要求重輸出
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import LLMCfg
from ..errors import LLMParseError
from .prompts import RETRY_JSON
from .schemas import to_strict_schema

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class CostTracker:
    """累計花費並在超過上限時擋下後續呼叫(config ``llm.daily_cost_cap_usd``)。"""

    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self.total = 0.0

    def add(self, cost: float) -> None:
        self.total += max(0.0, cost)

    @property
    def exceeded(self) -> bool:
        return self.total >= self.cap


def _json_candidates(text: str) -> Iterator[str]:
    """從模型輸出裡挖出可能的 JSON 片段,由最可能到最將就。"""
    stripped = text.strip()
    yield stripped
    if (m := re.search(r"```(?:json)?\s*(.*?)```", text, re.S)) is not None:
        yield m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        yield text[start : end + 1]


def parse_structured[T: BaseModel](text: str, schema_model: type[T]) -> T:
    """第 2 層保險。全部候選都失敗才丟 LLMParseError。"""
    for candidate in _json_candidates(text):
        try:
            return schema_model.model_validate_json(candidate)
        except (ValidationError, json.JSONDecodeError, ValueError):
            continue
    raise LLMParseError(text)


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        cfg: LLMCfg,
        *,
        client: httpx.AsyncClient | None = None,
        tracker: CostTracker | None = None,
    ) -> None:
        self.cfg = cfg
        self.tracker = tracker or CostTracker(cfg.daily_cost_cap_usd)
        self._client = client
        self._owns_client = client is None
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": cfg.referer,
            "X-Title": cfg.title,
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
            self._owns_client = True
        return self._client

    async def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        temperature: float = 0.2,
    ) -> tuple[T, float]:
        """回傳 (解析後的物件, 這次呼叫的成本 USD)。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        text, cost = await self._call(model, messages, schema_model, schema_name, temperature)

        try:
            return parse_structured(text, schema_model), cost
        except LLMParseError:
            log.warning("模型輸出無法解析,啟動修復輪(model=%s)", model)

        # 第 3 層:把壞掉的輸出丟回去要求重輸出。對便宜模型成功率很高。
        repair = [
            *messages,
            {"role": "assistant", "content": text},
            {"role": "user", "content": RETRY_JSON.format(raw=text[:2000])},
        ]
        repaired, repair_cost = await self._call(model, repair, schema_model, schema_name, 0.0)
        return parse_structured(repaired, schema_model), cost + repair_cost

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=20),
        reraise=True,
    )
    async def _call(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema_model: type[BaseModel],
        schema_name: str,
        temperature: float,
    ) -> tuple[str, float]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": to_strict_schema(schema_model),
                },
            },
            # 第 1 層:只路由到真的支援 structured output 的 provider
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }

        response = await self._get_client().post(
            f"{self.cfg.base_url}/chat/completions", headers=self.headers, json=body
        )
        response.raise_for_status()
        payload = response.json()

        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(json.dumps(payload)[:500]) from exc

        cost = float((payload.get("usage") or {}).get("cost") or 0.0)
        self.tracker.add(cost)
        return text, cost
