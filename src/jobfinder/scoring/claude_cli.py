"""Claude Code CLI 客戶端 —— 用**訂閱額度**跑評分,不花 API 錢。

介面刻意與 :class:`OpenRouterClient` 相同(``structured()`` 回 ``(物件, 成本)``、
有 ``tracker``、``aclose()``),所以 stage1/stage2/hitrate 不用改一行。

幾個非顯而易見的決定:

- **清掉 ``ANTHROPIC_API_KEY``**:CLI 看到它就改走 API 計費,訂閱額度反而沒用到
- **不用 ``--bare``**:它只認 API key、不讀 OAuth,等於不能用訂閱
- **在空目錄執行 + ``--setting-sources ""`` + ``--strict-mcp-config`` + ``--tools ""``**:
  否則專案 CLAUDE.md、hooks、MCP 工具定義全部會被塞進每一次評分的 context。
  實測剝乾淨後的基底只剩約 170 input token
- ``--system-prompt`` **取代** Claude Code 的預設系統提示(不是附加)
- 沒有 temperature 可調 —— 這是 CLI 的限制,分數雜訊要重新量
- ``cost`` 回的是 CLI 算的 **API 等價成本**(``total_cost_usd``),訂閱下不會真的扣錢,
  但它是「這次吃了多少額度」最穩定的相對指標
- 額度用完(usage limit)**不重試**、直接丟 :class:`ClaudeQuotaExhausted` 讓整批停下
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..errors import JobFinderError, LLMParseError
from .llm_client import CostTracker, OpenRouterClient, last_model_used, parse_structured
from .schemas import to_strict_schema

log = logging.getLogger(__name__)

#: 訊息裡出現這些字樣就視為額度耗盡 —— 重試只會白白卡在同一面牆上
_QUOTA_MARKERS = ("usage limit", "rate limit", "limit reached", "rate_limit", "overloaded")


class ClaudeCliError(JobFinderError):
    """CLI 回報錯誤(非額度問題)。"""


class ClaudeQuotaExhausted(ClaudeCliError):
    """訂閱額度用完或被限流。**不重試**,呼叫端應中止整批。"""


@dataclass
class CallUsage:
    """單次呼叫的用量。探測腳本拿來估「重評全部舊資料」要吃多少額度。"""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float  # API 等價成本,訂閱下不實付
    duration_s: float
    num_turns: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def _usage_from(payload: dict[str, Any], model: str, duration_s: float) -> CallUsage:
    usage = payload.get("usage") or {}
    return CallUsage(
        model=model,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        duration_s=round(duration_s, 2),
        num_turns=int(payload.get("num_turns") or 0),
    )


class ClaudeCliClient:
    def __init__(
        self,
        *,
        tracker: CostTracker | None = None,
        timeout_seconds: float = 300.0,
        effort: str | None = None,
        executable: str | None = None,
        max_concurrent: int = 2,
    ) -> None:
        exe = executable or shutil.which("claude")
        if not exe:
            raise ClaudeCliError("找不到 claude CLI。先安裝 Claude Code 並登入訂閱帳號。")
        self.executable = exe
        #: 預設上限給很大:訂閱下 cost 只是等價值,真正的上限是額度,由 ClaudeQuotaExhausted 把關
        self.tracker = tracker or CostTracker(1_000.0)
        self.timeout_seconds = timeout_seconds
        self.effort = effort
        self.calls: list[CallUsage] = []
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # 固定的空目錄:不讓 CLI 自動吃到專案的 CLAUDE.md。不每次 mkdtemp ——
        # pipeline 不一定會呼叫 aclose(),那樣每天會留下一個殘骸
        self._workdir = Path(tempfile.gettempdir()) / "jobfinder-claude-cwd"
        self._workdir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        """沒有要釋放的資源;留著是為了與 OpenRouterClient 介面一致。"""

    def _argv(self, model: str, system: str, schema: dict[str, Any]) -> list[str]:
        argv = [
            self.executable,
            "-p",
            "--model", model,
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False),
        ]  # fmt: skip
        if self.effort:
            argv += ["--effort", self.effort]
        return argv

    async def structured[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        temperature: float = 0.2,  # noqa: ARG002 —— CLI 不支援,只為了介面相容
    ) -> tuple[T, float]:
        async with self._semaphore:
            payload, usage = await self._run(model, system, user, to_strict_schema(schema_model))
        last_model_used.set(f"claude-cli:{model}")
        self.calls.append(usage)
        self.tracker.add(usage.cost_usd)

        structured = payload.get("structured_output")
        if isinstance(structured, dict):
            return schema_model.model_validate(structured), usage.cost_usd
        # 沒有 structured_output 時退回解析 result 文字(與 OpenRouter 路徑共用容錯)
        return parse_structured(str(payload.get("result") or ""), schema_model), usage.cost_usd

    async def _run(
        self, model: str, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], CallUsage]:
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *self._argv(model, system, schema),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workdir,
            env=_scrubbed_env(),
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(user.encode("utf-8")), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            raise ClaudeCliError(f"claude CLI 逾時({self.timeout_seconds}s)") from None
        duration = time.monotonic() - started

        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            self._raise_for_text(stdout + "\n" + stderr, proc.returncode)
            raise LLMParseError(stdout[:500]) from None

        if payload.get("is_error") or payload.get("subtype") != "success":
            self._raise_for_text(json.dumps(payload, ensure_ascii=False), proc.returncode)
            raise ClaudeCliError(f"claude CLI 失敗:{str(payload.get('result'))[:300]}")

        return payload, _usage_from(payload, model, duration)

    @staticmethod
    def _raise_for_text(text: str, returncode: int | None) -> None:
        lowered = text.lower()
        if any(marker in lowered for marker in _QUOTA_MARKERS):
            raise ClaudeQuotaExhausted(text[:300])
        if returncode not in (0, None):
            raise ClaudeCliError(f"claude CLI 結束碼 {returncode}:{text[:300]}")


class ClaudeWithFallback:
    """Claude 訂閱優先,失敗就退回 OpenRouter。介面同 :class:`OpenRouterClient`。

    - 呼叫端照舊傳 config 裡的 OpenRouter model id;這裡用 ``model_map`` 換成 Claude 的
    - **一旦 Claude 失敗,這次執行剩下的呼叫全部走 OpenRouter**(黏著)。CLI 壞掉或額度
      用完通常不是只壞一次,每筆都先撞一次 Claude 只會讓每日排程拖上好幾分鐘
    - 回傳的 cost 是**實付金額**:Claude 路徑回 0(訂閱不另計費),等價成本記在
      ``claude.calls``。``tracker`` 是 OpenRouter 的那個 —— ``daily_cost_cap_usd``
      只該攔真的會花錢的呼叫,否則 Claude 的等價成本會讓上限每天都被吃光
    """

    def __init__(
        self,
        claude: ClaudeCliClient,
        fallback: OpenRouterClient | None,
        model_map: dict[str, str],
    ) -> None:
        self.claude = claude
        self.fallback = fallback
        self.model_map = model_map
        self.tracker = fallback.tracker if fallback is not None else CostTracker(1_000.0)
        #: 退回的原因(None = 還在用 Claude)。pipeline 可以把它放進日報的警告
        self.fell_back: str | None = None

    async def aclose(self) -> None:
        await self.claude.aclose()
        if self.fallback is not None:
            await self.fallback.aclose()

    async def structured[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        temperature: float = 0.2,
    ) -> tuple[T, float]:
        if self.fell_back is None:
            try:
                result, _ = await self.claude.structured(
                    model=self.model_map.get(model, model),
                    system=system,
                    user=user,
                    schema_model=schema_model,
                    schema_name=schema_name,
                )
                return result, 0.0
            except (ClaudeCliError, LLMParseError) as exc:
                if self.fallback is None:
                    raise
                # 並行時可能好幾個 task 同時失敗 —— 只記第一個原因
                if self.fell_back is None:
                    self.fell_back = f"{type(exc).__name__}: {str(exc)[:200]}"
                    log.warning("Claude 評分失敗,本次執行改走 OpenRouter:%s", self.fell_back)

        if self.fallback is None:
            raise ClaudeCliError(f"Claude 已失敗且沒有 OpenRouter 退路:{self.fell_back}")
        return await self.fallback.structured(
            model=model,
            system=system,
            user=user,
            schema_model=schema_model,
            schema_name=schema_name,
            temperature=temperature,
        )
