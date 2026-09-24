"""候選清單:原始 JSON → JobRow、職稱/產業分類、篩選、排序、匯出。"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime

from webui_helpers import detail, record, row

from jobfinder.storage.db import connect, ensure_schema
from jobfinder.webui.candidates import (
    CandidateFilter,
    apply_filter,
    build_row,
    load_rows,
    picks,
    sort_rows,
    week_start,
)
from jobfinder.webui.decisions import Decision
from jobfinder.webui.export import to_csv, to_markdown
from jobfinder.webui.groups import industry_group, title_group, title_matches

ALL_INDUSTRIES = ["半導體/電子", "金融", "軟體", "其他"]
ALL_GROUPS = ["資料工程", "資料科學/ML", "資料分析/BI", "AI/演算法", "軟體開發", "其他"]


def decision(job_no: str, status: str, note: str = "") -> Decision:
    reason = "其他" if status == "skip" else None
    return Decision(1, job_no, status, reason, note, "2026-09-22T09:00:00+08:00")


# ── 分類 ──
def test_industry_by_code_prefix():
    assert industry_group(1001006001) == "半導體/電子"
    assert industry_group("1004001") == "金融"
    assert industry_group(1001001002) == "軟體"
    assert industry_group(1009001) == "其他"
    assert industry_group(None) == "其他"


def test_data_engineering_wins_over_ai():
    assert title_group("AI 數據開發工程師") == "資料工程"
    assert title_group("Senior Data & AI Engineer") == "資料工程"
    assert title_group("AI工程師") == "AI/演算法"


def test_kangxi_radical_title_normalized():
    """真實資料:「資料⼯程師」的「⼯」是 U+2F2F 康熙部首,不做 NFKC 就會掉到「其他」。"""
    assert title_group("【總公司】資料⼯程師(人工智慧部)") == "資料工程"


def test_title_query_or_and_literal():
    assert title_matches("資深 ETL 工程師", "資料工程|etl")
    assert not title_matches("前端工程師", "資料工程|etl")
    assert title_matches("C++ 工程師", "C++")  # 當字面字串,不是正則
    assert title_matches("任何職稱", "  ")


# ── 組裝 ──
def test_open_ended_salary_text():
    r = row(summary_overrides={"s10": 50, "salaryLow": 39000, "salaryHigh": 9999999})
    assert r.salary_text == "月薪 39,000 元以上"


def test_english_from_language_code_or_text():
    assert row(summary_overrides={"languageRequirements": [{"language": 1}]}).english
    assert row(detail_payload=detail(jd="TOEIC 750 以上")).english
    assert not row().english


def test_first_seen_in_taipei_date():
    r = row(first_seen="2026-09-21T17:30:00+00:00")  # UTC 週一 17:30 = 台北週二 01:30
    assert r.first_seen == date(2026, 9, 22)


def test_detail_layer_only_when_detail_exists():
    assert row().detail_text is None
    assert "ETL" in row(detail_payload=detail()).detail_text


def test_corrupt_json_does_not_crash():
    rec = record()
    rec["raw_detail"] = "{not json"
    assert build_row(rec).has_detail is False


def test_load_rows_joins_latest_deep_score(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    ensure_schema(conn)
    rec = record("J1")
    conn.execute(
        "INSERT INTO jobs (job_no, job_name, cust_name, job_url, area_desc, edu_desc,"
        " first_seen_at, last_seen_at, last_new_at, status, raw_summary)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "J1",
            rec["job_name"],
            rec["cust_name"],
            rec["job_url"],
            rec["area_desc"],
            rec["edu_desc"],
            rec["first_seen_at"],
            rec["first_seen_at"],
            rec["first_seen_at"],
            "scored",
            rec["raw_summary"],
        ),
    )
    for stage, score in [("deep", 70), ("screen", 99), ("deep", 85)]:
        conn.execute(
            "INSERT INTO scores (job_no, stage, model, total_score, created_at)"
            " VALUES ('J1', ?, 'm', ?, ?)",
            (stage, score, datetime(2026, 9, 22).isoformat()),
        )
    conn.commit()
    rows = load_rows(conn)
    conn.close()
    assert len(rows) == 1
    assert rows[0].deep_score == 85  # 最新一筆深評,不是粗篩


# ── 篩選 ──
def _filter(**kw) -> CandidateFilter:
    base = dict(
        start=date(2026, 9, 21),
        end=date(2026, 9, 27),
        industries=ALL_INDUSTRIES,
        title_groups=ALL_GROUPS,
        gate="全部",
    )
    base.update(kw)
    return CandidateFilter(**base)


def test_week_start_is_monday():
    assert week_start(date(2026, 9, 24)) == date(2026, 9, 21)
    assert week_start(date(2026, 9, 21)) == date(2026, 9, 21)


def test_filter_by_date_industry_title():
    rows = [
        row(job_no="in"),
        row(job_no="old", first_seen="2026-09-10T08:00:00+08:00"),
        row(job_no="fin", summary_overrides={"coIndustry": 1004001}),
        row(job_no="ai", title="AI工程師"),
    ]
    got = apply_filter(rows, _filter(industries=["半導體/電子"], title_groups=["資料工程"]), {})
    assert [r.job_no for r in got] == ["in"]


def test_filter_gate_and_english():
    rows = [
        row(job_no="ok"),
        row(job_no="small", summary_overrides={"employeeCount": 10}),
        row(job_no="eng", summary_overrides={"languageRequirements": [{"language": 1}]}),
    ]
    assert {r.job_no for r in apply_filter(rows, _filter(gate="通過第 1 道"), {})} == {"ok", "eng"}
    assert [r.job_no for r in apply_filter(rows, _filter(gate="卡在第 1 道"), {})] == ["small"]
    assert "eng" not in {r.job_no for r in apply_filter(rows, _filter(no_english_only=True), {})}


def test_hide_skipped_uses_latest_decision():
    rows = [row(job_no="a"), row(job_no="b")]
    latest = {"a": decision("a", "skip"), "b": decision("b", "apply")}
    assert [r.job_no for r in apply_filter(rows, _filter(), latest)] == ["b"]
    assert len(apply_filter(rows, _filter(hide_skipped=False), latest)) == 2


def test_sort_puts_missing_last():
    rows = [row(job_no="none", score=None), row(job_no="lo", score=60), row(job_no="hi", score=90)]
    assert [r.job_no for r in sort_rows(rows, "深評分數")] == ["hi", "lo", "none"]


# ── 匯出 ──
def test_picks_are_latest_apply_in_range():
    rows = [
        row(job_no="a"),
        row(job_no="b"),
        row(job_no="old", first_seen="2026-09-01T08:00:00+08:00"),
    ]
    latest = {
        "a": decision("a", "apply"),
        "b": decision("b", "pending"),
        "old": decision("old", "apply"),
    }
    got = picks(rows, latest, date(2026, 9, 21), date(2026, 9, 27))
    assert [r.job_no for r in got] == ["a"]


def test_markdown_escapes_pipe_in_title():
    r = row(job_no="a", title="資料工程師 | 金融")
    md = to_markdown([r], {"a": decision("a", "apply", note="優先")})
    body = md.splitlines()[2]
    assert "資料工程師 \\| 金融" in body and "優先" in body
    assert body.count(" | ") == 9  # 10 欄 → 9 個分隔,沒被職稱切歪


def test_csv_has_bom_for_excel():
    data = to_csv([row(job_no="a")], {})
    assert data.startswith("﻿".encode())
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed[0][0] == "公司" and parsed[1][0] == "台積電"


def test_record_fixture_uses_real_field_names():
    """防呆:假資料若用錯欄位名,上面的測試會空轉通過。"""
    s = json.loads(record()["raw_summary"])
    assert {"employeeCount", "coIndustry", "period", "s10", "languageRequirements"} <= s.keys()


# ── 104 原文排版 ──
def test_detail_sections_follow_104_layout():
    from jobfinder.webui.jd_view import detail_sections

    payload = detail(jd="負責 ETL")["data"]
    payload["condition"] = {
        "acceptRole": {"role": [{"code": 2, "description": "應屆畢業生"}]},
        "workExp": "1年以上",
        "edu": "大學以上",
        "major": ["資訊工程相關", "統計學相關"],
        "language": [
            {"language": "英文", "ability": {"listening": "中等", "speaking": "略懂"}},
        ],
        "specialty": [{"description": "Python"}, {"description": "SQL"}],
        "skill": [],
        "certificate": [{"name": "TQC"}],
        "other": "熟悉 Airflow 者佳\n",
    }
    work, conds = detail_sections(payload)
    assert work == "負責 ETL"
    assert conds == [
        ("接受身份", "應屆畢業生"),
        ("工作經歷", "1年以上"),
        ("學歷要求", "大學以上"),
        ("科系要求", "資訊工程相關、統計學相關"),
        ("語文條件", "英文 -- 聽 /中等、說 /略懂"),
        ("擅長工具", "Python、SQL"),
        ("具備證照", "TQC"),
        ("其他條件", "熟悉 Airflow 者佳"),
    ]  # 空的欄位(工作技能)不顯示


def test_row_carries_original_jd_only_with_detail():
    assert row().jd_description == "" and row().jd_conditions == []
    assert row(detail_payload=detail(jd="負責 ETL")).jd_description == "負責 ETL"
