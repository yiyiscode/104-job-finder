"""Modal 部署層。

刻意做得很薄:這裡只負責 Image / Volume / Secret / Cron 的宣告,實際流程在
``jobfinder.pipeline.run_daily`` —— 與本機 CLI 走的是同一個函式,沒有 ``if IS_MODAL`` 分支。

部署:

    modal secret create job-finder-secrets \\
        OPENROUTER_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
    modal run modal_app.py::daily_run     # 先手動驗一次(決定成敗的一刻)
    modal deploy modal_app.py
"""

from __future__ import annotations

import modal

APP_NAME = "job-finder"
VOL_PATH = "/data"

CORE_DEPS = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "pyyaml>=6.0",
    "tenacity>=8.3",
    "python-dateutil>=2.9",
    "tzdata>=2024.1",
]


def _with_project(img: modal.Image) -> modal.Image:
    return (
        img.add_local_dir("src/jobfinder", remote_path="/root/jobfinder")
        .add_local_file("config/config.yaml", remote_path="/root/config.yaml")
        .add_local_file("profile.md", remote_path="/root/profile.md")
    )


#: 預設。104 的搜尋 API 沒有 Cloudflare,只要帶對 Referer 就行 ——
#: 不需要瀏覽器,image 從幾百 MB 降到幾 MB,冷啟動也快得多。
image = _with_project(modal.Image.debian_slim(python_version="3.12").pip_install(*CORE_DEPS))

#: 備援。只有在 104 把 Referer 檢查換成真正的 bot detection、需要把
#: `scrape.mode` 切成 `browser` 時才用得到。切換時把 daily_run 的 image 換成這個。
browser_image = _with_project(
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "tar",
        # CJK 字型。缺中文字型會讓 canvas 指紋異常,反而更容易被判定為機器人。
        "fonts-noto-cjk",
    )
    .pip_install(*CORE_DEPS, "patchright>=1.49")
    # 必須分兩行:`--with-deps` 在 Modal build 的 non-tty 環境偶爾會吞掉錯誤碼
    .run_commands(
        "patchright install-deps chromium",
        "patchright install chromium",
    )
)


#: 這個模組**在本機與容器裡都會被 import**:本機是為了定義 app,容器是為了跑函式。
#: 兩邊的設定檔位置不同,所以兩個都要試 —— 只寫相對路徑會讓容器 crash-loop。
_CONFIG_PATHS = ("config/config.yaml", "/root/config.yaml")


def _scrape_mode() -> str:
    """讀出抓取模式 —— image 與資源配置都取決於它。"""
    import yaml

    for path in _CONFIG_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                return (yaml.safe_load(f).get("scrape") or {}).get("mode", "http")
        except FileNotFoundError:
            continue
    return "http"


volume = modal.Volume.from_name("job-finder-data", create_if_missing=True)
secrets = [modal.Secret.from_name("job-finder-secrets")]

app = modal.App(APP_NAME)


# ⚠️ 這裡跟著 config 的 scrape.mode 走:
#    http    → image(輕量,無 Chromium)
#    browser → browser_image(含 Chromium 與 CJK 字型,記憶體要開大)
#
# 實測 2026-08-22:Modal 的 AWS 雪梨 IP(52.63.49.208)打 104 會拿到
# `cf-mitigated: challenge` 的 Cloudflare Managed Challenge —— 家用台灣住宅 IP 則不會。
# 也就是說,Cloudflare 的挑戰是**依 IP 信譽決定**的,不是這條路徑一律沒有。
_MODE = _scrape_mode()
_IS_BROWSER = _MODE == "browser"


@app.function(
    image=browser_image if _IS_BROWSER else image,
    volumes={VOL_PATH: volume},
    secrets=secrets,
    # 台北時間每天 08:00。已驗證 modal.Cron 支援 IANA 時區名,不需自己換算 UTC。
    schedule=modal.Cron("0 8 * * *", timezone="Asia/Taipei"),
    timeout=60 * 30,
    # Chromium 在 2GB 常 OOM;HTTP 模式則很輕
    memory=4096 if _IS_BROWSER else 512,
    cpu=2.0 if _IS_BROWSER else 0.5,
    # ⚠️ SPEC.md 規則 5:絕不自動重試。
    # Modal 的 function 級重試會在被擋後隔幾分鐘把整趟再跑一次,
    # 那正是把「暫時被挑戰」變成「永久封 IP」的行為。
    retries=0,
    # ⚠️ SPEC.md 規則 3:杜絕多個容器同時打 104。
    max_containers=1,
)
def daily_run(force: bool = False) -> dict:
    """每日排程的進入點。

    ``force=True`` 只給**手動驗證**用(``modal run modal_app.py::daily_run --force``)——
    它繞過「每日僅執行一次」那道護欄,因為架設期間你可能一天要試好幾次。
    cron 走預設值,該護欄照常生效。

    ⚠️ 它**不會**繞過熔斷器與緊急開關 —— 那兩道無論如何都要過。
    """
    import asyncio
    import sys

    sys.path.insert(0, "/root")

    from jobfinder.config import load_config
    from jobfinder.logging_conf import setup_logging
    from jobfinder.notify.telegram import TelegramNotifier
    from jobfinder.pipeline import Deps, run_daily
    from jobfinder.scoring import OpenRouterClient, TwoStageScorer, load_resume

    cfg = load_config("/root/config.yaml")
    setup_logging(cfg.runtime.log_level)

    if cfg.scrape.mode == "browser":
        from jobfinder.scrape.playwright_source import playwright_source

        def make_source():
            return playwright_source(cfg, data_dir=VOL_PATH, volume=volume)
    else:
        from jobfinder.http_source import http_source

        def make_source():
            return http_source(cfg)

    from jobfinder.config import Secrets

    creds = Secrets()
    creds.require_all()

    deps = Deps(
        source_factory=make_source,
        scorer=TwoStageScorer(
            OpenRouterClient(creds.openrouter_api_key, cfg.llm),
            cfg.llm,
            cfg.scoring,
            load_resume("/root/profile.md"),
        ),
        notifier=TelegramNotifier(
            creds.telegram_bot_token,
            creds.telegram_chat_id,
            notify=cfg.notify,
            scoring=cfg.scoring,
        ),
    )

    report = asyncio.run(run_daily(cfg, deps, data_dir=VOL_PATH, volume=volume, force=force))
    return {
        "status": report.status,
        "fetched": report.jobs_fetched,
        "new": report.jobs_new,
        "notified": len(report.notified),
        "requests_used": report.requests_used,
        "cost_usd": round(report.llm_cost_usd, 4),
        "warnings": report.warnings,
    }


@app.function(image=image, volumes={VOL_PATH: volume}, secrets=secrets, timeout=300)
def reset_circuit() -> str:
    """遠端解除熔斷器:``modal run modal_app.py::reset_circuit``"""
    import sys

    sys.path.insert(0, "/root")

    from jobfinder.config import load_config
    from jobfinder.scrape.blocking import CircuitState
    from jobfinder.storage import Database, JobRepo

    cfg = load_config("/root/config.yaml")
    with Database(VOL_PATH, cfg.paths.db_filename, volume=volume) as db:
        repo = JobRepo(db.conn, cfg.dedupe)
        before = repo.get_circuit()
        repo.save_circuit(CircuitState.closed())
    return f"熔斷器已解除(先前 {before.state},連續失敗 {before.consecutive_failures} 次)"


@app.function(image=image, secrets=secrets, timeout=180)
def diagnose() -> dict:
    """從 Modal 的 IP 發**一個**請求,把原始回應攤開來看。

    存在的理由:本機 200 而 Modal 403 時,唯一能分辨「Cloudflare 挑戰 / 地區封鎖 /
    速率限制 / Referer 規則」的方法就是看回應本身。這個函式刻意繞過 pipeline 的護欄,
    所以**只發一個請求**,不做任何重試。
    """
    import sys

    sys.path.insert(0, "/root")
    import httpx

    from jobfinder.config import load_config
    from jobfinder.scrape.urls import build_search_api_url

    cfg = load_config("/root/config.yaml")
    url = build_search_api_url(cfg.search, cfg.search.keywords[0], 1)

    out: dict = {"url": url}
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        # 順便看看出口 IP 是誰,好判斷是不是 ASN/地區問題
        try:
            out["egress_ip"] = c.get("https://api.ipify.org").text.strip()
        except Exception as exc:
            out["egress_ip"] = f"(取得失敗 {exc})"

        r = c.get(
            url,
            headers={
                "Referer": cfg.scrape.referer,
                "User-Agent": cfg.scrape.user_agent or "JobFinder/0.1",
            },
        )

    body = r.text
    print("=" * 60)
    print(f"egress IP : {out['egress_ip']}")
    print(f"status    : {r.status_code}")
    print(f"body 長度  : {len(body)}")
    print(f"server    : {r.headers.get('server')}")
    print(f"cf-ray    : {r.headers.get('cf-ray')}")
    print(f"cf-mitigated: {r.headers.get('cf-mitigated')}")
    print(f"content-type: {r.headers.get('content-type')}")
    print("-" * 60)
    print("完整 headers:")
    for k, v in r.headers.items():
        print(f"  {k}: {v}")
    print("-" * 60)
    print("body 前 800 字:")
    print(body[:800])
    print("=" * 60)

    out.update(
        status=r.status_code,
        body_len=len(body),
        body_head=body[:600],
        server=r.headers.get("server"),
        cf_ray=r.headers.get("cf-ray"),
        cf_mitigated=r.headers.get("cf-mitigated"),
        content_type=r.headers.get("content-type"),
        retry_after=r.headers.get("retry-after"),
        headers=dict(r.headers),
    )
    return out


@app.local_entrypoint()
def main() -> None:
    print(daily_run.remote())
