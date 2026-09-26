"""技能趨勢:兩層分開計 N、揭露率、「以上」型不入上限、規模分桶、分群矩陣。"""

from __future__ import annotations

from webui_helpers import detail, row

from jobfinder.webui import trends
from jobfinder.webui.skills import match_skills


def test_skill_dictionary_covers_data_engineering():
    got = match_skills("熟 Tableau、Power BI、dbt、SQL Server 與 Airflow")
    assert {"Tableau", "Power BI", "dbt", "SQL Server", "Airflow"} <= got


def test_java_does_not_match_javascript():
    assert "Java" not in match_skills("JavaScript 前端")


def test_layers_have_separate_denominators():
    rows = [
        row(job_no="a", summary_overrides={"description": "Python"}),
        row(job_no="b", summary_overrides={"description": "Python"}),
        row(job_no="c", detail_payload=detail(jd="Python 與 SQL")),
    ]
    summary = {s.skill: s for s in trends.skill_rates(rows, trends.SUMMARY_LAYER)}
    full = {s.skill: s for s in trends.skill_rates(rows, trends.DETAIL_LAYER)}
    assert summary["Python"].count == 2 and summary["Python"].rate == 2 / 3
    assert full["Python"].count == 1 and full["Python"].rate == 1.0  # N=1,不是 3


def test_per_job_dedupe():
    rows = [row(summary_overrides={"description": "Python python PYTHON"})]
    assert trends.skill_rates(rows, trends.SUMMARY_LAYER)[0].count == 1


def test_empty_detail_layer():
    assert trends.skill_rates([row()], trends.DETAIL_LAYER) == []


def test_salary_stats_disclosure_and_open_ended():
    rows = [
        row(summary_overrides={"s10": 50, "salaryLow": 40000, "salaryHigh": 60000}),
        row(summary_overrides={"s10": 50, "salaryLow": 55000, "salaryHigh": 9999999}),
        row(summary_overrides={"s10": 10, "salaryLow": 0, "salaryHigh": 0}),
        row(summary_overrides={"s10": 60, "salaryLow": 840000, "salaryHigh": 960000}),
    ]
    s = trends.salary_stats(rows)
    assert s.disclosure_rate == 3 / 4
    assert sorted(s.lows) == [40000, 55000, 70000]
    assert sorted(s.highs) == [60000, 80000]  # 「以上」型的 9,999,999 不列入
    assert s.open_ended == 1


def test_size_distribution_buckets_and_unknown():
    rows = [
        row(summary_overrides={"employeeCount": c})
        for c in (10, 30, 99, 100, 500, 999, 1000, 5000, None)
    ]
    dist = dict(trends.size_distribution(rows))
    assert dist == {
        "<30": 1,
        "30–99": 2,
        "100–499": 1,
        "500–999": 2,
        "1000–4999": 1,
        "5000+": 1,
        "未提供": 1,
    }


def test_weekly_rates_group_by_monday():
    rows = [
        row(first_seen="2026-09-21T08:00:00+08:00", summary_overrides={"description": "SQL"}),
        row(first_seen="2026-09-27T08:00:00+08:00", summary_overrides={"description": "無"}),
        row(first_seen="2026-09-28T08:00:00+08:00", summary_overrides={"description": "SQL"}),
    ]
    got = {(str(c["week"]), c["n"]): c["rate"] for c in trends.weekly_rates(rows, ["SQL"])}
    assert got == {("2026-09-21", 2): 0.5, ("2026-09-28", 1): 1.0}


def test_group_matrix_reports_n_per_group():
    rows = [
        row(title="資料工程師", summary_overrides={"description": "SQL"}),
        row(title="資料工程師", summary_overrides={"description": "SQL Python"}),
        row(title="AI工程師", summary_overrides={"description": "Python"}),
    ]
    skills, cells = trends.group_matrix(rows, lambda r: r.title_group, trends.SUMMARY_LAYER)
    lookup = {(c["group"], c["skill"]): c for c in cells}
    assert lookup[("資料工程", "SQL")]["rate"] == 1.0 and lookup[("資料工程", "SQL")]["n"] == 2
    assert lookup[("AI/演算法", "SQL")]["rate"] == 0.0
    assert "Python" in skills
