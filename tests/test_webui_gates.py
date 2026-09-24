"""三道閘門的規則(docs/webui-spec.md §二)。每條規則都要有「擋」與「不擋」兩面。"""

from __future__ import annotations

import pytest
from webui_helpers import detail, row

from jobfinder.webui.gates import Light, Training


def fails(r) -> str:
    return " ".join(r.gates.fails)


def flags(r) -> str:
    return " ".join(r.gates.flags)


def test_clean_job_passes_gate1():
    r = row()
    assert r.gates.gate1 is Light.PASS
    assert r.gates.stuck_at is None
    assert r.gates.passes_through(1)


# ── 公司人數 ──
def test_small_company_excluded():
    r = row(summary_overrides={"employeeCount": 29})
    assert r.gates.gate1 is Light.FAIL and "<30" in fails(r)
    assert r.gates.stuck_at == 1


def test_thirty_employees_is_enough():
    assert row(summary_overrides={"employeeCount": 30}).gates.gate1 is Light.PASS


@pytest.mark.parametrize("count", [None, 0])
def test_missing_employee_count_is_yellow_not_excluded(count):
    r = row(summary_overrides={"employeeCount": count})
    assert r.employees is None
    assert r.gates.gate1 is Light.WARN
    assert "員工數未提供" in flags(r)


# ── 地點 ──
@pytest.mark.parametrize("area", ["高雄市前鎮區", "台南市東區", "臺南市永康區", "彰化縣彰化市"])
def test_south_of_taichung_excluded(area):
    assert row(area=area).gates.gate1 is Light.FAIL


@pytest.mark.parametrize("area", ["台北市內湖區", "新竹縣竹北市", "台中市西屯區", "苗栗縣竹南鎮"])
def test_taichung_and_north_allowed(area):
    assert row(area=area).gates.gate1 is Light.PASS


# ── 輪班:有全文看全文,沒全文看摘要 ──
def test_shift_work_in_detail_excluded():
    r = row(detail_payload=detail(jd="需配合輪班,含夜班"))
    assert "輪班" in fails(r)


def test_on_call_in_summary_excluded_when_no_detail():
    r = row(summary_overrides={"description": "需 on-call 支援系統"})
    assert "輪班" in fails(r)


# ── 派遣 ──
def test_dispatch_company_excluded():
    assert "派遣" in fails(row(company="某某人力派遣股份有限公司"))


def test_dispatch_industry_excluded():
    r = row(summary_overrides={"coIndustryDesc": "人力仲介代徵"})
    assert "派遣" in fails(r)


# ── 職稱 ──
@pytest.mark.parametrize("title", ["資料工程實習生", "Data Intern", "工讀生-資料整理", "時薪人員"])
def test_intern_titles_excluded(title):
    assert "實習" in fails(row(title=title))


@pytest.mark.parametrize(
    "title", ["2027 校園徵才-資料工程師", "預聘資料工程師", "研發替代役-資料工程"]
)
def test_campus_titles_excluded(title):
    assert "校園徵才" in fails(row(title=title))


def test_campus_title_with_autumn_is_kept():
    assert "校園徵才" not in fails(row(title="秋季校園徵才-資料工程師"))


@pytest.mark.parametrize("title", ["AI工程師", "AI 工程師", "人工智慧工程師", "演算法工程師"])
def test_ai_titles_flagged_red_but_not_excluded(title):
    r = row(title=title)
    assert "AI職稱" in flags(r)
    assert r.gates.gate1 is Light.WARN
    assert r.gates.passes_through(1)


def test_ai_data_title_not_flagged():
    """寬鬆比對會誤傷這一類 —— 規格裡評 ~75% 命中的華碩「AI 數據開發工程師」。"""
    assert "AI職稱" not in flags(row(title="AI 數據開發工程師"))


# ── 薪資(換算月薪後比)──
def test_salary_ceiling_below_floor_excluded():
    r = row(summary_overrides={"s10": 50, "salaryLow": 35000, "salaryHigh": 45000})
    assert "薪資上限" in fails(r)


def test_open_ended_low_salary_is_yellow():
    r = row(summary_overrides={"s10": 50, "salaryLow": 40000, "salaryHigh": 9999999})
    assert r.salary_open_ended and r.salary_high_monthly is None
    assert "談薪不可退" in flags(r)
    assert r.gates.passes_through(1)


def test_annual_salary_converted_before_comparison():
    """年薪 720,000 = 月 60,000,不能拿 720,000 或 72 萬跟 5 萬比出錯的結論。"""
    r = row(summary_overrides={"s10": 60, "salaryLow": 600000, "salaryHigh": 720000})
    assert r.salary_high_monthly == 60000
    assert "薪資" not in fails(r)


def test_negotiable_salary_not_excluded():
    r = row(summary_overrides={"s10": 10, "salaryLow": 0, "salaryHigh": 0})
    assert r.salary_text == "待遇面議"
    assert r.gates.gate1 is Light.PASS


# ── 經歷 ──
# 104 的 period = 年資 + 1:0 不拘、2 = 1年以上、3 = 2年以上、4 = 3年以上
@pytest.mark.parametrize("period", [0, 2])
def test_up_to_one_year_allowed(period):
    assert row(summary_overrides={"period": period}).gates.gate1 is Light.PASS


def test_period_two_is_one_year_not_two():
    """使用者回報:欣興在 104 上寫「1年以上」,UI 卻顯示 2 年以上(period=2)。"""
    assert row(summary_overrides={"period": 2}).experience == "1年以上"


def test_two_years_is_yellow_not_excluded():
    """2026-09-24 使用者定案(以真實年資計):1 年過、2 年標黃、3 年以上排除。"""
    r = row(summary_overrides={"period": 3})
    assert "經歷 2 年" in flags(r) and not r.gates.fails
    assert r.gates.gate1 is Light.WARN and r.gates.passes_through(1)


@pytest.mark.parametrize(("period", "years"), [(4, 3), (7, 6)])
def test_three_years_or_more_excluded(period, years):
    assert f"經歷 {years} 年" in fails(row(summary_overrides={"period": period}))


# ── Platform/DataOps 樣態 ──
def test_platform_keywords_flagged():
    r = row(detail_payload=detail(jd="維運 K8s 叢集,使用 Terraform 與 Helm"))
    assert "Platform/DataOps" in flags(r)


# ── 第 2 道 ──
def test_training_regex_found():
    r = row(detail_payload=detail(jd="完善的新人培訓與導師制度"))
    assert r.gates.training is Training.FOUND


def test_training_not_found_with_detail():
    assert row(detail_payload=detail(jd="負責 ETL")).gates.training is Training.NOT_FOUND


def test_training_unknown_without_detail():
    assert row().gates.training is Training.UNKNOWN


def test_chore_category_flagged():
    r = row(detail_payload=detail(categories=("數位行銷企劃",)))
    assert "行銷/行政/業務" in flags(r)


def test_undetermined_gates_do_not_block():
    """第 2、3 道要 LLM,還沒跑 → 未判定。未判定不能算卡住,否則全部職缺都卡在第 2 道。"""
    r = row()
    assert r.gates.gate2 is Light.UNKNOWN and r.gates.gate3 is Light.UNKNOWN
    assert r.gates.passes_through(3)
