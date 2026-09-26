"""已標投遞頁的履歷修改建議:組合現有資料,不呼叫 API。"""

from __future__ import annotations

from webui_helpers import detail, record

from jobfinder.hitrate.compute import Requirement
from jobfinder.hitrate.store import StoredHitRate
from jobfinder.webui.candidates import PIPELINE_STATUS_HELP, build_row
from jobfinder.webui.tailoring import build_tailoring


def R(item, match, kind="required", core=False, evidence="國泰 CAP"):
    return Requirement(item=item, kind=kind, match=match, evidence=evidence, core=core)


def _row(reqs, **extra):
    rec = record(detail_payload=detail(jd="負責 ETL 與 Airflow 排程"))
    rec.update(extra)
    return build_row(rec, StoredHitRate("1", None, 0, 0, 0, reqs, "m", "t"))


def test_splits_emphasize_and_actions_with_core_first():
    row = _row(
        [
            R("SQL", "yes", evidence="技能表 SQL"),
            R("Airflow", "partial", evidence="自建排程"),
            R("AWS 資料服務", "no", core=True),
            R("Tableau", "no", kind="preferred"),
        ]
    )
    t = build_tailoring(row)
    assert t.emphasize == [("SQL", "技能表 SQL")]
    assert t.actions[0].startswith("⚠️ 核心") and "AWS 資料服務" in t.actions[0]
    assert "Airflow" in t.actions[1] and "自建排程" in t.actions[1]
    assert t.preferred_gaps == ["Tableau"]


def test_deep_score_tips_are_included():
    row = _row(
        [],
        resume_tip="把排程系統寫成資料管線",
        highlights='["有 ETL 實作"]',
        red_flags='["沒用過雲端資料服務"]',
    )
    t = build_tailoring(row)
    assert t.resume_tip == "把排程系統寫成資料管線"
    assert t.highlights == ["有 ETL 實作"] and t.red_flags == ["沒用過雲端資料服務"]


def test_empty_when_nothing_to_suggest():
    assert build_tailoring(build_row(record())).empty


def test_every_pipeline_status_is_explained():
    assert set(PIPELINE_STATUS_HELP) == {
        "new",
        "filtered_out",
        "screened_out",
        "scored",
        "notified",
    }
