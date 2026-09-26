"""科系要求:程式端比對,附加成命中率的一條必備(不標核心)。"""

from __future__ import annotations

import pytest
from webui_helpers import detail, record

from jobfinder.hitrate.compute import Requirement
from jobfinder.hitrate.store import StoredHitRate
from jobfinder.webui.candidates import build_row
from jobfinder.webui.major import major_requirement


@pytest.mark.parametrize(
    ("major", "match"),
    [
        ("資訊管理相關", "yes"),
        ("統計學相關", "yes"),
        ("數理統計相關", "yes"),
        ("數學及電算機科學學科類", "yes"),
        ("資訊工程相關", "partial"),
        ("電機電子工程相關", "partial"),
        ("機械工程相關", "no"),
        ("醫藥工程相關", "no"),
    ],
)
def test_single_major(major, match):
    assert major_requirement([major]).match == match


def test_multiple_majors_take_best_since_any_one_qualifies():
    req = major_requirement(["機械工程相關", "資訊工程相關", "資訊管理相關"])
    assert req.match == "yes" and req.kind == "required" and req.core is False
    assert "機械工程相關" in req.item


def test_no_major_requirement_adds_nothing():
    assert major_requirement([]) is None
    assert major_requirement(["", "  "]) is None


def _row_with(majors, llm_reqs):
    payload = detail()
    payload["data"]["condition"]["major"] = majors
    hit = StoredHitRate("1", None, 0, 0, 0, llm_reqs, "m", "t")
    return build_row(record(detail_payload=payload), hit)


def test_major_is_appended_and_rate_recomputed():
    sql = Requirement(item="SQL", kind="required", match="yes", evidence="x")
    row = _row_with(["機械工程相關"], [sql])
    assert [r.item for r in row.hit_requirements] == ["SQL", "科系:機械工程相關"]
    assert row.hit_rate == 0.5  # 1 符合 + 1 不符


def test_major_counts_even_when_llm_gave_twelve_items():
    """LLM 最多 12 條;科系要附加在截斷之後,不能被擠掉。"""
    reqs = [
        Requirement(item=f"S{i}", kind="required", match="yes", evidence="x") for i in range(12)
    ]
    row = _row_with(["機械工程相關"], reqs)
    assert len(row.hit_requirements) == 13 and row.hit_rate == pytest.approx(12 / 13)


def test_no_major_leaves_rate_unchanged():
    sql = Requirement(item="SQL", kind="required", match="partial", evidence="x")
    assert _row_with([], [sql]).hit_rate == 0.5
