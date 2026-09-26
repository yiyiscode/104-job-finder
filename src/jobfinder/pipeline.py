"""流程編排。

這個檔案不做任何 IO —— 抓取、評分、通知全部從 :mod:`ports` 注入。所以:

* 測試可以四個假件全注入,離線跑完整條流程
* 若 Modal 的 IP 過不了 Cloudflare,換一個 ``JobSource`` 就能把抓取搬到別處,
  這裡一行都不用改(SPEC.md 風險 1 備案 ④)

**護欄的順序是刻意的**:緊急開關 → 熔斷器 → 每日一次,三道都在開瀏覽器之前。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .errors import (
    BudgetExhausted,
    ChallengeBlocked,
    FatalScrapeError,
    LoggedInDetected,
    SourceMisconfigured,
)
from .models import RejectedJob, RunReport, ScoredJob
from .normalize import merge_summaries
from .ports import JobSource, Notifier, Scorer
from .scrape import blocking
from .storage import Database, JobRepo
from .targeting import apply_targeting

log = logging.getLogger(__name__)

SourceFactory = Callable[[], AbstractAsyncContextManager[JobSource]]


@dataclass
class Deps:
    source_factory: SourceFactory
    scorer: Scorer
    notifier: Notifier


def _to_rejected(scored: ScoredJob) -> RejectedJob:
    reason = scored.one_liner or f"評分 {scored.total},判定 {scored.verdict}"
    return RejectedJob(
        job_no=scored.job_no,
        job_name=scored.summary.job_name,
        cust_name=scored.summary.cust_name,
        total=scored.total,
        reason=reason[:60],
    )


async def run_daily(
    cfg: Config,
    deps: Deps,
    *,
    data_dir: str,
    volume: Any = None,
    now: datetime | None = None,
    force: bool = False,
    limit: int | None = None,
    rescore_all: bool = False,
) -> RunReport:
    """跑完一次日報。

    ``rescore_all=True`` 時跳過「只留今天新出現的」這一步,改成把抓到的全部重評。
    離線模式(``--replay`` / ``--from-fixtures``)用它 —— 那兩個模式的用途就是
    拿同一批資料反覆重跑來調 prompt 與排版,被去重擋掉就完全失去意義。
    職缺仍然會照常寫進 DB,所以之後還是能 ``--replay``。
    """
    tz = ZoneInfo(cfg.runtime.timezone)
    now = now or datetime.now(tz)
    report = RunReport(started_at=now)

    with Database(data_dir, cfg.paths.db_filename, volume=volume) as db:
        repo = JobRepo(db.conn, cfg.dedupe)

        # ── 護欄:三道都在開瀏覽器之前 ────────────────────────────
        if not cfg.scraping_allowed():
            return await _skip(
                report,
                repo,
                deps,
                "抓取已停用",
                ["config `scrape.enabled` 或環境變數 JOBFINDER_SCRAPE_DISABLED 已關閉抓取。"],
            )

        circuit = repo.get_circuit()
        try:
            circuit = blocking.assert_closed(circuit, cfg.circuit, now)
            repo.save_circuit(circuit)
        except Exception as exc:  # CircuitOpen
            return await _skip(report, repo, deps, "熔斷器冷卻中,今天不抓取", [str(exc)])

        if not force and repo.has_run_today(now):
            return await _skip(
                report,
                repo,
                deps,
                "今天已經執行過",
                ["每日僅執行一次(SPEC.md 規則 3)。要重跑請加 --force。"],
            )

        report.run_id = repo.start_run(now)

        # ── 抓取 ──────────────────────────────────────────────────
        try:
            details, screen_outcome = await _fetch_and_screen(
                cfg, deps, repo, report, now, limit, rescore_all
            )
        except SourceMisconfigured as exc:
            # 我方請求少了東西(通常是 Referer),不是被 104 封鎖。
            # 熔斷對設定錯誤毫無幫助 —— 冷卻 24 小時之後設定還是錯的。
            await _handle_misconfigured(deps, repo, report, exc, now)
            db.checkpoint()
            return report
        except (ChallengeBlocked, LoggedInDetected, FatalScrapeError) as exc:
            await _handle_block(cfg, deps, repo, report, circuit, exc, now)
            db.checkpoint()
            return report

        # ── 深評 ──────────────────────────────────────────────────
        scored = await _deep_score_all(cfg, deps, repo, report, screen_outcome, details)

        from .scoring.rules import select_for_notification

        passing, rejected_scored = select_for_notification(scored, cfg.scoring)
        report.notified = passing
        report.rejected = screen_outcome.dropped + [_to_rejected(s) for s in rejected_scored]
        report.warnings.extend(screen_outcome.warnings)

        # 抓取與入庫都完成了,先存一次檔
        db.checkpoint()

        # ── 通知 ──────────────────────────────────────────────────
        await _notify(cfg, deps, report)

        repo.mark_notified([s.job_no for s in passing])
        # 深評過但沒推的要標 scored —— 停在 new 會跟「沒看過」混在一起
        repo.mark_scored([s.job_no for s in rejected_scored])
        repo.mark_status([r.job_no for r in screen_outcome.dropped], "screened_out")
        report.finished_at = datetime.now(tz)
        repo.save_circuit(blocking.reset(circuit))
        repo.finish_run(report)
        repo.prune(now)

        # 推播成功且已標記,再存一次 —— 這樣即使容器接著崩潰也不會重推
        db.checkpoint()

    return report


# ── 各階段 ──────────────────────────────────────────────────────────


async def _fetch_and_screen(cfg, deps, repo, report, now, limit, rescore_all=False):
    from .ports import DetailBatch

    details = DetailBatch()
    screen_outcome = None

    async with deps.source_factory() as source:
        try:
            batch = await source.fetch_summaries()
        except BudgetExhausted as exc:
            log.info("抓取預算用盡:%s", exc)
            batch = getattr(source, "partial_batch", None)
            if batch is None:
                raise

        report.jobs_fetched = len(batch.jobs)
        report.schema_drift_count = len(batch.drift)
        if batch.drift:
            report.warnings.append(f"schema drift {len(batch.drift)} 筆,104 可能已改版")
            log.warning("schema drift: %s", batch.drift[:5])
        if not batch.jobs and batch.observed_urls:
            # 頁面有發別的 XHR 卻攔不到目標 API = 路徑被改了(SPEC.md 風險 2)
            report.warnings.append(
                "攔截到 0 個目標 API 回應,但頁面有其他 XHR —— 104 可能已改版。"
                f" 實際觀察到:{', '.join(batch.observed_urls[:5])}"
            )

        merged = merge_summaries(batch.jobs)
        # 照常 register:jobs 表要有資料,scores 的外鍵才掛得上,之後也才能 --replay。
        # rescore_all 只是不套用「只留今天新出現的」這層過濾。
        new_jobs = repo.register(merged, now)
        if rescore_all:
            new_jobs = merged
        report.jobs_new = len(new_jobs)
        log.info("抓到 %d 筆,其中 %d 筆是新的", len(merged), len(new_jobs))

        # ── 產業／規模規則層 ───────────────────────────────────────
        # 刻意放在粗篩**之前**:不符合的職缺不必進 LLM,也不該吃掉詳細頁名額。
        # 放在 limit 之前,這樣 --limit N 的 N 是「要評的目標職缺」而不是「抓到的前 N 筆」。
        targeted = apply_targeting(new_jobs, cfg.targeting)
        new_jobs = targeted.keep
        report.jobs_filtered_out = targeted.dropped_count
        report.warnings.extend(targeted.warnings)
        if targeted.dropped:
            # 刻意不是 screened_out —— 這些連 LLM 都沒看過。兩種淘汰原因共用同一個
            # 狀態值的話,事後無法分辨「產業不對」與「模型覺得不適合」。
            repo.mark_status([j.job_no for j in targeted.dropped], "filtered_out")

        if limit:
            new_jobs = new_jobs[:limit]

        screen_outcome = await deps.scorer.screen(new_jobs)
        report.jobs_screened_in = len(screen_outcome.keep)
        report.llm_cost_usd += screen_outcome.cost_usd
        # 粗篩刷掉的留一筆紀錄。留下來的不用存 —— 它們馬上會有 deep 紀錄。
        repo.save_screen_drops(
            screen_outcome.dropped,
            run_id=report.run_id,
            model=cfg.llm.screen.model,
            now=now,
        )

        if screen_outcome.keep:
            try:
                details = await source.fetch_details(screen_outcome.keep)
            except BudgetExhausted as exc:
                log.info("詳細頁預算用盡:%s", exc)
                report.warnings.append("詳細頁預算用盡,部分職缺只用列表資訊評分")

        report.requests_used = source.requests_used

    for job_no, detail in details.details.items():
        repo.save_detail(job_no, detail.raw)
    report.schema_drift_count += len(details.drift)

    return details, screen_outcome


async def _deep_score_all(cfg, deps, repo, report, screen_outcome, details) -> list[ScoredJob]:
    scored: list[ScoredJob] = []
    for job in screen_outcome.keep:
        if getattr(deps.scorer, "budget_exceeded", False):
            report.warnings.append(f"LLM 成本已達上限 ${cfg.llm.daily_cost_cap_usd},剩餘職缺未深評")
            break
        try:
            result, cost = await deps.scorer.deep(job, details.details.get(job.job_no))
        except Exception as exc:
            log.warning("深評 %s 失敗:%s", job.job_no, exc)
            report.warnings.append(f"{job.job_no} 深評失敗:{exc}")
            continue

        report.llm_cost_usd += cost
        scored.append(result)
        repo.save_score(
            result,
            run_id=report.run_id,
            raw_response=getattr(deps.scorer, "raw_responses", {}).get(job.job_no, ""),
            now=report.started_at,
        )

    report.jobs_deep_scored = len(scored)
    return scored


async def _notify(cfg, deps, report) -> None:
    try:
        if cfg.notify.send_summary_first and (report.notified or cfg.notify.send_when_zero_matches):
            await deps.notifier.send_summary(report)
        for job in report.notified:
            await deps.notifier.send_job(job)
    except Exception as exc:
        log.exception("推播失敗")
        report.status = "partial"
        report.error_kind = "notify_failed"
        report.error_detail = str(exc)


async def _skip(report, repo, deps, title, lines) -> RunReport:
    """護欄擋下的執行。不算失敗,也不用掉今天的額度。"""
    log.info("跳過本次執行:%s", title)
    report.status = "skipped"
    report.error_kind = "skipped"
    report.error_detail = title
    report.finished_at = report.started_at
    report.run_id = report.run_id or repo.start_run(report.started_at)
    repo.finish_run(report)
    try:
        await deps.notifier.send_alert(title, lines)
    except Exception:
        log.exception("跳過通知也送不出去")
    return report


async def _handle_misconfigured(deps, repo, report, exc, now) -> None:
    """403 但沒有挑戰頁特徵 = 我方設定問題,**不觸發熔斷**。

    熔斷是為了「104 在擋我們,先退開」而設計的。設定錯誤冷卻 24 小時之後
    設定還是錯的,只是白白少跑一天,而且會讓人以為是被封鎖而往錯的方向查。
    """
    log.error("來源設定有問題:%s", exc)
    report.status = "failed"
    report.error_kind = "SourceMisconfigured"
    report.error_detail = str(exc)
    report.finished_at = now
    repo.finish_run(report)

    try:
        await deps.notifier.send_alert(
            "104 回 403,但這是設定問題不是被封鎖",
            [
                str(exc),
                "",
                "熔斷器**沒有**啟動 —— 這不是被 104 擋,是請求少了東西。",
                "請檢查 config.yaml 的 scrape.referer 是否為 104 的網址。",
                "若 referer 沒問題,代表 104 改了 origin 規則,需要重新查證。",
            ],
        )
    except Exception:
        log.exception("告警送不出去")


async def _handle_block(cfg, deps, repo, report, circuit, exc, now) -> None:
    """被擋了。**絕不重試** —— 記錄、熔斷、告警,然後安靜退場。"""
    kind = type(exc).__name__
    log.error("抓取被中止(%s):%s", kind, exc)

    tripped = blocking.trip(circuit, f"{kind}: {exc}", now)
    repo.save_circuit(tripped)

    cooldown = blocking.cooldown_for(tripped.consecutive_failures, cfg.circuit)
    next_run = (
        (tripped.tripped_at + cooldown).isoformat()
        if cooldown and tripped.tripped_at
        else "需手動 `jobfinder reset-circuit` 解除"
    )

    report.status = "blocked"
    report.error_kind = kind
    report.error_detail = str(exc)
    report.finished_at = now
    repo.finish_run(report)

    lines = [
        f"原因:{exc}",
        f"頁面標題:{getattr(exc, 'page_title', '(無)') or '(無)'}",
        f"URL:{getattr(exc, 'url', '(無)') or '(無)'}",
        f"熔斷器:已連續觸發 {tripped.consecutive_failures} 次",
        f"下次可執行:{next_run}",
        "",
        "已停止所有後續請求,不會重試。",
    ]
    if isinstance(exc, LoggedInDetected):
        lines.append("⚠️ 這次是偵測到登入態,請刪除瀏覽器 profile 後重新 bootstrap。")

    try:
        await deps.notifier.send_alert("104 抓取被中止", lines)
    except Exception:
        log.exception("告警也送不出去")
