"""自評 app 的邏輯(webui/selfeval.py)與 decisions.db 的兩張新表。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from webui_helpers import row

from jobfinder.webui import selfeval as se
from jobfinder.webui.decisions import DecisionStore

THIS_WEEK = date(2026, 10, 5)
NOW = datetime(2026, 10, 7, 9, 0)


def _job(n, score, status="scored", seen="2026-10-06T08:00:00+08:00"):
    return row(job_no=n, score=score, status=status, first_seen=seen)


# ── 抽樣 ──
def test_sample_only_below_median_scored_and_stable_within_week():
    rows = [_job(f"j{i}", 40 + i * 5) for i in range(12)]
    rows.append(_job("noscore", None, status="screened_out"))
    since, median, picks = se.weekly_sample(rows, THIS_WEEK)
    assert since == THIS_WEEK
    assert all(r.deep_score < median for r in picks)
    assert "noscore" not in {r.job_no for r in picks}, "沒深評的當不了校正模型的反例"
    assert [r.job_no for r in picks] == [r.job_no for r in se.weekly_sample(rows, THIS_WEEK)[2]]


def test_sample_looks_back_when_this_week_is_thin():
    rows = [_job(f"old{i}", 50 + i, seen="2026-09-22T08:00:00+08:00") for i in range(12)]
    since, _, picks = se.weekly_sample(rows, THIS_WEEK)
    assert since < THIS_WEEK and picks


def test_sample_empty_pool():
    assert se.weekly_sample([], THIS_WEEK)[2] == []


# ── 佇列 ──
def test_queue_order_dedup_and_half_rated_comes_back():
    rows = [_job(f"s{i}", 30 + i) for i in range(12)]  # 低分 → 抽樣候選
    rows += [_job("n1", 90, status="notified"), _job("n2", 85, status="notified")]
    rows.append(_job("none", None, status="filtered_out"))
    desires = {"n1": 4}  # n1 只評了想去,還沒評準不準 → 要回來
    feedback = {"n2": "ok"}
    queue = se.build_queue(rows, desires, feedback, THIS_WEEK)
    sources = [c.source for c in queue]
    assert sources[: sources.count("sample")] == ["sample"] * sources.count("sample")
    assert {"n1", "n2"} <= {c.job.job_no for c in queue}
    assert "none" not in {c.job.job_no for c in queue}
    assert len({c.job.job_no for c in queue}) == len(queue), "抽樣與推播重疊時只留一份"


def test_queue_skips_fully_rated_and_caps_size():
    rows = [_job(f"j{i}", 50 + i % 40) for i in range(50)]
    done = {f"j{i}" for i in range(10)}
    queue = se.build_queue(
        rows, dict.fromkeys(done, 3), dict.fromkeys(done, "ok"), THIS_WEEK, size=30
    )
    assert len(queue) == 30
    assert not done & {c.job.job_no for c in queue}


# ── 指標 ──
def test_spearman():
    assert se.spearman([(1, 1), (2, 2), (3, 3)]) == pytest.approx(1.0)
    assert se.spearman([(1, 3), (2, 2), (3, 1)]) == pytest.approx(-1.0)
    assert se.spearman([(1, 1), (2, 2)]) is None
    assert se.spearman([(1, 3), (2, 3), (3, 3)]) is None  # 一邊全同


def test_metrics_counts_only_jobs_with_deep_score():
    rows = [_job("a", 80), _job("b", 50), _job("c", None)]
    m = se.metrics(rows, {"a": 5, "b": 1, "c": 4}, {"a": "ok"})
    assert m.labeled == [(80, 5), (50, 1)]
    assert (m.positives, m.negatives) == (1, 1)
    assert m.feedback["ok"] == 1


# ── 技能自評寫回履歷 ──
RESUME = "# 名字\n\n## 技能\n\nPython\n\n## 學歷\n\n台科大\n"


def test_skills_block_inserted_before_education_then_replaced_in_place():
    once = se.apply_skills_block(RESUME, {"SQL": 4, "Spark": 1})
    assert once.index("skill-self-rating:start") < once.index("## 學歷")
    assert "| SQL | 4 能獨立負責一塊 |" in once
    twice = se.apply_skills_block(once, {"SQL": 5})
    assert twice.count("skill-self-rating:start") == 1
    assert "Spark" not in twice and "| SQL | 5" in twice
    assert twice.replace(se.skills_block({"SQL": 5}), "").startswith("# 名字")


def test_skills_block_removed_when_empty_and_rest_untouched():
    with_block = se.apply_skills_block(RESUME, {"SQL": 3})
    assert se.apply_skills_block(with_block, {}) == RESUME


def test_skills_block_appended_when_no_education_heading():
    out = se.apply_skills_block("# 名字\n", {"SQL": 2})
    assert out.startswith("# 名字\n\n") and out.rstrip().endswith("-->")


# ── decisions.db 新表 ──
def test_feedback_records_score_and_model_latest_wins(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    store.give_feedback("J1", "high", 88, "google/gemini-2.5-flash", NOW)
    store.give_feedback("J1", "ok", 79, "claude-cli:sonnet", NOW)
    assert store.latest_feedback() == {"J1": "ok"}
    with pytest.raises(ValueError):
        store.give_feedback("J1", "meh", 70, "m", NOW)


def test_skill_ratings_latest_wins_and_clear(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    store.rate_skill("SQL", 3, NOW)
    store.rate_skill("SQL", 4, NOW)
    store.rate_skill("Spark", 2, NOW)
    store.rate_skill("Spark", None, NOW)
    assert store.latest_skills() == {"SQL": 4}
    with pytest.raises(ValueError):
        store.rate_skill("SQL", 6, NOW)
