"""程式版命中率:技能詞典比對。只顯示、不參與閘門。"""

from __future__ import annotations

from pathlib import Path

from webui_helpers import detail, record, row, summary

from jobfinder.webui.candidates import SORT_KEYS, build_row, sort_rows
from jobfinder.webui.skill_match import resume_skills, skill_match

HAVE = frozenset({"Python", "SQL", "Tableau"})


def test_detail_splits_required_and_preferred_by_line():
    d = detail(jd="負責 ETL\n熟悉 Python 與 SQL\n會 Airflow 者佳\nAWS 經驗加分")["data"]
    m = skill_match(HAVE, summary(), d)
    assert m.source == "全文"
    assert m.matched == ["Python", "SQL"]
    assert m.missing == ["ETL / Data Pipeline"]
    assert m.preferred_missing == ["AWS", "Airflow"]
    assert m.rate == 2 / 3  # 加分不進分母


def test_specialty_field_counts_as_required():
    d = detail(jd="資料處理")["data"]
    d["condition"]["specialty"] = [{"description": "Power BI"}]
    assert "Power BI" in skill_match(HAVE, summary(), d).missing


def test_skill_in_both_required_and_preferred_lines_is_required():
    d = detail(jd="熟悉 Python\nPython 進階者佳")["data"]
    m = skill_match(HAVE, summary(), d)
    assert m.matched == ["Python"] and m.preferred_missing == []


def test_summary_only_when_no_detail():
    m = skill_match(HAVE, summary(description="使用 Python 與 Spark"), None)
    assert m.source == "摘要" and m.rate == 0.5


def test_no_recognizable_skill_is_none_not_zero():
    assert skill_match(HAVE, summary(jobName="業務", description="拜訪客戶"), None).rate is None


def test_program_rate_does_not_touch_gate3():
    r = build_row(record(detail_payload=detail(jd="熟悉 Spark")), None, HAVE)
    assert r.prog_rate == 0.0
    assert r.gates.passes_through(3)  # 只顯示,不擋人


def test_program_rate_is_sortable_and_skipped_without_resume():
    assert row().prog_rate is None  # 沒給履歷技能就不算
    rows = [
        build_row(record("lo", detail_payload=detail(jd="Python Spark")), None, HAVE),
        build_row(record("hi", detail_payload=detail(jd="Python SQL")), None, HAVE),
    ]
    assert [r.job_no for r in sort_rows(rows, "命中率(程式)")] == ["hi", "lo"]
    assert set(SORT_KEYS) >= {"命中率(LLM)", "命中率(程式)"}


def test_real_resume_skills_are_recognized():
    have = resume_skills((Path(__file__).resolve().parents[1] / "profile-de.md").read_text("utf-8"))
    assert {"Python", "SQL", "Tableau", "Power BI", "FastAPI"} <= have
    # 2026-09-24 補:履歷寫「R、SAS」「Streamlit」「BI / 視覺化」,舊詞典全漏
    assert {"SAS", "Streamlit", "Data Visualization"} <= have


def test_short_codes_need_word_boundary():
    from jobfinder.webui.skills import match_skills

    assert "SAS" not in match_skills("Sassy 的團隊")
