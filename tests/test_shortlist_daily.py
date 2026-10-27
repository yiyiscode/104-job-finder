"""投遞清單的每日補抓:跟補深評佇列一樣,只吃當天**剩下的**詳細頁額度。

另一個要擋的回歸:「jobs.db 有那一列」不等於「有全文」—— 被規則層或粗篩刷掉的職缺
有列沒全文,清單曾因此永遠不抓它們。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_backfill import BudgetedSource, DropAllScorer
from test_pipeline import NOW, FakeNotifier, FakeScorer, FakeSource, SourceFactory, make_jobs

from jobfinder.config import load_config
from jobfinder.pipeline import Deps, run_daily
from jobfinder.shortlist.store import ShortlistStore
from jobfinder.storage import Database, JobRepo


@pytest.fixture
def cfg():
    return load_config("config/config.yaml")


@pytest.fixture
def store(tmp_path):
    return ShortlistStore(tmp_path / "shortlist.db")


async def run(cfg, tmp_path, source, store, *, day=1, scorer=None, **kw):
    deps = Deps(
        source_factory=SourceFactory(source),
        scorer=scorer or FakeScorer(),
        notifier=FakeNotifier(),
        shortlist=store,
    )
    return await run_daily(cfg, deps, data_dir=str(tmp_path), now=NOW + timedelta(days=day), **kw)


def add(store, *detail_ids):
    for d in detail_ids:
        store.add(d, "", "", NOW)


async def test_only_uses_leftover_detail_quota(cfg, tmp_path, store):
    add(store, "aaaa1", "bbbb2", "cccc3")
    source = BudgetedSource(details=2)
    report = await run(cfg, tmp_path, source, store)

    assert source.detail_calls == [["aaaa1"], ["bbbb2"]]
    assert source.requests_used == 2
    assert report.shortlist_fetched == 2
    assert [e.detail_id for e in store.to_fetch(set())] == ["cccc3"]  # 明天再抓


async def test_todays_new_jobs_get_the_quota_first(cfg, tmp_path, store):
    add(store, "aaaa1", "bbbb2")
    source = BudgetedSource(make_jobs(2), details=3)
    report = await run(cfg, tmp_path, source, store)

    assert source.detail_calls[0] == ["job0", "job1"]
    assert source.detail_calls[1:] == [["aaaa1"]]
    assert report.shortlist_fetched == 1


async def test_jobs_db_row_without_detail_is_fetched_and_filled(cfg, tmp_path, store):
    """第一天全被粗篩刷掉 → 有列沒全文。清單要抓它,並把全文補回 jobs.db 那一列。"""
    await run(cfg, tmp_path, FakeSource(make_jobs(2)), None, day=0, scorer=DropAllScorer())
    add(store, "job0")
    source = BudgetedSource(details=18)
    report = await run(cfg, tmp_path, source, store)

    assert source.detail_calls == [["job0"]]
    assert report.shortlist_fetched == 1
    with Database(str(tmp_path), cfg.paths.db_filename) as db:
        index = JobRepo(db.conn, cfg.dedupe).detail_index()
    assert index["job0"] == ("job0", True)
    assert index["job1"] == ("job1", False)  # 不在清單的不動


async def test_jobs_db_row_with_detail_is_not_refetched(cfg, tmp_path, store):
    await run(cfg, tmp_path, BudgetedSource(make_jobs(1), details=18), None, day=0)
    add(store, "job0")
    source = BudgetedSource(details=18)
    await run(cfg, tmp_path, source, store)
    assert source.detail_calls == []


async def test_closed_job_is_not_fetched_again(cfg, tmp_path, store):
    add(store, "aaaa1")
    await run(cfg, tmp_path, BudgetedSource(details=18, closed={"aaaa1"}), store)
    source = BudgetedSource(details=18)
    await run(cfg, tmp_path, source, store, day=2)
    assert source.detail_calls == []


def deep_scored(cfg, tmp_path):
    with Database(str(tmp_path), cfg.paths.db_filename) as db:
        rows = db.conn.execute("SELECT DISTINCT job_no FROM scores WHERE stage='deep'")
        return {r["job_no"] for r in rows}


async def test_jobs_db_row_is_deep_scored_after_fetch_but_not_pushed(cfg, tmp_path, store):
    """被粗篩刷掉 → 清單補抓到全文 → 同一次執行補深評;分數存 jobs.db,不進日報。"""
    await run(cfg, tmp_path, FakeSource(make_jobs(2)), None, day=0, scorer=DropAllScorer())
    add(store, "job0")
    report = await run(cfg, tmp_path, BudgetedSource(details=18), store)

    assert report.shortlist_scored == 1
    assert report.notified == []
    assert deep_scored(cfg, tmp_path) == {"job0"}  # 不在清單的 job1 不評


async def test_manually_fetched_detail_moves_into_jobs_db_without_refetch(cfg, tmp_path, store):
    """手動 shortlist fetch 只存 shortlist.db;之前 jobs.db 那列永遠拿不到全文、也就永遠不評。"""
    await run(cfg, tmp_path, FakeSource(make_jobs(1)), None, day=0, scorer=DropAllScorer())
    add(store, "job0")
    store.record_fetch("job0", NOW, raw_detail="{}")  # 模擬手動 shortlist fetch 抓到的
    source = BudgetedSource(details=18)
    report = await run(cfg, tmp_path, source, store)

    assert source.detail_calls == []  # 已經有全文,不再對 104 發請求
    assert report.shortlist_scored == 1
    with Database(str(tmp_path), cfg.paths.db_filename) as db:
        assert JobRepo(db.conn, cfg.dedupe).detail_index()["job0"] == ("job0", True)


async def test_shortlist_deep_scoring_is_capped_per_run(cfg, tmp_path, store):
    from jobfinder.pipeline import SHORTLIST_DEEP_PER_RUN

    n = SHORTLIST_DEEP_PER_RUN + 2
    await run(cfg, tmp_path, FakeSource(make_jobs(n)), None, day=0, scorer=DropAllScorer())
    add(store, *[f"job{i}" for i in range(n)])
    first = await run(cfg, tmp_path, BudgetedSource(details=18), store)
    second = await run(cfg, tmp_path, BudgetedSource(details=18), store, day=2)

    assert first.shortlist_scored == SHORTLIST_DEEP_PER_RUN
    assert second.shortlist_scored == 2  # 剩下的明天接著評,評過的不重評
    assert len(deep_scored(cfg, tmp_path)) == n


async def test_offline_and_limit_runs_never_fetch_shortlist(cfg, tmp_path, store):
    add(store, "aaaa1")
    report = await run(cfg, tmp_path, FakeSource([]), store)
    assert report.shortlist_fetched == 0

    source = BudgetedSource(details=18)
    await run(cfg, tmp_path, source, store, day=2, limit=2)
    assert source.detail_calls == []
    assert [e.detail_id for e in store.to_fetch(set())] == ["aaaa1"]
