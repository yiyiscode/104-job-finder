"""護欄測試。

SPEC.md §11 明列這些是驗收條件。護欄沒有測試等於沒有護欄 —— 這個檔案壞掉時,
壞的不是「一個功能」,而是「使用者的 104 帳號與家用 IP 的保險絲」。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jobfinder.config import CircuitCfg, ScrapeCfg, load_config
from jobfinder.errors import (
    BudgetExhausted,
    ChallengeBlocked,
    CircuitOpen,
    ConfigError,
    ForbiddenPath,
    LoggedInDetected,
)
from jobfinder.scrape import blocking
from jobfinder.scrape.budget import RequestBudget
from jobfinder.scrape.urls import (
    assert_path_allowed,
    build_detail_api_url,
    build_job_url,
    build_search_api_url,
    build_search_page_url,
    extract_detail_id,
    normalize_job_link,
)

NOW = datetime(2026, 8, 21, 8, 0, 0)


# ═══ 設定硬上限(SPEC.md 規則 3、4、7、9)════════════════════════════


def test_real_config_is_valid(raw_config):
    """專案實際使用的 config.yaml 必須通過所有硬性驗證。"""
    cfg = load_config("config/config.yaml")
    assert cfg.scraping_allowed()
    assert sum(cfg.scoring.weights.model_dump().values()) == 100


@pytest.mark.parametrize(
    ("path", "value", "expect_in_message"),
    [
        (("scrape", "max_pages_per_keyword"), 10, "less than or equal to 3"),
        (("scrape", "max_details_per_run"), 100, "less than or equal to 25"),
        (("scrape", "max_navigations_per_run"), 500, "less than or equal to 40"),
        (("llm", "screen", "batch_size"), 50, "less than or equal to 15"),
        (("circuit", "cooldown_hours"), 1, "greater than or equal to 24"),
    ],
)
def test_config_rejects_values_beyond_hard_limits(
    config_dict, write_config, path, value, expect_in_message
):
    """設定只能往更保守調。放寬到硬上限之外必須拒絕啟動,不是警告。"""
    node = config_dict
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert expect_in_message in str(exc.value)


@pytest.mark.parametrize("field", ["page_delay_range", "keyword_delay_range", "detail_delay_range"])
def test_config_rejects_delays_below_three_seconds(config_dict, write_config, field):
    """節流不可繞過:任何低於 3 秒的延遲都拒絕啟動。"""
    config_dict["scrape"][field] = [0.1, 0.2]
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "低於硬性下限" in str(exc.value)


def test_config_rejects_inverted_delay_range(config_dict, write_config):
    config_dict["scrape"]["page_delay_range"] = [9.0, 4.0]
    with pytest.raises(ConfigError):
        load_config(write_config(config_dict))


def test_config_rejects_forged_user_agent(config_dict, write_config):
    """偽造 UA 與實際瀏覽器指紋不一致,反而是最明顯的機器人訊號(規則 7)。"""
    config_dict["scrape"]["user_agent"] = "Mozilla/5.0 (definitely a real browser)"
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "user_agent" in str(exc.value)


def test_config_rejects_weights_not_summing_to_100(config_dict, write_config):
    config_dict["scoring"]["weights"]["tech_fit"] = 99
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "加總必須是 100" in str(exc.value)


def test_config_rejects_jobexp_upper_bound_mistake(config_dict, write_config):
    """jobexp 是級距值不是上限值。只送 ['3'] 會漏掉新鮮人職缺(SPEC.md §4.2)。"""
    config_dict["search"]["jobexp"] = ["3"]
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "1年以下" in str(exc.value)


@pytest.mark.parametrize("key", ["username", "password", "account", "login", "cookie"])
def test_config_rejects_any_104_credential_field(config_dict, write_config, key):
    """規則 9 的第一道防線:沒有欄位可填,就不可能不小心登入。"""
    config_dict["scrape"][key] = "whatever"
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "匿名訪客" in str(exc.value)


def test_config_still_allows_proxy_credentials(config_dict, write_config):
    """代理伺服器的帳密與 104 帳號無關,不該被憑證掃描誤殺。"""
    config_dict["scrape"]["proxy"] = {
        "server": "http://tw.proxy.example:8000",
        "username": "u",
        "password": "p",
    }
    cfg = load_config(write_config(config_dict))
    assert cfg.scrape.proxy is not None
    assert cfg.scrape.proxy.server.startswith("http://")


def test_emergency_kill_switch_via_env(monkeypatch):
    """使用者要能不改程式、不等 deploy 就立刻停掉抓取(規則 10)。"""
    cfg = load_config("config/config.yaml")
    assert cfg.scraping_allowed()
    monkeypatch.setenv("JOBFINDER_SCRAPE_DISABLED", "1")
    assert not cfg.scraping_allowed()


# ═══ URL 黑名單(SPEC.md 規則 10)══════════════════════════════════


@pytest.mark.parametrize(
    "url",
    [
        "https://www.104.com.tw/job/71gqf?apply=form",
        "https://www.104.com.tw/jobs/apply/analysis",
        "https://www.104.com.tw/my104/savejob",
        "https://www.104.com.tw/member/login",
        "https://www.104.com.tw/company/abc/follow",
        "https://www.104.com.tw/resume/edit",
    ],
)
def test_forbidden_paths_are_rejected(url):
    """應徵、收藏、追蹤、登入是「已登入使用者的動作」,觸發即異常訊號。"""
    with pytest.raises(ForbiddenPath):
        assert_path_allowed(url)


@pytest.mark.parametrize("builder", [build_search_api_url, build_search_page_url])
def test_search_url_omits_order_and_sends_both_jobexp_brackets(builder):
    cfg = load_config("config/config.yaml")
    url = builder(cfg.search, "AI工程師", page=1)
    assert "order=" not in url, "order=15 是符合度排序且改版會漂移,不該依賴它"
    assert "jobexp=1%2C3" in url
    assert "isnew=3" in url


def test_http_mode_uses_api_path_not_the_disallowed_page_path():
    """robots.txt 的 `Disallow: /jobs/search/?*page=*` 要求字面的 `/jobs/search/?`。

    API 路徑不含那個字串,落在 `Allow: /jobs/` 底下 —— HTTP 模式在合規上反而更乾淨。
    """
    cfg = load_config("config/config.yaml")
    api = build_search_api_url(cfg.search, "AI工程師", page=2)
    page = build_search_page_url(cfg.search, "AI工程師", page=2)

    assert "/jobs/search/api/jobs?" in api
    assert "/jobs/search/?" not in api
    assert "/jobs/search/?" in page, "瀏覽器備援才會走命中 Disallow 的那條路"


@pytest.mark.parametrize(
    "param",
    ["kwop", "hotJob", "recommendJob", "irsTag", "expansionType", "excludeIndustryCat"],
)
def test_robots_disallowed_query_params_are_rejected(param):
    """robots.txt 明文 `Disallow: *<name>=*` 的參數,本專案一個都不需要。"""
    with pytest.raises(ForbiddenPath) as exc:
        assert_path_allowed(f"https://www.104.com.tw/jobs/search/api/jobs?keyword=x&{param}=7")
    assert "robots.txt" in str(exc.value)


def test_build_job_url_rejects_injection():
    with pytest.raises(ValueError):
        build_job_url("71gqf?apply=form")


def test_detail_api_url_uses_detail_id():
    """詳細頁 API 只吃連結尾段。拿 jobNo 去打會回 404「職務不存在」(已實測)。"""
    assert build_detail_api_url("94234") == "https://www.104.com.tw/job/ajax/content/94234"


def test_normalize_link_and_extract_detail_id():
    assert normalize_job_link("//www.104.com.tw/job/71gqf") == "https://www.104.com.tw/job/71gqf"
    # 104 現在給的是絕對網址
    assert (
        normalize_job_link("https://www.104.com.tw/job/94234") == "https://www.104.com.tw/job/94234"
    )
    assert extract_detail_id("https://www.104.com.tw/job/94234?jobsource=x") == "94234"


# ═══ 請求預算(SPEC.md 規則 3、4)═══════════════════════════════════


class FakePage:
    def __init__(self, *, fail: bool = False):
        self.visited: list[str] = []
        self.fail = fail

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        if self.fail:
            raise TimeoutError("boom")
        return None


class FakeSleeper:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_budget(**overrides):
    base = load_config("config/config.yaml").scrape.model_dump()
    base.update(overrides)
    sleeper = FakeSleeper()
    return RequestBudget(ScrapeCfg(**base), sleeper=sleeper), sleeper


async def test_budget_raises_when_navigation_quota_exhausted():
    budget, _ = make_budget(max_navigations_per_run=2, max_pages_per_keyword=3)
    page = FakePage()
    budget.start_keyword("k")
    for _ in range(2):
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    with pytest.raises(BudgetExhausted):
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert len(page.visited) == 2


async def test_budget_enforces_per_keyword_page_cap():
    budget, _ = make_budget(max_pages_per_keyword=2)
    page = FakePage()
    budget.start_keyword("AI工程師")
    for _ in range(2):
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert not budget.has_page_quota()
    with pytest.raises(BudgetExhausted):
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=3")

    # 換關鍵字後額度重置,但總導覽預算仍持續累計
    budget.start_keyword("MLOps")
    assert budget.has_page_quota()
    await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert budget.navigations_used == 3


async def test_budget_enforces_detail_cap():
    budget, _ = make_budget(max_details_per_run=1)
    page = FakePage()
    await budget.navigate(page, build_job_url("aaa"), kind="detail", delay="none")
    assert not budget.has_detail_quota()
    with pytest.raises(BudgetExhausted):
        await budget.navigate(page, build_job_url("bbb"), kind="detail")


async def test_budget_throttles_between_requests_but_not_before_the_first():
    budget, sleeper = make_budget()
    page = FakePage()
    budget.start_keyword("k")
    await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert sleeper.calls == [], "第一個請求不該等待"

    await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=2")
    assert len(sleeper.calls) == 1
    lo, hi = budget.cfg.page_delay_range
    assert lo <= sleeper.calls[0] <= hi


async def test_budget_delays_are_jittered_not_fixed():
    """固定間隔本身就是機器人指紋。"""
    budget, sleeper = make_budget(max_navigations_per_run=40, max_pages_per_keyword=3)
    page = FakePage()
    for kw in ("a", "b", "c", "d", "e", "f"):
        budget.start_keyword(kw)
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert len(set(sleeper.calls)) > 1


async def test_budget_counts_failed_navigation():
    """goto 拋例外時封包也已經送出去了,必須算進預算 —— 少算會讓實際請求量超上限。"""
    budget, _ = make_budget()
    page = FakePage(fail=True)
    budget.start_keyword("k")
    with pytest.raises(TimeoutError):
        await budget.navigate(page, "https://www.104.com.tw/jobs/search/?page=1")
    assert budget.navigations_used == 1


async def test_budget_refuses_forbidden_url_before_any_request():
    budget, _ = make_budget()
    page = FakePage()
    with pytest.raises(ForbiddenPath):
        await budget.navigate(page, "https://www.104.com.tw/job/71gqf?apply=form")
    assert page.visited == []
    assert budget.navigations_used == 0


# ═══ 封鎖偵測(SPEC.md 規則 1)═════════════════════════════════════


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 403},
        {"status": 429},
        {"status": 503},
        {"title": "Just a moment..."},
        {"body": "Enable JavaScript and cookies to continue"},
        {"body": "Error code: 1020"},
        {"turnstile_present": True},
    ],
)
def test_block_signals_are_detected(kwargs):
    assert blocking.detect_block(**kwargs) is not None


def test_normal_page_is_not_flagged_as_blocked():
    assert (
        blocking.detect_block(status=200, title="找工作 - 104人力銀行", body="AI工程師 職缺列表")
        is None
    )


def test_raise_if_blocked_raises_challenge_blocked():
    with pytest.raises(ChallengeBlocked) as exc:
        blocking.raise_if_blocked(status=403, url="https://www.104.com.tw/jobs/search/")
    assert exc.value.url.endswith("/jobs/search/")


def test_challenge_blocked_is_fatal_not_transient():
    """型別上就必須是「不可重試」那一族,避免呼叫端誤接成可重試錯誤。"""
    from jobfinder.errors import FatalScrapeError, TransientScrapeError

    assert issubclass(ChallengeBlocked, FatalScrapeError)
    assert not issubclass(ChallengeBlocked, TransientScrapeError)


# ═══ 匿名守門(SPEC.md 規則 9)═════════════════════════════════════


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self):
        return self._cookies


ANON_COOKIES = [
    {"name": "cf_clearance", "domain": ".104.com.tw", "value": "x"},
    {"name": "_ga", "domain": ".104.com.tw", "value": "y"},
]


async def test_assert_anonymous_passes_for_logged_out_visitor():
    await blocking.assert_anonymous(FakeContext(ANON_COOKIES))


@pytest.mark.parametrize("name", ["104_session", "ARJ", "access_token", "member_id", "uid", "jwt"])
async def test_assert_anonymous_blocks_logged_in_session(name):
    """帳號被停權比 IP 被鎖嚴重得多,所以這是硬性中止而不是警告。"""
    cookies = [*ANON_COOKIES, {"name": name, "domain": ".104.com.tw", "value": "z"}]
    with pytest.raises(LoggedInDetected):
        await blocking.assert_anonymous(FakeContext(cookies))


def test_identity_cookies_are_never_persisted():
    """連存都不存,就不會在下次執行被帶回來。"""
    cookies = [
        {"name": "cf_clearance", "domain": ".104.com.tw"},
        {"name": "__cf_bm", "domain": ".104.com.tw"},
        {"name": "104_session", "domain": ".104.com.tw"},
        {"name": "access_token", "domain": ".104.com.tw"},
        {"name": "_ga", "domain": ".104.com.tw"},
    ]
    kept = {c["name"] for c in blocking.filter_persistable_cookies(cookies)}
    assert kept == {"cf_clearance", "__cf_bm"}


def test_persist_whitelist_drops_unknown_cookies():
    """白名單而非黑名單:104 換個 cookie 名字也不會漏出去。"""
    kept = blocking.filter_persistable_cookies(
        [{"name": "brand_new_identity_thing", "domain": ".104.com.tw"}]
    )
    assert kept == []


# ═══ 熔斷器(SPEC.md 規則 2)═══════════════════════════════════════

CIRCUIT = CircuitCfg(cooldown_hours=24, escalated_cooldown_hours=72)


def test_circuit_starts_closed():
    blocking.assert_closed(blocking.CircuitState.closed(), CIRCUIT, NOW)


def test_circuit_blocks_during_cooldown():
    state = blocking.trip(blocking.CircuitState.closed(), "HTTP 403", NOW)
    with pytest.raises(CircuitOpen) as exc:
        blocking.assert_closed(state, CIRCUIT, NOW + timedelta(hours=23))
    assert exc.value.failures == 1


def test_circuit_reopens_after_cooldown():
    state = blocking.trip(blocking.CircuitState.closed(), "HTTP 403", NOW)
    recovered = blocking.assert_closed(state, CIRCUIT, NOW + timedelta(hours=25))
    assert recovered.state == "closed"
    # 失敗次數保留,下次再被擋要升級冷卻期
    assert recovered.consecutive_failures == 1


def test_second_block_escalates_to_72_hours():
    state = blocking.trip(blocking.CircuitState.closed(), "第一次", NOW)
    state = blocking.trip(state, "第二次", NOW)
    with pytest.raises(CircuitOpen):
        blocking.assert_closed(state, CIRCUIT, NOW + timedelta(hours=48))
    blocking.assert_closed(state, CIRCUIT, NOW + timedelta(hours=73))


def test_third_block_requires_manual_reset():
    state = blocking.CircuitState.closed()
    for i in range(3):
        state = blocking.trip(state, f"第 {i + 1} 次", NOW)
    with pytest.raises(CircuitOpen) as exc:
        blocking.assert_closed(state, CIRCUIT, NOW + timedelta(days=365))
    assert "reset-circuit" in str(exc.value)


def test_manual_reset_clears_failure_count():
    state = blocking.trip(blocking.CircuitState.closed(), "x", NOW)
    cleared = blocking.reset(state)
    assert cleared.consecutive_failures == 0
    blocking.assert_closed(cleared, CIRCUIT, NOW)


# ═══ 兩種 403 的分辨(架構改成純 HTTP 後新增)═══════════════════════


def test_empty_403_body_means_our_request_was_wrong():
    """實測:不帶 Referer 打搜尋 API 回 403 且 body 是 0 bytes。

    那是 origin 的白名單規則,不是被封鎖 —— 熔斷 24 小時對設定錯誤毫無幫助。
    """
    assert blocking.classify_403("") == "misconfigured"
    assert blocking.classify_403("   \n ") == "misconfigured"


def test_403_with_a_challenge_page_means_we_really_got_blocked():
    """實測:/sitemap.xml 的 403 body 有 5KB 的 Turnstile 挑戰頁。"""
    assert blocking.classify_403("<title>Just a moment...</title>") == "challenge"
    assert blocking.classify_403("Enable JavaScript and cookies to continue") == "challenge"


def test_detect_block_stays_fail_closed_on_403():
    """瀏覽器模式讀 body 可能失敗而得到空字串。

    那時必須 fail-closed —— 把真正的封鎖誤判成設定問題會讓我們繼續打下去。
    分流交給 HTTP source 明確呼叫 classify_403,不靠 detect_block 猜。
    """
    assert blocking.detect_block(status=403, body="") is not None


# ═══ user_agent 的正確做法在兩種模式下剛好相反 ═══════════════════════


def test_browser_mode_ignores_the_user_agent_field(config_dict, write_config):
    """browser 模式不讀這個欄位(Chromium 用自己的真實 UA),所以設著也不該擋。

    反過來說:在 browser 模式偽造 UA 之所以危險,是因為它與 Chromium 的實際指紋
    不一致 —— 而我們的做法是**根本不傳**,所以那個風險不存在。
    """
    config_dict["scrape"]["mode"] = "browser"
    cfg = load_config(write_config(config_dict))
    assert cfg.scrape.mode == "browser"


def test_user_agent_is_required(config_dict, write_config):
    config_dict["scrape"]["user_agent"] = None
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "誠實" in str(exc.value)


def test_http_mode_rejects_pretending_to_be_a_browser(config_dict, write_config):
    """104 的 API 根本不檢查 UA(實測 curl/8.x 也照樣 200),偽裝沒有好處。"""
    config_dict["scrape"]["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "偽裝" in str(exc.value)


def test_http_mode_requires_a_104_referer(config_dict, write_config):
    """搜尋 API 的門檻就是這個 header,設錯一定 403。"""
    # 明確指定 mode,不依賴 config.yaml 當下的值 —— 切換抓取模式不該弄壞這個測試
    config_dict["scrape"]["mode"] = "http"
    config_dict["scrape"]["referer"] = "https://www.google.com/"
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(config_dict))
    assert "referer" in str(exc.value)
