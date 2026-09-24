"""本機進入點。

與 Modal 共用同一個 ``pipeline.run_daily`` —— 差別只在注入哪些實作,
程式碼裡沒有 ``if IS_MODAL`` 分支。

離線旗標(``--from-fixtures`` / ``--replay`` / ``--dry-run`` / ``--fake-llm``)不是
便利功能,是 SPEC.md 規則 6 的落實:開發期絕不反覆打 104。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import HARD_MAX_LIVE_LIMIT, Config, Secrets, load_config
from .errors import JobFinderError
from .logging_conf import setup_logging
from .pipeline import Deps, run_daily
from .storage import Database, JobRepo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobfinder", description="104 每日職缺日報")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--data-dir", default="local_data")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="執行一次完整流程")
    run.add_argument(
        "--from-fixtures",
        action="store_true",
        help="從 tests/fixtures 讀職缺,完全不連 104(開發預設走這條)",
    )
    run.add_argument(
        "--replay",
        type=int,
        metavar="RUN_ID",
        help="拿某次 run 存下的原始 JSON 重跑評分,不重爬。調 prompt 用",
    )
    run.add_argument("--dry-run", action="store_true", help="不送 Telegram,印到終端並存 HTML 預覽")
    run.add_argument("--fake-llm", action="store_true", help="不打 OpenRouter,用啟發式假評分")
    run.add_argument("--headful", action="store_true", help="開有頭瀏覽器(需搭配 --limit)")
    run.add_argument(
        "--limit",
        type=int,
        help=f"只處理前 N 筆;連線模式硬上限 {HARD_MAX_LIVE_LIMIT}",
    )
    run.add_argument("--force", action="store_true", help="忽略「今天已執行過」的限制")
    run.add_argument("--fixtures-dir", default="tests/fixtures")

    sub.add_parser("reset-circuit", help="手動解除熔斷器")
    sub.add_parser("status", help="顯示最近幾次執行與熔斷器狀態")
    hr = sub.add_parser("hitrate", help="計算第 3 道閘門「必備命中率」(只打 OpenRouter,不連 104)")
    hr.add_argument("--limit", type=int, help="本次最多算幾筆(不超過 config 的 max_jobs_per_run)")
    hr.add_argument("--dry-run", action="store_true", help="只列出會算哪些職缺,不呼叫 LLM")
    hr.add_argument("--fake-llm", action="store_true", help="用技能詞典粗估,不花錢(數字不可信)")

    ui = sub.add_parser("ui", help="開啟本機 Web UI(候選清單 / 技能趨勢),只讀 jobs.db、不連 104")
    ui.add_argument("--port", type=int, default=8501)
    return parser


def _ui(args: argparse.Namespace) -> int:
    """啟動 Streamlit。**永遠只綁 127.0.0.1** —— jobs.db 含實際求職資料,
    Streamlit 預設綁 0.0.0.0 會對整個區網公開。"""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        print('需要先安裝 UI 依賴:uv pip install -e ".[ui]"', file=sys.stderr)
        return 2
    app = Path(__file__).with_name("webui") / "app.py"
    # 命中率綁定履歷版本,UI 要知道是哪一份。設定讀不到也不該讓 UI 開不起來 → 退回預設
    try:
        resume = load_config(args.config).paths.resume
    except JobFinderError:
        resume = "profile-de.md"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address", "127.0.0.1",
        "--server.port", str(args.port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--", "--data-dir", str(Path(args.data_dir).resolve()),
        "--resume", str(Path(resume).resolve()),
    ]  # fmt: skip
    print(f"Web UI:http://127.0.0.1:{args.port}(Ctrl+C 結束)")
    return subprocess.call(cmd)


async def _hitrate(cfg: Config, args: argparse.Namespace) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .hitrate.compute import resume_hash
    from .hitrate.runner import load_job_rows, run_hitrate
    from .hitrate.store import HitRateStore
    from .scoring import load_resume

    hcfg = cfg.hitrate
    if not hcfg.enabled:
        print("hitrate.enabled = false,跳過")
        return 0
    jobs_db = Path(args.data_dir) / cfg.paths.db_filename
    if not jobs_db.exists():
        print(f"找不到 {jobs_db},pipeline 還沒跑過", file=sys.stderr)
        return 1

    limit = hcfg.max_jobs_per_run if args.limit is None else min(args.limit, hcfg.max_jobs_per_run)
    model = hcfg.model or cfg.llm.deep.model
    resume = load_resume(cfg.paths.resume)

    if args.fake_llm or args.dry_run:
        from .hitrate.fake import FakeHitRateClient

        client, closer = FakeHitRateClient(), None
    else:
        from .scoring.llm_client import CostTracker, OpenRouterClient

        secrets = Secrets()
        if not secrets.openrouter_api_key:
            raise SystemExit("缺少 OPENROUTER_API_KEY。離線請加 --fake-llm 或 --dry-run。")
        client = OpenRouterClient(
            secrets.openrouter_api_key, cfg.llm, tracker=CostTracker(hcfg.cost_cap_usd)
        )
        closer = client.aclose

    try:
        report = await run_hitrate(
            rows=load_job_rows(jobs_db),
            store=HitRateStore(Path(args.data_dir) / hcfg.db_filename),
            client=client,
            model="fake" if args.fake_llm else model,
            resume_text=resume.full,
            resume_hash=resume_hash(resume.full),
            limit=limit,
            cost_cap_usd=hcfg.cost_cap_usd,
            now=datetime.now(ZoneInfo(cfg.runtime.timezone)),
            dry_run=args.dry_run,
        )
    finally:
        if closer is not None:
            await closer()

    print(report.summary_line())
    if args.dry_run:
        for job_no, _ in report.rates:
            print(f"  會算:{job_no}")
    return 1 if report.stopped_reason.startswith("連續失敗") else 0


def _validate_live_flags(cfg: Config, args: argparse.Namespace) -> None:
    """連線模式的護欄。SPEC.md 規則 6。"""
    offline = args.from_fixtures or args.replay
    if offline:
        return
    if args.headful and cfg.scrape.mode != "browser":
        raise SystemExit(
            "--headful 只有在 scrape.mode: browser 時有意義。 目前是 http 模式,根本不會開瀏覽器。"
        )
    if args.headful and (args.limit is None or args.limit > HARD_MAX_LIVE_LIMIT):
        raise SystemExit(
            f"--headful 是手動連線 debug 用,必須加 --limit 且不得超過"
            f" {HARD_MAX_LIVE_LIMIT}。開發請改用 --from-fixtures。"
        )
    if args.limit is not None and args.limit > HARD_MAX_LIVE_LIMIT:
        raise SystemExit(f"連線模式的 --limit 硬上限是 {HARD_MAX_LIVE_LIMIT}")


def _build_notifier(cfg: Config, args: argparse.Namespace):
    if args.dry_run:
        from .notify.telegram import ConsoleNotifier

        return ConsoleNotifier(cfg.scoring, out_path=str(Path(args.data_dir) / "preview.html"))

    from .notify.telegram import TelegramNotifier

    secrets = Secrets()
    if not secrets.telegram_bot_token or not secrets.telegram_chat_id:
        raise SystemExit(
            "缺少 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID。"
            " 開發請加 --dry-run,或複製 .env.example 成 .env 填入。"
        )
    return TelegramNotifier(
        secrets.telegram_bot_token,
        secrets.telegram_chat_id,
        notify=cfg.notify,
        scoring=cfg.scoring,
    )


def _build_scorer(cfg: Config, args: argparse.Namespace):
    if args.fake_llm:
        from .scoring.fake import FakeScorer

        return FakeScorer(cfg.scoring)

    from .scoring import OpenRouterClient, TwoStageScorer, load_resume

    secrets = Secrets()
    if not secrets.openrouter_api_key:
        raise SystemExit("缺少 OPENROUTER_API_KEY。開發請加 --fake-llm。")
    return TwoStageScorer(
        OpenRouterClient(secrets.openrouter_api_key, cfg.llm),
        cfg.llm,
        cfg.scoring,
        load_resume(cfg.paths.resume),
    )


def _build_source_factory(cfg: Config, args: argparse.Namespace):
    if args.from_fixtures:
        from .fixture_source import fixture_source

        return lambda: fixture_source(args.fixtures_dir, cfg.search.keywords[0])

    if args.replay:
        from .fixture_source import replay_source

        with Database(args.data_dir, cfg.paths.db_filename) as db:
            pairs = JobRepo(db.conn, cfg.dedupe).load_replay(args.replay)
        if not pairs:
            raise SystemExit(f"run #{args.replay} 沒有可重播的資料")
        print(f"重播 run #{args.replay}:{len(pairs)} 筆職缺")
        return lambda: replay_source(pairs)

    if cfg.scrape.mode == "browser":
        from .scrape.playwright_source import playwright_source

        return lambda: playwright_source(cfg, data_dir=args.data_dir, headless=not args.headful)

    from .http_source import http_source

    return lambda: http_source(cfg)


async def _run(cfg: Config, args: argparse.Namespace) -> int:
    deps = Deps(
        source_factory=_build_source_factory(cfg, args),
        scorer=_build_scorer(cfg, args),
        notifier=_build_notifier(cfg, args),
    )
    # 重播與 fixture 模式本來就是拿來反覆跑的:既不受「每日一次」限制,
    # 也不該被去重擋掉(不然第二次跑就永遠是 0 筆,調 prompt 完全沒東西可看)。
    offline = bool(args.from_fixtures or args.replay)

    report = await run_daily(
        cfg,
        deps,
        data_dir=args.data_dir,
        force=args.force or offline,
        limit=args.limit,
        rescore_all=offline,
    )

    if args.dry_run and (path := deps.notifier.save_html()):
        print(f"\n排版預覽已存到 {path}")

    print(
        f"\n狀態={report.status} 抓取={report.jobs_fetched} 新={report.jobs_new}"
        f" 深評={report.jobs_deep_scored} 推播={len(report.notified)}"
        f" 請求={report.requests_used} 成本=${report.llm_cost_usd:.4f}"
    )
    for warning in report.warnings:
        print(f"  ⚠️  {warning}")
    return 0 if report.status in ("success", "skipped") else 1


def _reset_circuit(cfg: Config, args: argparse.Namespace) -> int:
    from .scrape.blocking import CircuitState

    with Database(args.data_dir, cfg.paths.db_filename) as db:
        repo = JobRepo(db.conn, cfg.dedupe)
        before = repo.get_circuit()
        repo.save_circuit(CircuitState.closed())
    print(f"熔斷器已解除(先前:{before.state},連續失敗 {before.consecutive_failures} 次)")
    return 0


def _status(cfg: Config, args: argparse.Namespace) -> int:
    with Database(args.data_dir, cfg.paths.db_filename) as db:
        repo = JobRepo(db.conn, cfg.dedupe)
        circuit = repo.get_circuit()
        rows = db.conn.execute(
            "SELECT started_at, status, jobs_new, jobs_notified, requests_used,"
            " llm_cost_usd, error_kind FROM runs ORDER BY id DESC LIMIT 14"
        ).fetchall()

    print(f"熔斷器:{circuit.state}(連續失敗 {circuit.consecutive_failures} 次)")
    if circuit.reason:
        print(f"  最後原因:{circuit.reason}")
    print(f"\n{'開始時間':<22}{'狀態':<10}{'新':>4}{'推播':>5}{'請求':>5}{'成本':>9}  錯誤")
    for r in rows:
        print(
            f"{r['started_at'][:19]:<22}{r['status']:<10}{r['jobs_new']:>4}"
            f"{r['jobs_notified']:>5}{r['requests_used']:>5}"
            f"{r['llm_cost_usd']:>9.4f}  {r['error_kind'] or ''}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ui":
        # 不載入設定:UI 只讀 db,不該因為 config 或秘密沒設好而開不起來
        return _ui(args)
    try:
        cfg = load_config(args.config)
    except JobFinderError as exc:
        print(f"設定錯誤:{exc}", file=sys.stderr)
        return 2

    setup_logging("DEBUG" if args.verbose else cfg.runtime.log_level)
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)

    try:
        if args.command == "reset-circuit":
            return _reset_circuit(cfg, args)
        if args.command == "status":
            return _status(cfg, args)
        if args.command == "hitrate":
            return asyncio.run(_hitrate(cfg, args))

        _validate_live_flags(cfg, args)
        return asyncio.run(_run(cfg, args))
    except JobFinderError as exc:
        print(f"錯誤:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
