"""端到端流程測試,四個假件全注入,完全離線。

最重要的斷言是**「瀏覽器有沒有被打開」**:緊急開關、熔斷器、每日一次這三道護欄
必須在開瀏覽器之前就擋下來。若順序錯了,被 104 擋住的隔天早上還是會再送一次請求。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest

from jobfinder.config import load_config
from jobfinder.errors import ChallengeBlocked, LoggedInDetected
from jobfinder.models import JobDetail, JobSummary, ScoreBreakdown, ScoredJob
from jobfinder.pipeline import Deps, run_daily
from jobfinder.ports import DetailBatch, ScreenOutcome, SummaryBatch
from jobfinder.scrape import blocking
from jobfinder.storage import Database, JobRepo

NOW = datetime(2026, 8, 21, 8, 0, 0)


# ─── 假件 ───────────────────────────────────────────────────────────


class FakeSource:
    def __init__(self, jobs=None, *, raise_on_fetch=None):
        self.jobs = jobs or []
        self.raise_on_fetch = raise_on_fetch
        self.requests_used = 4

    async def fetch_summaries(self):
        if self.raise_on_fetch:
            raise self.raise_on_fetch
        return SummaryBatch(jobs=list(self.jobs))

    async def fetch_details(self, jobs):
        # 介面吃的是 JobSummary,因為來源需要 detail_id 才打得到詳細頁 API
        return DetailBatch(
            details={j.job_no: JobDetail(job_no=j.job_no, work_exp="不拘") for j in jobs}
        )


class SourceFactory:
    """記錄瀏覽器(source)到底有沒有被建立。"""

    def __init__(self, source):
        self.source = source
        self.opened = 0

    def __call__(self):
        @asynccontextmanager
        async def _cm():
            self.opened += 1
            yield self.source

        return _cm()


class FakeScorer:
    def __init__(self, score=85):
        self.score = score
        self.raw_responses = {}
        self.budget_exceeded = False

    async def screen(self, jobs):
        return ScreenOutcome(keep=list(jobs))

    async def deep(self, job, detail):
        return (
            ScoredJob(
                summary=job,
                detail=detail,
                breakdown=ScoreBreakdown(
                    tech_fit=30,
                    exp_fit=20,
                    domain_fit=15,
                    growth_fit=12,
                    practical_fit=8,
                ),
                total=self.score,
                verdict="apply",
                one_liner="測試用評分",
            ),
            0.001,
        )


class FakeNotifier:
    def __init__(self):
        self.summaries = []
        self.jobs = []
        self.alerts = []

    async def send_summary(self, report):
        self.summaries.append(report)

    async def send_job(self, scored):
        self.jobs.append(scored)

    async def send_alert(self, title, lines):
        self.alerts.append((title, lines))


def make_jobs(n=2):
    # raw 一定要帶:normalize 出來的職缺永遠有原始 JSON,而 --replay 就是靠它重建。
    # 假件少了 raw 會讓 replay 相關的行為在測試裡看起來是壞的。
    return [
        JobSummary(
            job_no=f"job{i}",
            detail_id=f"d{i}",
            job_name=f"AI工程師{i}",
            cust_name="示範公司",
            job_url=f"https://www.104.com.tw/job/job{i}",
            appear_date="20260821",
            matched_keywords=["AI工程師"],
            raw={
                "jobNo": f"job{i}",
                "jobName": f"AI工程師{i}",
                "custName": "示範公司",
                "link": {"job": f"//www.104.com.tw/job/job{i}"},
                "appearDate": "20260821",
            },
        )
        for i in range(n)
    ]


@pytest.fixture
def cfg():
    return load_config("config/config.yaml")


@pytest.fixture
def setup(tmp_path, cfg):
    def _build(source=None, scorer=None):
        factory = SourceFactory(source or FakeSource(make_jobs()))
        notifier = FakeNotifier()
        deps = Deps(
            source_factory=factory,
            scorer=scorer or FakeScorer(),
            notifier=notifier,
        )
        return deps, factory, notifier, str(tmp_path)

    return _build


def read_circuit(data_dir, cfg):
    with Database(data_dir, cfg.paths.db_filename) as db:
        return JobRepo(db.conn, cfg.dedupe).get_circuit()


# ─── 正常路徑 ───────────────────────────────────────────────────────


async def test_happy_path_notifies_and_records(cfg, setup):
    deps, factory, notifier, data_dir = setup()
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.status == "success"
    assert report.jobs_new == 2
    assert len(notifier.jobs) == 2
    assert len(notifier.summaries) == 1
    assert report.requests_used == 4
    assert factory.opened == 1


async def test_second_run_reports_nothing_new(cfg, setup):
    jobs = make_jobs()
    deps, _, _, data_dir = setup(FakeSource(jobs))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, _, notifier2, _ = setup(FakeSource(jobs))
    report = await run_daily(cfg, deps2, data_dir=data_dir, now=NOW + timedelta(days=1))
    assert report.jobs_new == 0
    assert notifier2.jobs == []
    # 0 則也要發摘要,靜默會讓使用者以為系統壞了
    assert len(notifier2.summaries) == 1


async def test_low_scores_are_listed_but_not_pushed(cfg, setup):
    deps, _, notifier, data_dir = setup(scorer=FakeScorer(score=42))
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert notifier.jobs == []
    assert len(report.rejected) == 2


# ═══ 護欄:被擋下時瀏覽器不該被打開 ═══════════════════════════════


async def test_kill_switch_short_circuits_before_opening_browser(cfg, setup, monkeypatch):
    monkeypatch.setenv("JOBFINDER_SCRAPE_DISABLED", "1")
    deps, factory, notifier, data_dir = setup()

    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.status == "skipped"
    assert factory.opened == 0, "緊急開關必須在開瀏覽器之前就擋下"
    assert notifier.alerts


async def test_open_circuit_short_circuits_before_opening_browser(cfg, setup):
    deps, factory, notifier, data_dir = setup()
    with Database(data_dir, cfg.paths.db_filename) as db:
        JobRepo(db.conn, cfg.dedupe).save_circuit(
            blocking.trip(blocking.CircuitState.closed(), "HTTP 403", NOW)
        )

    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(hours=2))

    assert report.status == "skipped"
    assert factory.opened == 0, "熔斷期間連瀏覽器都不該開"
    assert "熔斷" in notifier.alerts[0][0]


async def test_circuit_allows_retry_after_cooldown(cfg, setup):
    deps, factory, _, data_dir = setup()
    with Database(data_dir, cfg.paths.db_filename) as db:
        JobRepo(db.conn, cfg.dedupe).save_circuit(
            blocking.trip(blocking.CircuitState.closed(), "HTTP 403", NOW)
        )

    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(hours=25))
    assert report.status == "success"
    assert factory.opened == 1


async def test_daily_quota_blocks_a_second_run_the_same_day(cfg, setup):
    deps, _, _, data_dir = setup()
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, factory2, notifier2, _ = setup()
    report = await run_daily(cfg, deps2, data_dir=data_dir, now=NOW.replace(hour=20))

    assert report.status == "skipped"
    assert factory2.opened == 0
    assert "已經執行過" in notifier2.alerts[0][0]


async def test_force_overrides_the_daily_quota(cfg, setup):
    deps, _, _, data_dir = setup()
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, factory2, _, _ = setup()
    report = await run_daily(cfg, deps2, data_dir=data_dir, now=NOW.replace(hour=20), force=True)
    assert report.status == "success"
    assert factory2.opened == 1


# ═══ 被擋時的行為(SPEC.md 規則 1、2)══════════════════════════════


async def test_challenge_trips_circuit_and_alerts_without_retrying(cfg, setup):
    source = FakeSource(
        raise_on_fetch=ChallengeBlocked(
            "HTTP 403", url="https://www.104.com.tw/jobs/search/", page_title="Just a moment..."
        )
    )
    deps, factory, notifier, data_dir = setup(source)

    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.status == "blocked"
    assert report.error_kind == "ChallengeBlocked"
    assert factory.opened == 1, "只嘗試一次,絕不重試"

    circuit = read_circuit(data_dir, cfg)
    assert circuit.state == "open"
    assert circuit.consecutive_failures == 1

    title, lines = notifier.alerts[0]
    assert "中止" in title
    body = "\n".join(lines)
    assert "Just a moment" in body
    assert "不會重試" in body
    assert "下次可執行" in body


async def test_being_blocked_twice_escalates_the_cooldown(cfg, setup):
    blocked = ChallengeBlocked("HTTP 403")

    deps, _, _, data_dir = setup(FakeSource(raise_on_fetch=blocked))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, factory2, _, _ = setup(FakeSource(raise_on_fetch=blocked))
    await run_daily(cfg, deps2, data_dir=data_dir, now=NOW + timedelta(hours=25), force=True)

    circuit = read_circuit(data_dir, cfg)
    assert circuit.consecutive_failures == 2

    # 第二次之後冷卻期升級到 72 小時,48 小時後仍該被擋
    deps3, factory3, _, _ = setup()
    report = await run_daily(
        cfg, deps3, data_dir=data_dir, now=NOW + timedelta(hours=25 + 48), force=True
    )
    assert report.status == "skipped"
    assert factory3.opened == 0


async def test_logged_in_detection_aborts_with_a_specific_warning(cfg, setup):
    source = FakeSource(raise_on_fetch=LoggedInDetected("偵測到 104_session"))
    deps, _, notifier, data_dir = setup(source)

    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.status == "blocked"
    assert report.error_kind == "LoggedInDetected"
    body = "\n".join(notifier.alerts[0][1])
    assert "登入態" in body
    assert "重新 bootstrap" in body


async def test_successful_run_resets_the_failure_count(cfg, setup):
    deps, _, _, data_dir = setup(FakeSource(raise_on_fetch=ChallengeBlocked("403")))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)
    assert read_circuit(data_dir, cfg).consecutive_failures == 1

    deps2, _, _, _ = setup()
    await run_daily(cfg, deps2, data_dir=data_dir, now=NOW + timedelta(hours=25), force=True)
    circuit = read_circuit(data_dir, cfg)
    assert circuit.state == "closed"
    assert circuit.consecutive_failures == 0


async def test_blocked_run_is_recorded_for_audit(cfg, setup):
    deps, _, _, data_dir = setup(FakeSource(raise_on_fetch=ChallengeBlocked("403")))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    with Database(data_dir, cfg.paths.db_filename) as db:
        row = db.conn.execute(
            "SELECT status, error_kind FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "blocked"
    assert row["error_kind"] == "ChallengeBlocked"


# ═══ 韌性 ═══════════════════════════════════════════════════════════


async def test_notification_failure_does_not_lose_the_run_record(cfg, setup):
    deps, _, notifier, data_dir = setup()

    async def boom(_report):
        raise RuntimeError("Telegram 掛了")

    notifier.send_summary = boom
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.status == "partial"
    assert report.error_kind == "notify_failed"
    with Database(data_dir, cfg.paths.db_filename) as db:
        assert db.conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 2


async def test_deep_score_failure_skips_only_that_job(cfg, setup):
    scorer = FakeScorer()
    calls = {"n": 0}
    original = scorer.deep

    async def flaky(job, detail):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模型爆了")
        return await original(job, detail)

    scorer.deep = flaky
    deps, _, notifier, data_dir = setup(scorer=scorer)
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.jobs_deep_scored == 1
    assert len(notifier.jobs) == 1
    assert any("深評失敗" in w for w in report.warnings)


async def test_schema_drift_is_surfaced_as_a_warning(cfg, setup):
    class DriftySource(FakeSource):
        async def fetch_summaries(self):
            return SummaryBatch(jobs=make_jobs(1), drift=["少了 salaryDesc"] * 3)

    deps, _, _, data_dir = setup(DriftySource())
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    assert report.schema_drift_count == 3
    assert any("改版" in w for w in report.warnings)


async def test_zero_intercepted_responses_warns_about_a_path_change(cfg, setup):
    class SilentSource(FakeSource):
        async def fetch_summaries(self):
            return SummaryBatch(
                jobs=[], observed_urls=["https://www.104.com.tw/jobs/search/api/v2/list"]
            )

    deps, _, _, data_dir = setup(SilentSource())
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    warning = " ".join(report.warnings)
    assert "可能已改版" in warning
    assert "api/v2/list" in warning


# ═══ 離線重跑(--replay / --from-fixtures)══════════════════════════


async def test_rescore_all_reprocesses_already_seen_jobs(cfg, setup):
    """`--replay` 的唯一用途就是拿舊資料重跑評分調 prompt。

    若被去重擋掉,第二次之後永遠是 0 筆 —— 那等於整個旗標作廢。
    """
    jobs = make_jobs()
    deps, _, _, data_dir = setup(FakeSource(jobs))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, _, notifier2, _ = setup(FakeSource(jobs))
    report = await run_daily(
        cfg,
        deps2,
        data_dir=data_dir,
        now=NOW + timedelta(days=1),
        rescore_all=True,
    )

    assert report.jobs_new == 2
    assert report.jobs_deep_scored == 2
    assert len(notifier2.jobs) == 2


async def test_rescore_all_still_writes_jobs_so_replay_keeps_working(cfg, setup):
    """重跑模式仍要寫進 jobs 表,否則 scores 的外鍵掛不上,也就無法再 replay。"""
    deps, _, _, data_dir = setup()
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW, rescore_all=True)

    with Database(data_dir, cfg.paths.db_filename) as db:
        repo = JobRepo(db.conn, cfg.dedupe)
        assert db.conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 2
        assert db.conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"] == 2
        run_id = db.conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
        assert len(repo.load_replay(run_id)) == 2


async def test_dedupe_still_applies_by_default(cfg, setup):
    """正式排程走的是預設路徑,去重必須照常生效。"""
    jobs = make_jobs()
    deps, _, _, data_dir = setup(FakeSource(jobs))
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)

    deps2, _, notifier2, _ = setup(FakeSource(jobs))
    report = await run_daily(cfg, deps2, data_dir=data_dir, now=NOW + timedelta(days=1))

    assert report.jobs_new == 0
    assert notifier2.jobs == []
