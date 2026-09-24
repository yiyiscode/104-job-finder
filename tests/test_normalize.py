"""normalize 層測試。

fixture 是 **2026-08-21 從 104 錄下的真實回應**,不是手工建的樣本。
104 改版時第一個壞掉的就是這裡,所以覆蓋率要求最高:重點不只是「正常資料能解析」,
而是**殘缺與變形的資料不會讓整輪掛掉**(見 SPEC.md 風險 2)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobfinder.normalize import (
    format_education,
    format_experience,
    format_salary,
    period_to_years,
    merge_summaries,
    monthly_equivalent,
    normalize_detail_response,
    normalize_search_response,
    pick,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def search_page():
    return normalize_search_response(load_fixture("search_page1.json"), "AI工程師")


# ─── pick ──────────────────────────────────────────────────────────


def test_pick_walks_nested_paths():
    data = {"data": {"jobDetail": {"jobDescription": "hi"}}}
    assert pick(data, "data.jobDetail.jobDescription") == "hi"


def test_pick_returns_default_for_missing_or_wrong_shape():
    assert pick({"data": None}, "data.list", []) == []
    assert pick({"data": "a string"}, "data.list", []) == []
    assert pick({}, "totally.made.up", "fallback") == "fallback"


# ─── 真實搜尋回應 ───────────────────────────────────────────────────


def test_real_search_response_parses_without_drift(search_page):
    """對真實資料應該零 drift —— 有 drift 就代表欄位對應又跟現實脫節了。"""
    assert search_page.drift == []
    assert len(search_page.jobs) == 32


def test_totals_come_from_metadata_pagination(search_page):
    """`data` 本身就是陣列,總數在 metadata.pagination —— 不是 data.totalCount。"""
    assert search_page.total_count == 602
    assert search_page.total_page == 21


def test_job_no_and_detail_id_are_different_things(search_page):
    """這兩個搞混會讓詳細頁全部 404(實測:拿 jobNo 打詳細頁回「職務不存在」)。"""
    job = search_page.jobs[0]
    assert job.job_no == "15305872"
    assert job.detail_id == "94234"
    assert job.job_no != job.detail_id
    assert job.job_url.endswith("/job/94234")

    # 整頁沒有任何一筆是相同的,代表這不是巧合
    assert all(j.job_no != j.detail_id for j in search_page.jobs)


def test_period_is_years_plus_one(search_page):
    """``period`` = 年資 + 1(0 = 不拘)。2026-09-24 對 369 筆真實詳細頁的 ``workExp`` 驗證:
    period 0 → 不拘(266)、2 → 1年以上(59)、3 → 2年以上(41),各只有 1 筆例外(雇主改過條件)。

    原本以為 period 就是年數,結果全站多算 1 年 —— 使用者在 104 上看到「1年以上」,
    卡片與 UI 卻寫「2年以上」。送 jobexp=10(5~10年)回傳 6~9,也符合 +1。
    """
    exp10 = normalize_search_response(load_fixture("search_exp10.json"), "工程師")
    junior = {j.min_years for j in search_page.jobs}
    senior = {j.min_years for j in exp10.jobs}

    assert junior <= {0, 1, 2}
    assert min(senior) >= 5, f"5~10年的搜尋不該出現低年資:{sorted(senior)}"
    assert max(senior) <= 10


@pytest.mark.parametrize(("period", "years"), [(None, None), (0, 0), (1, 0), (2, 1), (3, 2), (9, 8)])
def test_period_to_years(period, years):
    assert period_to_years(period) == years


def test_experience_is_rendered_as_readable_text(search_page):
    by_no = {j.job_no: j for j in search_page.jobs}
    assert by_no["15305872"].period_desc == "經歷不拘"  # period=0
    assert by_no["11582950"].period_desc == "2年以上"  # period=3


def test_education_codes_map_to_the_text_104_itself_uses(search_page):
    """已用詳細頁交叉驗證:[4]→大學、[5]→碩士、[3,4,5,6]→專科以上。"""
    by_no = {j.job_no: j for j in search_page.jobs}
    assert by_no["15305872"].edu_codes == [4]
    assert by_no["15305872"].edu_desc == "大學"


def test_salary_string_is_built_because_the_list_has_none(search_page):
    """104 的搜尋列表沒有 salaryDesc,只有上下限。"""
    by_no = {j.job_no: j for j in search_page.jobs}
    assert by_no["15305872"].salary_desc == "月薪 45,000~55,000 元"
    # 上下限都是 0 代表面議,不是月薪 0 元
    negotiable = next(j for j in search_page.jobs if j.salary_low is None)
    assert negotiable.salary_desc == "待遇面議"


def test_tags_drop_internal_codes(search_page):
    """tags 是 dict,desc 為空時退回 param 會撈出 wf1/wf7 這種內部代碼 —— 那是噪音。"""
    all_tags = {t for j in search_page.jobs for t in j.tags}
    assert not any(t.startswith("wf") and t[2:].isdigit() for t in all_tags)


def test_extra_signals_are_captured(search_page):
    job = search_page.jobs[0]
    assert job.industry == "其他專業／科學及技術業"
    assert job.employee_count == 24
    assert job.hr_response_pr is not None and 0 <= job.hr_response_pr <= 1


def test_matched_keyword_is_recorded(search_page):
    assert all(j.matched_keywords == ["AI工程師"] for j in search_page.jobs)


def test_raw_json_is_retained_for_replay(search_page):
    """保留 raw 是刻意的:--replay 靠它重跑評分而不重爬(這是防封鎖的一環)。"""
    job = search_page.jobs[0]
    assert job.raw["jobName"] == job.job_name


# ─── 格式化純函式 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("low", "high", "stype", "expected"),
    [
        (45000, 55000, 50, "月薪 45,000~55,000 元"),
        (567000, 675000, 60, "年薪 567,000~675,000 元"),
        (200, None, 20, "時薪 200 元"),
        (None, None, 10, "待遇面議"),
        (0, 0, 10, "待遇面議"),
        (60000, 60000, 50, "月薪 60,000 元"),
        (60000, None, None, "待遇 60,000 元"),
        # 104 用 9,999,999 代表「以上」型沒有上限(真實資料 190 筆),
        # 不處理就會印出「月薪 39,000~9,999,999 元」
        (39000, 9999999, 50, "月薪 39,000 元以上"),
    ],
)
def test_format_salary_respects_the_salary_type(low, high, stype, expected):
    """不看類型就會把年薪 567,000 寫成「月薪 56 萬」—— 這是會誤導人的錯誤。"""
    assert format_salary(low, high, stype) == expected


@pytest.mark.parametrize(
    ("low", "stype", "expected"),
    [
        (45000, 50, 45000),
        (567000, 60, 47250),  # 年薪換算成月薪才能公平比較
        (200, 20, 35200),
        (0, 10, None),  # 面議是「不知道」,不是「零」
        (50000, None, None),
    ],
)
def test_monthly_equivalent(low, stype, expected):
    assert monthly_equivalent(low, stype) == expected


def test_annual_salary_is_not_mistaken_for_a_high_monthly_one(search_page):
    """真實資料裡就有兩筆年薪職缺,是這個 bug 的實際來源。"""
    annual = [j for j in search_page.jobs if j.salary_type == 60]
    assert annual, "fixture 裡應該有年薪職缺"
    for job in annual:
        assert job.salary_desc.startswith("年薪")
        assert job.monthly_low and job.monthly_low < 100_000


@pytest.mark.parametrize(
    ("years", "expected"),
    [(0, "經歷不拘"), (1, "1年以上"), (8, "8年以上"), (None, "未提供")],
)
def test_format_experience(years, expected):
    assert format_experience(years) == expected


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ([4], "大學"),
        ([5], "碩士"),
        ([3, 4, 5, 6], "專科以上"),
        ([4, 5], "大學以上"),
        ([], "學歷不拘"),
        ([99], "學歷不拘"),
    ],
)
def test_format_education(codes, expected):
    assert format_education(codes) == expected


# ─── 容錯 ───────────────────────────────────────────────────────────


def test_unknown_extra_fields_do_not_break_parsing():
    """104 加欄位不該打掛我們。"""
    payload = {
        "data": [
            {
                "jobNo": "999",
                "jobName": "AI工程師",
                "custName": "某公司",
                "link": {"job": "https://www.104.com.tw/job/zzz"},
                "brandNewFieldAddedBy104": {"deeply": {"nested": True}},
            }
        ]
    }
    page = normalize_search_response(payload, "AI工程師")
    assert len(page.jobs) == 1
    assert page.drift == []
    assert page.jobs[0].detail_id == "zzz"


def test_missing_job_no_falls_back_to_detail_id():
    payload = {
        "data": [
            {
                "jobName": "AI工程師",
                "custName": "某公司",
                "link": {"job": "https://www.104.com.tw/job/recovered1"},
            }
        ]
    }
    page = normalize_search_response(payload, "kw")
    assert page.jobs[0].job_no == "recovered1"
    assert page.jobs[0].detail_id == "recovered1"


def test_row_without_any_id_is_skipped_with_drift():
    page = normalize_search_response({"data": [{"jobName": "無主職缺"}]}, "kw")
    assert page.jobs == []
    assert page.drift


def test_missing_names_are_recorded_but_do_not_drop_the_row():
    payload = {"data": [{"jobNo": "x1", "link": {"job": "https://www.104.com.tw/job/x1"}}]}
    page = normalize_search_response(payload, "kw")
    assert len(page.jobs) == 1
    assert page.drift, "缺 jobName/custName 應記 drift"


def test_structural_change_reports_drift_instead_of_crashing():
    """`data` 不再是陣列 = 104 改版了。要告警,不是 traceback。"""
    page = normalize_search_response({"data": {"list": []}}, "kw")
    assert page.jobs == []
    assert any("改版" in d for d in page.drift)


def test_empty_payload_is_survivable():
    page = normalize_search_response({}, "kw")
    assert page.jobs == []
    assert page.total_count == 0


def test_content_hash_changes_only_on_material_change(search_page):
    """雇主只刷新上架日期時,內容雜湊不該變(去重要靠這個分辨)。"""
    job = search_page.jobs[0]
    original = job.content_hash()
    assert job.model_copy(update={"appear_date": "20260822"}).content_hash() == original
    assert job.model_copy(update={"salary_desc": "月薪 70,000 元"}).content_hash() != original


def test_screen_block_is_compact(search_page):
    """粗篩是批次的,每筆 token 都要省。"""
    block = search_page.jobs[0].to_screen_block()
    assert block.count("\n") == 3
    assert "15305872" in block
    assert len(block) < 400


def test_merge_summaries_dedupes_and_unions_keywords(search_page):
    a = search_page.jobs[0]
    b = a.model_copy(update={"matched_keywords": ["MLOps"]})
    merged = merge_summaries([a, b, search_page.jobs[1]])
    assert len(merged) == 2
    assert merged[0].matched_keywords == ["AI工程師", "MLOps"]


# ─── 真實詳細頁 ─────────────────────────────────────────────────────


def test_real_detail_parses_all_key_fields():
    drift: list[str] = []
    detail = normalize_detail_response(load_fixture("detail_94234.json"), "15305872", drift)
    assert drift == []
    assert detail.job_description and "AI" in detail.job_description
    # 詳細頁的年資與學歷已經是文字,不用查代碼表
    assert detail.work_exp == "不拘"
    assert detail.edu == "大學"
    assert detail.salary == "月薪45,000~55,000元"
    assert detail.salary_min == 45000
    assert detail.industry == "其他專業／科學及技術業"
    assert detail.address == "台北市中山區"
    # jobCategory 是 {code, description} 的陣列
    assert "全端工程師" in detail.job_category


@pytest.mark.parametrize(
    ("fixture", "expected_edu"),
    [("detail_94xn5.json", "專科以上"), ("detail_8yp7c.json", "碩士")],
)
def test_detail_edu_text_matches_the_list_codes(fixture, expected_edu):
    assert normalize_detail_response(load_fixture(fixture), "x").edu == expected_edu


def test_detail_api_error_is_recorded_not_raised():
    """拿 jobNo 打詳細頁會回 `{"error": {...}}` —— 要記 drift,不是 crash。"""
    drift: list[str] = []
    detail = normalize_detail_response(load_fixture("detail_404_error.json"), "15305872", drift)
    assert detail.job_no == "15305872"
    assert detail.job_description is None
    assert any("職務不存在" in d for d in drift)


def test_detail_missing_jobdetail_records_drift_but_returns_object():
    drift: list[str] = []
    detail = normalize_detail_response({"data": {"condition": {"workExp": "3年以上"}}}, "x", drift)
    assert detail.job_description is None
    assert len(drift) >= 1
    # 即使 jobDetail 缺失,condition 仍要解析出來供年資 hard rule 使用
    assert detail.work_exp == "3年以上"


def test_detail_of_empty_payload_does_not_crash():
    drift: list[str] = []
    detail = normalize_detail_response({}, "nothing", drift)
    assert detail.job_no == "nothing"
    assert detail.specialty == []
    assert drift
