"""補深評佇列:被舊粗篩規則誤刷、仍在架上的職缺,用每日排程**剩下的**詳細頁額度補抓並深評。

最重要的斷言是「**不多發任何一次 104 請求**」—— 補抓只能吃當天沒用完的名額。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_pipeline import (
    NOW,
    FakeNotifier,
    FakeScorer,
    FakeSource,
    SourceFactory,
    make_jobs,
)

from jobfinder.config import load_config
from jobfinder.models import JobDetail, RejectedJob
from jobfinder.pipeline import Deps, run_daily
from jobfinder.ports import DetailBatch, ScreenOutcome, SummaryBatch
from jobfinder.storage import Database, JobRepo


@pytest.fixture
def cfg():
    return load_config("config/config.yaml")


@pytest.fixture
def setup(tmp_path):
    def _build(source, scorer):
        deps = Deps(source_factory=SourceFactory(source), scorer=scorer, notifier=FakeNotifier())
        return deps, None, deps.notifier, str(tmp_path)

    return _build


class FakeBudget:
    def __init__(self, details: int, navigations: int = 40):
        self.details_remaining = details
        self.navigations_remaining = navigations


class BudgetedSource:
    """有 ``budget`` = 真的連線的來源。``closed`` 回 104 的「職務不存在」,``flaky`` 暫時失敗。"""

    def __init__(self, jobs=(), *, details: int, closed=(), flaky=()):
        self.jobs = list(jobs)
        self.budget = FakeBudget(details)
        self.closed, self.flaky = set(closed), set(flaky)
        self.requests_used = 0
        self.detail_calls: list[list[str]] = []

    async def fetch_summaries(self):
        return SummaryBatch(jobs=list(self.jobs))

    async def fetch_details(self, jobs):
        self.detail_calls.append([j.job_no for j in jobs])
        batch = DetailBatch()
        for job in jobs:
            if self.budget.details_remaining <= 0:
                batch.truncated_by_budget = True
                break
            self.budget.details_remaining -= 1
            self.requests_used += 1
            if job.job_no in self.flaky:
                batch.drift.append(f"{job.job_no}: 詳細頁取得失敗")
                continue
            raw = (
                {"error": {"code": 11201, "message": "職務不存在"}}
                if job.job_no in self.closed
                else {}
            )
            batch.details[job.job_no] = JobDetail(job_no=job.job_no, raw=raw, work_exp="不拘")
        return batch


class DropAllScorer(FakeScorer):
    """第一天:舊規則把全部刷掉 —— 製造出「沒有詳細頁、沒有深評」的職缺。"""

    async def screen(self, jobs):
        return ScreenOutcome(
            dropped=[
                RejectedJob(
                    job_no=j.job_no,
                    job_name=j.job_name,
                    cust_name=j.cust_name,
                    total=20,
                    reason="要求2年以上經驗",
                )
                for j in jobs
            ]
        )


async def seed(cfg, setup, n=3, priorities=None):
    """第一天跑完、全部被刷掉,再把它們排進佇列。回傳 data_dir。"""
    jobs = make_jobs(n)
    deps, _, _, data_dir = setup(FakeSource(jobs), DropAllScorer())
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW)
    priorities = priorities or {j.job_no: 90 - i for i, j in enumerate(jobs)}
    with Database(data_dir, cfg.paths.db_filename) as db:
        JobRepo(db.conn, cfg.dedupe).enqueue_backfill(priorities, NOW)
    return data_dir


def queue(data_dir, cfg):
    with Database(data_dir, cfg.paths.db_filename) as db:
        return {
            r["job_no"]: (r["outcome"], r["attempts"])
            for r in db.conn.execute("SELECT * FROM backfill_queue")
        }


async def day2(cfg, setup, data_dir, source, scorer=None):
    deps, _, notifier, _ = setup(source, scorer or FakeScorer())
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(days=1))
    return report, notifier


async def test_backfill_only_uses_leftover_detail_quota(cfg, setup):
    """額度只剩 2 → 只補優先度最高的 2 筆,第 3 筆留到明天。"""
    data_dir = await seed(cfg, setup)
    source = BudgetedSource(details=2)
    report, _ = await day2(cfg, setup, data_dir, source)

    assert source.detail_calls == [["job0", "job1"]]
    assert source.requests_used == 2
    assert report.backfilled == 2
    q = queue(data_dir, cfg)
    assert q["job0"][0] == q["job1"][0] == "scored"
    assert q["job2"] == (None, 0)


async def test_todays_new_jobs_get_the_quota_first(cfg, setup):
    """當天新職缺的詳細頁永遠優先;補抓只吃剩下的。"""
    data_dir = await seed(cfg, setup)
    new = make_jobs(5)[3:]  # job3、job4 是今天新出現的
    source = BudgetedSource(new, details=3)
    report, _ = await day2(cfg, setup, data_dir, source)

    assert source.detail_calls[0] == ["job3", "job4"]
    assert source.detail_calls[1] == ["job0"]
    assert report.backfilled == 1


async def test_backfilled_jobs_are_scored_but_not_pushed(cfg, setup):
    """日報只推「今天新出現的」;補評的分數照存、標 scored,UI 看得到。"""
    data_dir = await seed(cfg, setup)
    report, notifier = await day2(cfg, setup, data_dir, BudgetedSource(details=18))

    assert report.backfilled == 3
    assert notifier.jobs == []
    with Database(data_dir, cfg.paths.db_filename) as db:
        statuses = {r["status"] for r in db.conn.execute("SELECT status FROM jobs")}
        deep = db.conn.execute("SELECT count(*) FROM scores WHERE stage='deep'").fetchone()[0]
    assert statuses == {"scored"}
    assert deep == 3


async def test_closed_jobs_leave_the_queue_without_scoring(cfg, setup):
    data_dir = await seed(cfg, setup)
    report, _ = await day2(cfg, setup, data_dir, BudgetedSource(details=18, closed={"job1"}))

    assert report.backfilled == 2
    assert queue(data_dir, cfg)["job1"] == ("closed", 0)


async def test_flaky_detail_retries_then_gives_up(cfg, setup):
    """暫時失敗記一次、明天再試;滿 3 次放棄,不能每天吃掉一個名額。"""
    data_dir = await seed(cfg, setup, n=1)
    for day in range(1, 4):
        source = BudgetedSource(details=18, flaky={"job0"})
        deps, _, _, _ = setup(source, FakeScorer())
        await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(days=day))
    assert queue(data_dir, cfg)["job0"] == ("gave_up", 3)

    source = BudgetedSource(details=18)
    deps, _, _, _ = setup(source, FakeScorer())
    await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(days=4))
    assert source.detail_calls == []  # 放棄的不再抓


async def test_offline_sources_never_backfill(cfg, setup):
    """重播 / fixture 沒有 budget —— 它們不連 104,也不該動佇列。"""
    data_dir = await seed(cfg, setup)
    report, _ = await day2(cfg, setup, data_dir, FakeSource([]))
    assert report.backfilled == 0
    assert all(v == (None, 0) for v in queue(data_dir, cfg).values())


async def test_limit_runs_do_not_backfill(cfg, setup):
    """``--limit`` 是手動試跑(硬上限 5),不該順便吃佇列。"""
    data_dir = await seed(cfg, setup)
    source = BudgetedSource(details=18)
    deps, _, _, _ = setup(source, FakeScorer())
    report = await run_daily(cfg, deps, data_dir=data_dir, now=NOW + timedelta(days=1), limit=2)
    assert report.backfilled == 0
    assert source.detail_calls == []


async def test_enqueue_skips_unknown_scored_and_duplicate_jobs(cfg, setup):
    data_dir = await seed(cfg, setup, n=2)
    await day2(cfg, setup, data_dir, BudgetedSource(details=1))  # job0 已補評
    with Database(data_dir, cfg.paths.db_filename) as db:
        repo = JobRepo(db.conn, cfg.dedupe)
        added = repo.enqueue_backfill({"job0": 99, "job1": 99, "ghost": 99}, NOW)
    assert added == 0  # job0 已深評、job1 已在佇列、ghost 不在 jobs 表
