"""LLM 回應的簡轉繁(zh.py)。"""

from __future__ import annotations

import pytest

from jobfinder.hitrate.compute import HitRateCheck
from jobfinder.scoring.schemas import DeepScore, ScreenResult
from jobfinder.zh import has_simplified, to_traditional


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("求职者虽有金融实习经验", "求職者雖有金融實習經驗"),
        ("熟悉台积电的后端系统", "熟悉台積電的後端系統"),  # 轉完「台」不能變「臺」
        ("需要了解数据仓储", "需要了解數據倉儲"),  # 「了解」不能變「瞭解」
    ],
)
def test_simplified_is_converted(raw, expected):
    assert to_traditional(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "台積電的資料工程師,需要了解 SQL 與 ETL",
        "里程碑式的專案,皇后區",  # 「里」「后」兩邊都是正常用字
        "富采光電的實際雇用關係",  # 公司名「富采」、「雇用」不能被改成「富採」「僱用」
        "Python / Airflow / dbt",
        "",
    ],
)
def test_traditional_text_is_left_untouched(text):
    """s2tw 套在繁體上會把「台」改成「臺」—— 已經是繁體的必須原樣回傳。"""
    assert not has_simplified(text)
    assert to_traditional(text) == text


def test_deep_score_fields_are_converted():
    score = DeepScore.model_validate(
        {
            "tech_fit": 20,
            "exp_fit": 10,
            "domain_fit": 10,
            "growth_fit": 10,
            "practical_fit": 5,
            "total_score": 55,
            "verdict": "maybe",
            "one_liner": "职缺偏向数据架构",
            "highlights": ["金融实习"],
            "red_flags": ["要求两年经验"],
            "resume_tip": "强调自建的ETL管线",
        }
    )
    assert score.one_liner == "職缺偏向數據架構"
    assert score.highlights == ["金融實習"]
    assert score.red_flags == ["要求兩年經驗"]
    assert score.resume_tip == "強調自建的ETL管線"


def test_screen_reason_is_converted():
    out = ScreenResult.model_validate(
        {"results": [{"job_no": "1", "keep": True, "rough": 70, "reason": "资料工程对口"}]}
    )
    assert out.results[0].reason == "資料工程對口"


def test_hitrate_text_is_converted():
    out = HitRateCheck.model_validate(
        {
            "requirements": [
                {"item": "熟悉云端数据库", "kind": "required", "match": "yes", "evidence": "有实作"}
            ]
        }
    )
    assert out.requirements[0].item == "熟悉雲端數據庫"
    assert out.requirements[0].evidence == "有實作"


def test_schema_stays_a_plain_string():
    """strict mode 吃的 JSON schema 不能因為加了轉換而變形。"""
    props = DeepScore.model_json_schema()["properties"]
    assert props["one_liner"] == {"title": "One Liner", "type": "string"}
