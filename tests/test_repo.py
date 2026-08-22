"""去重邏輯測試。

去重是日報的價值來源:「只告訴我沒看過的」。這裡壞掉的症狀是使用者每天收到一樣的東西,
或反過來永遠收不到 —— 兩種都會讓整個專案失去意義。SPEC.md §5 的六個情境全部要覆蓋。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jobfinder.config import DedupeCfg
from jobfinder.models import JobSummary
from jobfinder.normalize import merge_summaries
from jobfinder.storage import JobRepo, connect, ensure_schema

DAY0 = datetime(2026, 8, 21, 8, 0, 0)


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "test.db")
    ensure_schema(conn)
    return JobRepo(conn, DedupeCfg(repost_cooldown_days=30, retain_days=180))


def make_job(job_no="a1b2c", *, appear_date="20260821", keyword="AI工程師", **kw):
    base = {
        "job_no": job_no,
        # 詳細頁 API 只吃 detail_id,與 job_no 是不同的東西
        "detail_id": f"d{job_no}",
        "job_name": "AI工程師",
        "cust_name": "示範公司",
        "job_url": f"https://www.104.com.tw/job/{job_no}",
        "appear_date": appear_date,
        "matched_keywords": [keyword],
    }
    base.update(kw)
    return JobSummary(**base)


# ─── SPEC.md §5 的六個情境 ──────────────────────────────────────────


def test_brand_new_job_is_new(repo):
    assert [j.job_no for j in repo.register([make_job()], DAY0)] == ["a1b2c"]


def test_same_job_same_appear_date_is_not_new(repo):
    repo.register([make_job()], DAY0)
    assert repo.register([make_job()], DAY0 + timedelta(days=1)) == []


def test_reposted_within_cooldown_is_not_new(repo):
    """雇主天天刷新上架日,不該天天推播。"""
    repo.register([make_job(appear_date="20260821")], DAY0)
    again = repo.register([make_job(appear_date="20260822")], DAY0 + timedelta(days=1))
    assert again == []


def test_reposted_after_cooldown_is_new_again(repo):
    """真的隔了很久重新開缺,值得再提醒一次。"""
    repo.register([make_job(appear_date="20260821")], DAY0)
    later = repo.register([make_job(appear_date="20261101")], DAY0 + timedelta(days=45))
    assert [j.job_no for j in later] == ["a1b2c"]


def test_screened_out_job_never_comes_back(repo):
    """已經判定不適合的,重新上架也不必再煩使用者。"""
    repo.register([make_job()], DAY0)
    repo.mark_status(["a1b2c"], "screened_out")
    assert repo.register([make_job(appear_date="20261101")], DAY0 + timedelta(days=60)) == []


def test_two_keywords_hitting_same_job_store_one_row_with_both(repo):
    jobs = [
        make_job(keyword="AI工程師"),
        make_job(keyword="LLM工程師"),
    ]
    merged = merge_summaries(jobs)
    assert len(merged) == 1
    assert merged[0].matched_keywords == ["AI工程師", "LLM工程師"]

    new = repo.register(merged, DAY0)
    assert len(new) == 1
    row = repo.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
    assert row["n"] == 1


def test_keywords_accumulate_across_runs(repo):
    repo.register([make_job(keyword="AI工程師")], DAY0)
    repo.register([make_job(keyword="MLOps")], DAY0 + timedelta(days=1))
    row = repo.conn.execute("SELECT matched_keywords FROM jobs WHERE job_no='a1b2c'").fetchone()
    assert "MLOps" in row["matched_keywords"]
    assert "AI工程師" in row["matched_keywords"]


# ─── 其他行為 ───────────────────────────────────────────────────────


def test_seen_count_and_last_seen_advance(repo):
    repo.register([make_job()], DAY0)
    repo.register([make_job()], DAY0 + timedelta(days=1))
    row = repo.conn.execute("SELECT seen_count, last_seen_at FROM jobs").fetchone()
    assert row["seen_count"] == 2
    assert row["last_seen_at"].startswith("2026-08-22")


def test_merge_summaries_preserves_first_seen_order():
    jobs = [make_job("aaa"), make_job("bbb"), make_job("aaa", keyword="MLOps")]
    assert [j.job_no for j in merge_summaries(jobs)] == ["aaa", "bbb"]


# ─── 每日一次(SPEC.md 規則 3)──────────────────────────────────────


def test_has_run_today_blocks_a_second_run(repo):
    assert not repo.has_run_today(DAY0)
    repo.start_run(DAY0)
    assert repo.has_run_today(DAY0.replace(hour=20))
    assert not repo.has_run_today(DAY0 + timedelta(days=1))


def test_skipped_runs_do_not_consume_the_daily_quota(repo):
    """被熔斷擋下的執行根本沒碰到 104,不該用掉今天的額度。"""
    run_id = repo.start_run(DAY0)
    repo.conn.execute("UPDATE runs SET status='skipped' WHERE id=?", (run_id,))
    repo.conn.commit()
    assert not repo.has_run_today(DAY0)


# ─── 熔斷器持久化(SPEC.md 規則 2)──────────────────────────────────


def test_circuit_state_round_trips(repo):
    from jobfinder.scrape import blocking

    assert repo.get_circuit().state == "closed"
    tripped = blocking.trip(blocking.CircuitState.closed(), "HTTP 403", DAY0)
    repo.save_circuit(tripped)

    restored = repo.get_circuit()
    assert restored.state == "open"
    assert restored.consecutive_failures == 1
    assert restored.reason == "HTTP 403"
    assert restored.tripped_at == DAY0


def test_circuit_survives_reconnect(tmp_path):
    """熔斷狀態丟了就會在冷卻期內又跑一次 —— 必須真的落地。"""
    from jobfinder.scrape import blocking

    path = tmp_path / "c.db"
    conn = connect(path)
    ensure_schema(conn)
    JobRepo(conn).save_circuit(blocking.trip(blocking.CircuitState.closed(), "被擋", DAY0))
    conn.close()

    conn2 = connect(path)
    ensure_schema(conn2)
    assert JobRepo(conn2).get_circuit().consecutive_failures == 1


# ─── 維護 ───────────────────────────────────────────────────────────


def test_prune_removes_stale_rows(repo):
    repo.register([make_job("old1")], DAY0 - timedelta(days=400))
    repo.register([make_job("fresh")], DAY0)
    assert repo.prune(DAY0) == 1
    remaining = [r["job_no"] for r in repo.conn.execute("SELECT job_no FROM jobs")]
    assert remaining == ["fresh"]
