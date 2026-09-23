"""產業／規模規則層的測試。

這一層決定了**哪些職缺根本不會被看到**,所以它跟 scrape/ 底下的護欄同一等級:
沒有測試等於沒有護欄。重點在三件事 ——
① 該留的留、該濾的濾;② 訊號消失時 fail-open 而不是靜默歸零;③ 設定打錯會拒絕啟動。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfinder.config import TargetingCfg, load_config
from jobfinder.errors import ConfigError
from jobfinder.models import JobSummary
from jobfinder.normalize import normalize_search_response
from jobfinder.targeting import apply_targeting

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def job(
    job_no: str, *, code: str | None = "1001006002", employees: int | None = 1200
) -> JobSummary:
    return JobSummary(
        job_no=job_no,
        detail_id=job_no,
        job_name=f"資料工程師 {job_no}",
        cust_name=f"公司 {job_no}",
        job_url=f"https://www.104.com.tw/job/{job_no}",
        industry_code=code,
        employee_count=employees,
    )


CFG = TargetingCfg(enabled=True, industry_prefixes=["1001006", "1004"], min_employee_count=500)


# ── 基本判定 ──────────────────────────────────────────────────────────


def test_keeps_target_industry_and_size():
    out = apply_targeting([job("1")], CFG)
    assert [j.job_no for j in out.keep] == ["1"]
    assert out.dropped_count == 0


def test_prefix_covers_whole_industry_group():
    """``1001006`` 要同時涵蓋 IC設計(...001)、半導體製造(...002)、其他半導體(...003)。"""
    jobs = [
        job("ic", code="1001006001"),
        job("fab", code="1001006002"),
        job("etc", code="1001006003"),
    ]
    assert len(apply_targeting(jobs, CFG).keep) == 3


def test_drops_non_target_industry():
    out = apply_targeting([job("soft", code="1001001002")], CFG)  # 電腦軟體服務業
    assert out.keep == []
    assert out.dropped_by_industry == 1
    assert out.dropped_by_size == 0


def test_drops_staffing_agency():
    """人力仲介代徵不在白名單裡,自然被濾掉 —— 不需要另外維護排除清單。"""
    out = apply_targeting([job("agency", code="1009001001", employees=9000)], CFG)
    assert out.keep == []
    assert out.dropped_by_industry == 1


def test_drops_small_company_in_target_industry():
    out = apply_targeting([job("small", employees=24)], CFG)
    assert out.keep == []
    assert out.dropped_by_size == 1
    assert out.dropped_by_industry == 0


def test_industry_checked_before_size():
    """產業不符時不該再算進「規模不足」—— 兩個計數器相加要等於被丟掉的筆數。"""
    out = apply_targeting([job("x", code="1001001002", employees=10)], CFG)
    assert out.dropped_by_industry + out.dropped_by_size == out.dropped_count == 1


def test_single_missing_employee_count_is_dropped():
    """整批只有零星幾筆缺值 = 那家公司沒填,不是訊號消失,照樣濾掉。"""
    jobs = [job(str(i)) for i in range(9)] + [job("nofill", employees=None)]
    out = apply_targeting(jobs, CFG)
    assert "nofill" not in [j.job_no for j in out.keep]
    assert out.dropped_by_size == 1
    assert not out.warnings


def test_disabled_keeps_everything():
    jobs = [job("a", code="1001001002", employees=3)]
    out = apply_targeting(jobs, TargetingCfg(enabled=False))
    assert out.keep == jobs
    assert out.applied == []


def test_empty_config_filters_nothing():
    """空前綴 + 門檻 0 是「不過濾」,不是「全部濾掉」。"""
    jobs = [job("a", code="1001001002", employees=3)]
    out = apply_targeting(jobs, TargetingCfg())
    assert out.keep == jobs


# ── fail-open:訊號消失時不可以靜默歸零 ────────────────────────────────


def test_fail_open_when_industry_code_disappears():
    """104 不再給 coIndustry 時,停用產業過濾並告警 —— 不是把日報變成 0 則。"""
    jobs = [job(str(i), code=None) for i in range(10)]
    out = apply_targeting(jobs, CFG)
    assert len(out.keep) == 10  # 規模仍符合,所以全留
    assert out.dropped_by_industry == 0
    assert any("coIndustry" in w for w in out.warnings)


def test_fail_open_when_employee_count_disappears():
    jobs = [job(str(i), employees=None) for i in range(10)]
    out = apply_targeting(jobs, CFG)
    assert len(out.keep) == 10
    assert out.dropped_by_size == 0
    assert any("employeeCount" in w for w in out.warnings)


def test_fail_open_only_past_half_missing():
    """缺一半以內仍照常過濾 —— 門檻要真的是門檻,不是一有缺值就放棄。"""
    jobs = [job(str(i)) for i in range(6)] + [job(f"n{i}", code=None) for i in range(4)]
    out = apply_targeting(jobs, CFG)
    assert out.dropped_by_industry == 4
    assert not out.warnings


# ── 設定驗證 ──────────────────────────────────────────────────────────


def test_chinese_industry_name_is_rejected():
    """填中文名稱不會報錯、只會默默濾光所有職缺 —— 所以 config 直接拒絕啟動。"""
    with pytest.raises(ValidationError, match="代碼"):
        TargetingCfg(industry_prefixes=["半導體製造業"])


def test_negative_employee_count_is_rejected():
    with pytest.raises(ValidationError):
        TargetingCfg(min_employee_count=-1)


def test_real_config_targets_semiconductor_and_finance(config_dict, write_config):
    """專案實際在用的設定:半導體(1001006)與金融(1004)都要在,門檻是大公司等級。"""
    cfg = load_config(write_config(config_dict))
    assert cfg.targeting.enabled
    assert "1001006" in cfg.targeting.industry_prefixes
    assert "1004" in cfg.targeting.industry_prefixes
    assert cfg.targeting.min_employee_count >= 500


def test_real_config_has_no_si_keywords(config_dict, write_config):
    """SI／接案導向的關鍵字與「聚焦半導體／金融大公司」互相抵消,不該回來。"""
    cfg = load_config(write_config(config_dict))
    assert "系統整合工程師" not in cfg.search.keywords
    assert "AI工程師" not in cfg.search.keywords


def test_bad_prefix_in_config_file_is_rejected(config_dict, write_config):
    config_dict["targeting"]["industry_prefixes"] = ["半導體製造業"]
    with pytest.raises(ConfigError):
        load_config(write_config(config_dict))


# ── 對真實 fixture 跑一次 ─────────────────────────────────────────────


def test_against_real_fixture():
    """真實回應裡確實帶得出 coIndustry / employeeCount,過濾後也確實變少。"""
    payload = json.loads((FIXTURE_DIR / "search_page1.json").read_text(encoding="utf-8"))
    jobs = normalize_search_response(payload, "資料工程師").jobs
    assert jobs, "fixture 應該有職缺"
    assert all(j.industry_code for j in jobs), "真實回應每筆都該有 coIndustry"

    out = apply_targeting(jobs, CFG)
    assert len(out.keep) < len(jobs), "這批 fixture 不可能全部都是半導體／金融大公司"
    for kept in out.keep:
        assert kept.industry_code is not None
        assert kept.industry_code.startswith(("1001006", "1004"))
        assert (kept.employee_count or 0) >= 500
