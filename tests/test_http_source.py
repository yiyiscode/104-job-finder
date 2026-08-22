"""純 HTTP 來源的測試。全部用 MockTransport,一個真實請求都不發。

這個檔案要證明兩件事:

1. **換掉抓取方式沒有把護欄一起換掉。** HTTP 路徑一樣受預算、節流、路徑黑名單約束。
2. **兩種 403 被正確分流。** 「Referer 掉了」是我方設定問題,不該熔斷;
   「真的被上防護」才該熔斷。搞混的話,一個設定錯誤會讓系統白白停機 24 小時。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobfinder.config import load_config
from jobfinder.errors import (
    BudgetExhausted,
    ChallengeBlocked,
    ForbiddenPath,
    SourceMisconfigured,
)
from jobfinder.http_source import HttpJobSource
from jobfinder.scrape.budget import RequestBudget

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SEARCH = json.loads((FIXTURE_DIR / "search_page1.json").read_text(encoding="utf-8"))
DETAIL = json.loads((FIXTURE_DIR / "detail_94234.json").read_text(encoding="utf-8"))

#: 實測:不帶 Referer 打搜尋 API 回 403 且 body 是 0 bytes
EMPTY_403 = httpx.Response(403, content=b"")
#: 實測:/sitemap.xml 回的 403 body 有 5KB 的 Turnstile 挑戰頁
CHALLENGE_403 = httpx.Response(
    403,
    text="<html><head><title>Just a moment...</title></head>"
    "<body>Enable JavaScript and cookies to continue"
    "<script src='https://challenges.cloudflare.com/turnstile'></script></body></html>",
)


class NoSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_source(handler, *, sleeper=None, **scrape_overrides):
    cfg = load_config("config/config.yaml")
    if scrape_overrides:
        cfg = cfg.model_copy(update={"scrape": cfg.scrape.model_copy(update=scrape_overrides)})
    budget = RequestBudget(cfg.scrape, sleeper=sleeper or NoSleep())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpJobSource(cfg, budget, client=client), budget, cfg


def ok_search(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=SEARCH)


# ═══ 基本行為 ═══════════════════════════════════════════════════════


async def test_referer_is_always_sent():
    """搜尋 API 的門檻就是這個 header。少了它一定 403。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Referer", ""))
        return httpx.Response(200, json=SEARCH)

    source, _, cfg = make_source(handler, max_pages_per_keyword=1)
    await source.fetch_summaries()
    assert seen and all(r == cfg.scrape.referer for r in seen)


async def test_user_agent_is_honest_not_a_fake_browser():
    """104 的 API 不檢查 UA,偽裝沒有好處,誠實標示反而降低合規風險。"""
    cfg = load_config("config/config.yaml")
    assert "Mozilla" not in (cfg.scrape.user_agent or "")
    assert "JobFinder" in (cfg.scrape.user_agent or "")


async def test_detail_uses_detail_id_and_the_jobs_own_page_as_referer():
    """詳細頁 API 只吃 detail_id,而且 Referer 必須是該職缺自己的頁面。"""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("Referer", "")))
        return httpx.Response(200, json=DETAIL)

    source, _, _ = make_source(handler)
    page = await _first_page(ok_search)
    job = page[0]

    await source.fetch_details([job])
    url, referer = seen[0]
    assert url.endswith(f"/job/ajax/content/{job.detail_id}")
    assert referer.endswith(f"/job/{job.detail_id}")
    assert job.job_no not in url, "拿 job_no 打詳細頁會 404"


async def _first_page(handler):
    source, _, _ = make_source(handler, max_pages_per_keyword=1)
    batch = await source.fetch_summaries()
    return batch.jobs


async def test_summaries_are_normalized_from_the_real_payload():
    jobs = await _first_page(ok_search)
    assert len(jobs) >= 30
    assert jobs[0].job_no == "15305872"
    assert jobs[0].detail_id == "94234"


# ═══ 兩種 403 的分流(這次改動的核心)═══════════════════════════════


async def test_empty_403_is_a_config_problem_not_a_block():
    """body 空的 403 = Referer 掉了。熔斷 24 小時對設定錯誤毫無幫助。"""
    source, _, _ = make_source(lambda r: EMPTY_403)
    with pytest.raises(SourceMisconfigured) as exc:
        await source.fetch_summaries()
    assert "Referer" in str(exc.value)


async def test_challenge_403_is_a_real_block():
    """body 有挑戰頁特徵 = 104 真的上防護了。這才該熔斷。"""
    source, _, _ = make_source(lambda r: CHALLENGE_403)
    with pytest.raises(ChallengeBlocked):
        await source.fetch_summaries()


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limiting_is_treated_as_a_block(status):
    source, _, _ = make_source(lambda r: httpx.Response(status, text="slow down"))
    with pytest.raises(ChallengeBlocked):
        await source.fetch_summaries()


async def test_misconfigured_is_not_a_subclass_of_the_fatal_family():
    """型別上就要分得開,否則 pipeline 的 except 順序一改就會誤觸熔斷。"""
    from jobfinder.errors import FatalScrapeError

    assert not issubclass(SourceMisconfigured, FatalScrapeError)
    assert issubclass(ChallengeBlocked, FatalScrapeError)


# ═══ 護欄沒有因為換路而消失 ═══════════════════════════════════════


async def test_every_request_goes_through_the_budget():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEARCH)

    source, budget, _ = make_source(handler, max_pages_per_keyword=1)
    await source.fetch_summaries()
    assert budget.navigations_used == calls["n"] == source.requests_used


async def test_navigation_budget_stops_the_run():
    source, budget, _ = make_source(ok_search, max_navigations_per_run=2)
    batch = await source.fetch_summaries()
    assert budget.navigations_used == 2
    assert batch.truncated_by_budget


async def test_detail_budget_is_enforced():
    source, budget, _ = make_source(
        lambda r: httpx.Response(200, json=DETAIL), max_details_per_run=2
    )
    jobs = await _first_page(ok_search)

    batch = await source.fetch_details(jobs[:10])
    assert len(batch.details) == 2
    assert batch.truncated_by_budget
    assert budget.details_used == 2


async def test_requests_are_throttled_with_jitter():
    sleeper = NoSleep()
    source, _, _ = make_source(ok_search, sleeper=sleeper, max_pages_per_keyword=3)
    await source.fetch_summaries()

    assert sleeper.calls, "HTTP 模式一樣要節流"
    assert len(set(sleeper.calls)) > 1, "固定間隔本身就是機器人指紋"
    assert min(sleeper.calls) >= 3.0


async def test_forbidden_query_params_are_refused_before_any_request():
    """robots.txt 禁用的參數若被誰加進 config,連請求都不該送出去。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEARCH)

    source, budget, _ = make_source(handler)
    with pytest.raises(ForbiddenPath):
        await budget.acquire(
            "https://www.104.com.tw/jobs/search/api/jobs?keyword=x&kwop=7", delay="none"
        )
    assert calls["n"] == 0
    assert budget.navigations_used == 0


async def test_budget_exhausted_is_not_an_error():
    """預算用完是設計,不是失敗 —— 呼叫端要拿已抓到的正常收尾。"""
    source, budget, _ = make_source(ok_search, max_navigations_per_run=1)
    await source.fetch_summaries()
    with pytest.raises(BudgetExhausted):
        await budget.acquire("https://www.104.com.tw/jobs/search/api/jobs?keyword=x")


# ═══ 局部失敗的韌性 ═══════════════════════════════════════════════


async def test_one_bad_detail_does_not_abort_the_batch():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=DETAIL)

    source, _, _ = make_source(handler)
    jobs = await _first_page(ok_search)

    batch = await source.fetch_details(jobs[:3])
    assert len(batch.details) == 2
    assert any("詳細頁取得失敗" in d for d in batch.drift)


async def test_non_json_response_is_transient_not_fatal():
    from jobfinder.errors import FatalScrapeError, TransientScrapeError

    source, _, _ = make_source(lambda r: httpx.Response(200, text="<html>維護中</html>"))
    with pytest.raises(TransientScrapeError) as exc:
        await source.fetch_summaries()
    assert not isinstance(exc.value, FatalScrapeError)
