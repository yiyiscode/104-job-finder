"""Claude 訂閱評分(claude -p)與 OpenRouter 退路。全部離線:子程序用假的取代。"""

from __future__ import annotations

import json

import httpx
import pytest

from jobfinder.config import load_config
from jobfinder.errors import LLMParseError
from jobfinder.scoring import claude_cli
from jobfinder.scoring.claude_cli import (
    ClaudeCliClient,
    ClaudeCliError,
    ClaudeQuotaExhausted,
    ClaudeWithFallback,
)
from jobfinder.scoring.llm_client import OpenRouterClient, last_model_used
from jobfinder.scoring.schemas import ScreenResult

SUCCESS = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 2,
    "total_cost_usd": 0.066,
    "result": "",
    "structured_output": {"results": []},
    "usage": {
        "input_tokens": 2,
        "output_tokens": 2500,
        "cache_read_input_tokens": 2633,
        "cache_creation_input_tokens": 0,
    },
}


class FakeProc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self._stdout = stdout.encode("utf-8")
        self.returncode = returncode
        self.stdin_seen = b""

    async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
        self.stdin_seen = data
        return self._stdout, b""

    def kill(self) -> None:  # pragma: no cover - 逾時路徑
        pass


@pytest.fixture
def fake_cli(monkeypatch):
    """把 asyncio.create_subprocess_exec 換成假的;回傳記錄每次呼叫的 list。"""
    seen: list[dict] = []
    replies: list[tuple[str, int]] = []

    async def fake_exec(*argv, **kwargs):
        stdout, code = replies.pop(0) if replies else (json.dumps(SUCCESS), 0)
        proc = FakeProc(stdout, code)
        seen.append({"argv": list(argv), "kwargs": kwargs, "proc": proc})
        return proc

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    return seen, replies


def make_cli() -> ClaudeCliClient:
    return ClaudeCliClient(executable="claude")


async def call(client, model: str = "sonnet"):
    return await client.structured(
        model=model, system="sys", user="職缺內容", schema_model=ScreenResult, schema_name="s"
    )


# ── ClaudeCliClient ──────────────────────────────────────────────────


async def test_cli_is_stripped_of_tools_mcp_and_project_context(fake_cli, monkeypatch):
    """少了任何一個,專案 CLAUDE.md / hooks / MCP 工具定義就會塞進每一次評分。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    seen, _ = fake_cli
    await call(make_cli())

    argv = seen[0]["argv"]
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--json-schema"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "sys"
    assert "--bare" not in argv  # --bare 只認 API key,等於不能用訂閱
    # 清掉 API key,否則 CLI 會改走 API 計費、根本沒用到訂閱額度
    assert "ANTHROPIC_API_KEY" not in seen[0]["kwargs"]["env"]
    assert seen[0]["proc"].stdin_seen.decode("utf-8") == "職缺內容"


async def test_cli_returns_structured_output_and_records_usage(fake_cli):
    client = make_cli()
    result, cost = await call(client)

    assert result.results == []
    assert cost == pytest.approx(0.066)
    assert client.calls[0].output_tokens == 2500
    assert client.calls[0].cache_read_tokens == 2633
    assert last_model_used.get() == "claude-cli:sonnet"


async def test_cli_falls_back_to_parsing_result_text(fake_cli):
    _, replies = fake_cli
    payload = {**SUCCESS, "structured_output": None, "result": '```json\n{"results": []}\n```'}
    replies.append((json.dumps(payload), 0))
    result, _ = await call(make_cli())
    assert result.results == []


async def test_usage_limit_raises_quota_exhausted(fake_cli):
    """額度用完不能重試 —— 要讓呼叫端知道是「牆」而不是偶發錯誤。"""
    _, replies = fake_cli
    payload = {**SUCCESS, "is_error": True, "subtype": "error", "result": "Usage limit reached"}
    replies.append((json.dumps(payload), 1))
    with pytest.raises(ClaudeQuotaExhausted):
        await call(make_cli())


async def test_other_cli_error_is_not_mistaken_for_quota(fake_cli):
    _, replies = fake_cli
    payload = {**SUCCESS, "is_error": True, "subtype": "error_max_structured_output_retries"}
    replies.append((json.dumps(payload), 1))
    with pytest.raises(ClaudeCliError) as exc:
        await call(make_cli())
    assert not isinstance(exc.value, ClaudeQuotaExhausted)


async def test_non_json_stdout_is_a_parse_error(fake_cli):
    _, replies = fake_cli
    replies.append(("not json at all", 0))
    with pytest.raises(LLMParseError):
        await call(make_cli())


# ── ClaudeWithFallback ───────────────────────────────────────────────


class FakeClaude:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.models: list[str] = []

    async def structured(self, *, model, **_):
        self.models.append(model)
        if self.fail_with is not None:
            raise self.fail_with
        last_model_used.set(f"claude-cli:{model}")
        return ScreenResult(results=[]), 0.07

    async def aclose(self):
        pass


def make_openrouter(seen: list[str]) -> OpenRouterClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"results": []}'}}],
                "usage": {"cost": 0.001},
            },
        )

    cfg = load_config("config/config.yaml").llm
    return OpenRouterClient(
        "key", cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_fallback_maps_model_and_reports_zero_real_cost():
    """Claude 路徑回 0:daily_cost_cap_usd 只該攔真的會花錢的呼叫。"""
    claude = FakeClaude()
    seen: list[str] = []
    client = ClaudeWithFallback(claude, make_openrouter(seen), {"gemini-flash": "sonnet"})

    _, cost = await call(client, model="gemini-flash")

    assert claude.models == ["sonnet"]
    assert cost == 0.0
    assert client.tracker.total == 0.0
    assert seen == []
    assert client.fell_back is None


async def test_claude_failure_falls_back_and_sticks_for_the_rest_of_the_run():
    claude = FakeClaude(fail_with=ClaudeQuotaExhausted("usage limit"))
    seen: list[str] = []
    client = ClaudeWithFallback(claude, make_openrouter(seen), {"gemini-flash": "sonnet"})

    await call(client, model="gemini-flash")
    await call(client, model="gemini-flash")

    assert len(claude.models) == 1  # 第二次不再先撞 Claude
    assert seen == ["gemini-flash", "gemini-flash"]  # 退路用原本的 OpenRouter id
    assert last_model_used.get() == "gemini-flash"
    assert client.fell_back and "ClaudeQuotaExhausted" in client.fell_back
    assert client.tracker.total == pytest.approx(0.002)


async def test_missing_cli_is_a_call_time_error_so_fallback_can_catch_it(monkeypatch):
    """排程的 PATH 可能沒有 claude —— 建構時就丟的話,日報直接死在評分前。"""
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    cli = ClaudeCliClient()  # 不丟
    seen: list[str] = []
    client = ClaudeWithFallback(cli, make_openrouter(seen), {"gemini-flash": "sonnet"})
    await call(client, model="gemini-flash")
    assert seen == ["gemini-flash"]
    assert "找不到 claude CLI" in (client.fell_back or "")


async def test_without_openrouter_key_claude_errors_propagate():
    client = ClaudeWithFallback(FakeClaude(fail_with=ClaudeCliError("boom")), None, {})
    with pytest.raises(ClaudeCliError):
        await call(client)


def test_config_ships_with_claude_provider_and_sane_defaults():
    llm = load_config("config/config.yaml").llm
    assert llm.provider == "claude_cli"
    assert llm.claude.deep_model and llm.claude.screen_model
    assert 1 <= llm.claude.max_concurrent <= 4
