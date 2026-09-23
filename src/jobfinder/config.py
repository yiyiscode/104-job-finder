"""設定載入與驗證。

這是**第一道護欄**。SPEC.md §9 的硬性限制在這裡強制:設定只能往更保守的方向調,
超過硬上限的值直接拒絕啟動 —— 不要讓一個手滑的設定值把使用者的 IP 或帳號賠進去。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

# ─── 硬上限:程式碼裡的常數,不從設定讀 ────────────────────────────────
HARD_MAX_PAGES_PER_KEYWORD = 3
HARD_MAX_DETAILS_PER_RUN = 25
HARD_MAX_NAVIGATIONS_PER_RUN = 40
HARD_MIN_DELAY_SECONDS = 3.0
HARD_MAX_SCREEN_BATCH = 15
HARD_MIN_COOLDOWN_HOURS = 24
HARD_MAX_LIVE_LIMIT = 5  # `--headful --limit N` 的 N 上限

#: 設定檔中出現這些字樣代表有人想加 104 登入憑證 —— 直接拒絕(SPEC.md 規則 9)。
FORBIDDEN_CONFIG_KEYS = {
    "username",
    "password",
    "passwd",
    "account",
    "credential",
    "credentials",
    "login",
    "session_id",
    "cookie",
    "cookies",
    "auth",
    "token",
}

DelayRange = tuple[float, float]


def _validate_delay(v: Any, field_name: str) -> DelayRange:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{field_name} 必須是 [下限, 上限] 兩個數字")
    lo, hi = float(v[0]), float(v[1])
    if lo < HARD_MIN_DELAY_SECONDS:
        raise ValueError(
            f"{field_name} 下限 {lo}s 低於硬性下限 {HARD_MIN_DELAY_SECONDS}s。"
            " 見 SPEC.md 規則 4 — 節流不可繞過。"
        )
    if hi < lo:
        raise ValueError(f"{field_name} 上限 {hi} 不可小於下限 {lo}")
    return (lo, hi)


class SearchCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(min_length=1)
    areas: list[str] = Field(min_length=1)
    jobexp: list[str] = Field(default_factory=lambda: ["1", "3"])
    isnew: int | None = 3
    mode: str = "s"

    @field_validator("jobexp")
    @classmethod
    def _warn_on_exp(cls, v: list[str]) -> list[str]:
        # jobexp 是級距值不是上限值。只送 ["3"] 是原始需求的錯誤,會漏掉新鮮人職缺。
        if v == ["3"]:
            raise ValueError(
                "jobexp=['3'] 只會拿到「1~3年」的職缺,漏掉「1年以下/經歷不拘」。"
                " 要涵蓋 3 年以下請用 ['1', '3']。見 SPEC.md §4.2。"
            )
        return v


class TargetingCfg(BaseModel):
    """產業與公司規模的規則層過濾(見 :mod:`jobfinder.targeting`)。

    這一層跑在粗篩之前,所以不符合的職缺連 LLM 都不會看到,
    也不會吃掉每次 18 個詳細頁名額。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    #: 104 的產業代碼**前綴**。階層式代碼,一個前綴涵蓋整群。
    #: 空清單 = 不過濾產業。
    industry_prefixes: list[str] = Field(default_factory=list)
    #: 公司員工數下限。0 = 不過濾規模。
    min_employee_count: int = Field(default=0, ge=0)

    @field_validator("industry_prefixes")
    @classmethod
    def _prefixes_must_be_numeric(cls, v: list[str]) -> list[str]:
        # 打錯成中文名稱是最容易犯的錯,而且不會報錯只會默默全部濾掉
        for p in v:
            if not p.isdigit():
                raise ValueError(
                    f"industry_prefixes 要放 104 的產業**代碼**前綴(如 '1001006' = 半導體業),"
                    f" 不是中文名稱。收到:{p!r}"
                )
        return v


class ProxyCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str
    username: str | None = None
    password: str | None = None


class ScrapeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    #: ``http`` = 直接打 API(預設,快一個數量級);``browser`` = Patchright 備援。
    #: 只有在 104 把 Referer 檢查換成真正的 bot detection 時才需要切到 browser。
    mode: Literal["http", "browser"] = "http"
    #: 搜尋 API 的門檻就是這個 header。空的話一定 403。
    referer: str = "https://www.104.com.tw/jobs/search/"
    search_api_pattern: str = "/jobs/search/api/jobs"
    detail_api_pattern: str = "/job/ajax/content/"

    max_pages_per_keyword: int = Field(default=3, ge=1, le=HARD_MAX_PAGES_PER_KEYWORD)
    max_details_per_run: int = Field(default=25, ge=1, le=HARD_MAX_DETAILS_PER_RUN)
    max_navigations_per_run: int = Field(default=40, ge=1, le=HARD_MAX_NAVIGATIONS_PER_RUN)

    page_delay_range: DelayRange = (3.0, 5.0)
    keyword_delay_range: DelayRange = (5.0, 9.0)
    detail_delay_range: DelayRange = (4.0, 8.0)

    headless: bool = True
    user_agent: str | None = None
    locale: str = "zh-TW"
    timezone_id: str = "Asia/Taipei"
    viewport: tuple[int, int] = (1440, 900)
    page_timeout_ms: int = 45_000
    api_wait_seconds: float = 25.0
    warmup_max_seconds: float = 30.0
    proxy: ProxyCfg | None = None
    save_debug_screenshot: bool = True

    @field_validator("page_delay_range", "keyword_delay_range", "detail_delay_range", mode="before")
    @classmethod
    def _check_delays(cls, v: Any, info: Any) -> DelayRange:
        return _validate_delay(v, info.field_name)

    @model_validator(mode="after")
    def _user_agent_must_be_honest(self) -> ScrapeCfg:
        """UA 必須誠實標示自己是誰,不可偽裝成瀏覽器。

        104 的 API 根本不檢查 UA(實測 ``curl/8.x`` 也照樣 200),所以偽裝成瀏覽器
        沒有任何好處,只會在對方看 log 時顯得心虛。誠實標示反而降低合規風險,
        也讓 104 想聯絡時有管道。

        ⚠️ **browser 模式完全不讀這個欄位** —— :mod:`scrape.browser` 一律用 Chromium
        自己的真實 UA。那邊偽造 UA 會讓它與實際的瀏覽器指紋不一致,反而最像機器人。
        """
        if not self.user_agent:
            raise ValueError(
                "user_agent 必須設定,而且要誠實標示用途與聯絡方式,"
                ' 例如 "JobFinder/0.1 (personal job-seeking use; +you@example.com)"。'
            )
        if "Mozilla" in self.user_agent:
            raise ValueError(
                "user_agent 不要偽裝成瀏覽器。104 的 API 根本不檢查 UA,"
                " 偽裝沒有好處,誠實標示反而降低合規風險。"
            )
        return self

    @model_validator(mode="after")
    def _referer_required_for_http(self) -> ScrapeCfg:
        if self.mode == "http" and not self.referer.startswith("https://www.104.com.tw/"):
            raise ValueError(
                "http 模式的 referer 必須是 104 自己的網址 —— 搜尋 API 的門檻就是這個"
                " header,設錯一定回 403。"
            )
        return self


class CircuitCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cooldown_hours: int = Field(default=24, ge=HARD_MIN_COOLDOWN_HOURS)
    escalated_cooldown_hours: int = Field(default=72, ge=HARD_MIN_COOLDOWN_HOURS)

    @model_validator(mode="after")
    def _escalation_must_escalate(self) -> CircuitCfg:
        if self.escalated_cooldown_hours < self.cooldown_hours:
            raise ValueError("escalated_cooldown_hours 不可小於 cooldown_hours")
        return self


class DedupeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repost_cooldown_days: int = Field(default=30, ge=1)
    retain_days: int = Field(default=180, ge=7)


class ScreenCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    batch_size: int = Field(default=15, ge=1, le=HARD_MAX_SCREEN_BATCH)
    max_concurrent: int = Field(default=3, ge=1, le=8)
    temperature: float = 0.1
    rough_threshold: int = Field(default=45, ge=0, le=100)


class DeepCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    max_concurrent: int = Field(default=4, ge=1, le=8)
    temperature: float = 0.2


class LLMCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://openrouter.ai/api/v1"
    referer: str = ""
    title: str = "job-finder"
    screen: ScreenCfg
    deep: DeepCfg
    fallback_model: str | None = None
    max_retries: int = Field(default=3, ge=1, le=5)
    timeout_seconds: float = 120.0
    daily_cost_cap_usd: float = Field(default=0.5, gt=0)


class WeightsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tech_fit: int = 35
    exp_fit: int = 25
    domain_fit: int = 15
    growth_fit: int = 15
    practical_fit: int = 10


class ScoringCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: int = Field(default=70, ge=0, le=100)
    max_per_day: int = Field(default=10, ge=1, le=50)
    mode: Literal["threshold", "top_n"] = "threshold"
    top_n: int = Field(default=5, ge=1, le=20)
    #: ``top_n`` 模式的品質下限。純粹「推前 N 名」在職缺荒的日子會把 30 分的
    #: 垃圾也推出來 —— 前 N 名不代表值得看。0 = 不設下限(舊行為)。
    top_n_floor: int = Field(default=0, ge=0, le=100)
    list_rejected_in_summary: bool = True
    max_rejected_listed: int = Field(default=10, ge=0, le=50)
    weights: WeightsCfg = Field(default_factory=WeightsCfg)
    senior_years_cutoff: int = Field(default=5, ge=1)
    senior_exp_fit_cap: int = Field(default=8, ge=0, le=25)

    @model_validator(mode="after")
    def _weights_sum_to_100(self) -> ScoringCfg:
        total = sum(self.weights.model_dump().values())
        if total != 100:
            raise ValueError(f"scoring.weights 加總必須是 100,目前是 {total}")
        return self


class NotifyCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["telegram"] = "telegram"
    parse_mode: Literal["HTML"] = "HTML"  # 不開放 MarkdownV2,見 SPEC.md §7
    min_interval_seconds: float = Field(default=1.2, ge=1.0)
    disable_link_preview: bool = True
    send_summary_first: bool = True
    send_when_zero_matches: bool = True
    alert_on_failure: bool = True


class PathsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: str = "profile.md"
    db_filename: str = "jobs.db"
    profile_tarball: str = "chrome-profile.tar.gz"
    storage_state: str = "storage_state.json"


class RuntimeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Taipei"
    log_level: str = "INFO"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    search: SearchCfg
    targeting: TargetingCfg = Field(default_factory=TargetingCfg)
    scrape: ScrapeCfg
    circuit: CircuitCfg = Field(default_factory=CircuitCfg)
    dedupe: DedupeCfg = Field(default_factory=DedupeCfg)
    llm: LLMCfg
    scoring: ScoringCfg = Field(default_factory=ScoringCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    runtime: RuntimeCfg = Field(default_factory=RuntimeCfg)

    def scraping_allowed(self) -> bool:
        """緊急剎車:設定與環境變數任一停用就不抓(SPEC.md 規則 10)。"""
        env = os.environ.get("JOBFINDER_SCRAPE_DISABLED", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return False
        return self.scrape.enabled


class Secrets(BaseSettings):
    """憑證。本機從 .env 讀,Modal 從 Secret 注入 —— 變數名相同,程式碼零分支。

    ⚠️ 這裡沒有、也絕不會有 104 的帳號密碼欄位。見 SPEC.md 規則 9。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def require_all(self) -> None:
        missing = [
            name
            for name, value in (
                ("OPENROUTER_API_KEY", self.openrouter_api_key),
                ("TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
                ("TELEGRAM_CHAT_ID", self.telegram_chat_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"缺少環境變數:{', '.join(missing)}。"
                " 本機請複製 .env.example 成 .env;Modal 請用 modal secret create。"
            )


def _assert_no_credential_keys(raw: Any, path: str = "") -> None:
    """掃描整份設定,任何疑似登入憑證的鍵一律拒絕。

    SPEC.md 規則 9 的第一道防線:沒有欄位可填,就不可能不小心登入。
    """
    if isinstance(raw, dict):
        for key, value in raw.items():
            here = f"{path}.{key}" if path else str(key)
            # proxy 的帳密是連代理伺服器用的,與 104 帳號無關,放行
            if str(key).lower() in FORBIDDEN_CONFIG_KEYS and not here.startswith("scrape.proxy"):
                raise ConfigError(
                    f"設定檔中出現疑似登入憑證的欄位 `{here}`。"
                    " 本專案全程以匿名訪客身分抓取,絕不登入 104 帳號 —— 見 SPEC.md 規則 9。"
                )
            _assert_no_credential_keys(value, here)
    elif isinstance(raw, list):
        for i, item in enumerate(raw):
            _assert_no_credential_keys(item, f"{path}[{i}]")


def load_config(path: str | Path) -> Config:
    """讀取並驗證設定。任何違反硬性限制的值都在這裡炸掉,不會跑到一半才發現。"""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"找不到設定檔:{p}")

    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError(f"設定檔 {p} 不是一個 YAML 物件")

    _assert_no_credential_keys(raw)

    try:
        return Config.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"設定檔 {p} 驗證失敗:\n{exc}") from exc
