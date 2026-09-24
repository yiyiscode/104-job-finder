"""第 3 道閘門:必備命中率。

重點護欄:
* 百分比由程式算,加分條件不進分母
* 結果綁定履歷版本 —— 履歷一改,舊結果不能冒充成新的
* 花錢的東西要有上限:筆數、成本、連續失敗都會停
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from webui_helpers import detail, record, row

from jobfinder.cli import main
from jobfinder.config import Config
from jobfinder.hitrate.compute import (
    MAX_REQUIREMENTS,
    HitRateCheck,
    Requirement,
    hit_rate,
    resume_hash,
)
from jobfinder.hitrate.runner import pick_targets, run_hitrate
from jobfinder.hitrate.store import HitRateStore
from jobfinder.storage.db import connect, ensure_schema
from jobfinder.webui.candidates import CandidateFilter, apply_filter, build_row
from jobfinder.webui.gates import Light

NOW = datetime(2026, 9, 24, 9, 0)


def req(kind: str = "required", match: str = "yes", item: str = "SQL") -> Requirement:
    return Requirement(item=item, kind=kind, match=match, evidence="x")


# ── 計算 ──
def test_rate_counts_partial_as_half_and_ignores_preferred():
    result = hit_rate(
        [
            req(match="yes"),
            req(match="partial"),
            req(match="no"),
            req(match="no"),
            req(kind="preferred", match="no"),  # 加分條件不進分母
        ]
    )
    assert result.required == 4 and result.met == 1 and result.partial == 1
    assert result.rate == pytest.approx(1.5 / 4)


def test_no_required_items_is_none_not_zero():
    """JD 只寫「者佳」→ 沒有可辨識的必備。回 0% 會被當成「完全不符」刷掉。"""
    result = hit_rate([req(kind="preferred")])
    assert result.rate is None and result.required == 0


def test_requirements_truncated_to_max():
    reqs = [req(match="yes") for _ in range(MAX_REQUIREMENTS)] + [req(match="no")] * 5
    assert hit_rate(reqs).rate == 1.0


def test_resume_hash_ignores_surrounding_whitespace_but_not_content():
    assert resume_hash("履歷 A\n") == resume_hash("  履歷 A")
    assert resume_hash("履歷 A") != resume_hash("履歷 B")


# ── 儲存 ──
def test_store_scoped_by_resume_version(tmp_path):
    store = HitRateStore(tmp_path / "hitrate.db")
    reqs = [req(match="yes"), req(match="no", item="AWS")]
    store.save("J1", "v1", hit_rate(reqs), reqs, "m", 0.002, NOW)

    got = store.for_resume("v1")["J1"]
    assert got.rate == 0.5 and [r.item for r in got.requirements] == ["SQL", "AWS"]
    assert store.for_resume("v2") == {}  # 履歷改了 → 視為未計算


def test_store_missing_file_is_empty(tmp_path):
    store = HitRateStore(tmp_path / "hitrate.db")
    (tmp_path / "hitrate.db").unlink()
    assert store.for_resume("v1") == {}


# ── 挑職缺 ──
def test_pick_targets_needs_detail_gate1_and_not_done():
    rows = [
        row(job_no="ok-old", first_seen="2026-09-20T08:00:00+08:00", detail_payload=detail()),
        row(job_no="ok-new", first_seen="2026-09-23T08:00:00+08:00", detail_payload=detail()),
        row(job_no="no-detail"),
        row(
            job_no="gate1-fail",
            summary_overrides={"employeeCount": 5},
            detail_payload=detail(),
        ),
        row(job_no="done", detail_payload=detail()),
    ]
    targets, pending = pick_targets(rows, done={"done"}, limit=1)
    assert pending == 2
    assert [r.job_no for r in targets] == ["ok-new"]  # 由新到舊


# ── 執行 ──
class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def structured(self, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _check(*matches: str) -> tuple[HitRateCheck, float]:
    return HitRateCheck(requirements=[req(match=m) for m in matches]), 0.01


def _rows(n: int):
    return [row(job_no=f"J{i}", detail_payload=detail()) for i in range(n)]


async def _run(tmp_path, client, rows, **kw):
    store = HitRateStore(tmp_path / "hitrate.db")
    params = dict(
        rows=rows, store=store, client=client, model="m", resume_text="履歷",
        resume_hash="v1", limit=10, cost_cap_usd=1.0, now=NOW,
    )  # fmt: skip
    params.update(kw)
    return await run_hitrate(**params), store


async def test_run_saves_and_does_not_recompute(tmp_path):
    client = StubClient([_check("yes", "no"), _check("yes")])
    report, store = await _run(tmp_path, client, _rows(2))
    assert report.computed == 2 and client.calls == 2
    # 同一天 → 依 job_no 由大到小:J1 先拿到第一個回應
    assert {k: v.rate for k, v in store.for_resume("v1").items()} == {"J1": 0.5, "J0": 1.0}

    again = StubClient([])
    report2, _ = await _run(tmp_path, again, _rows(2))
    assert again.calls == 0 and report2.candidates == 0


async def test_cost_cap_stops_run(tmp_path):
    client = StubClient([_check("yes")] * 5)
    report, _ = await _run(tmp_path, client, _rows(5), cost_cap_usd=0.02)
    assert report.computed == 2 and "成本上限" in report.stopped_reason


async def test_single_failure_continues_three_in_a_row_aborts(tmp_path):
    client = StubClient([ValueError("parse"), _check("yes"), *[RuntimeError("down")] * 3])
    report, _ = await _run(tmp_path, client, _rows(6))
    assert report.computed == 1 and report.failed == 4
    assert "連續失敗 3" in report.stopped_reason
    assert client.calls == 5  # 第 6 筆沒打


async def test_dry_run_calls_nothing(tmp_path):
    client = StubClient([])
    report, store = await _run(tmp_path, client, _rows(3), dry_run=True)
    assert client.calls == 0 and len(report.rates) == 3 and store.for_resume("v1") == {}


# ── 接進第 3 道閘門 ──
def _stored(rate):
    from jobfinder.hitrate.store import StoredHitRate

    return StoredHitRate("1", rate, 1, 0, 0, [req()], "m", "t")


@pytest.mark.parametrize(
    ("rate", "light", "status"),
    [
        (0.70, Light.PASS, "已計算"),
        (0.69, Light.FAIL, "已計算"),
        (None, Light.UNKNOWN, "無明列必備"),
    ],
)
def test_gate3_from_hit_rate(rate, light, status):
    r = build_row(record(detail_payload=detail()), _stored(rate))
    assert r.gates.gate3 is light and r.hit_rate_status == status


def test_gate3_status_without_result():
    assert build_row(record()).hit_rate_status == "無全文"
    assert build_row(record(detail_payload=detail())).hit_rate_status == "未計算"
    assert build_row(record(detail_payload=detail())).gates.passes_through(3)


def test_filters_for_gate3():
    rows = [
        build_row(record("pass", detail_payload=detail()), _stored(0.8)),
        build_row(record("fail", detail_payload=detail()), _stored(0.4)),
        build_row(record("todo", detail_payload=detail())),
    ]
    base = dict(
        start=datetime(2026, 9, 21).date(), end=datetime(2026, 9, 27).date(),
        industries=["半導體/電子"], title_groups=["資料工程"],
    )  # fmt: skip
    hit = apply_filter(rows, CandidateFilter(**base, gate="命中率達標"), {})
    stuck = apply_filter(rows, CandidateFilter(**base, gate="卡在第 3 道"), {})
    assert [r.job_no for r in hit] == ["pass"]  # 未計算不算達標
    assert [r.job_no for r in stuck] == ["fail"]


# ── 設定硬上限 ──
@pytest.mark.parametrize(
    "override", [{"max_jobs_per_run": 101}, {"cost_cap_usd": 0.51}, {"cost_cap_usd": 0}]
)
def test_hitrate_config_hard_caps(config_dict, override):
    config_dict["hitrate"] = {**config_dict.get("hitrate", {}), **override}
    with pytest.raises(ValueError):
        Config.model_validate(config_dict)


# ── CLI 端到端(假 LLM,不花錢、不連網)──
def test_cli_hitrate_fake_llm_end_to_end(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    ensure_schema(conn)
    rec = record("J1", detail_payload=detail(jd="需熟悉 SQL 與 Airflow,建置 ETL"))
    conn.execute(
        "INSERT INTO jobs (job_no, job_name, cust_name, job_url, area_desc, edu_desc,"
        " first_seen_at, last_seen_at, last_new_at, status, raw_summary, raw_detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scored', ?, ?)",
        (
            "J1", rec["job_name"], rec["cust_name"], rec["job_url"], rec["area_desc"],
            rec["edu_desc"], rec["first_seen_at"], rec["first_seen_at"], rec["first_seen_at"],
            rec["raw_summary"], rec["raw_detail"],
        ),
    )  # fmt: skip
    conn.commit()
    conn.close()

    assert main(["--data-dir", str(tmp_path), "hitrate", "--fake-llm"]) == 0

    import sqlite3

    rows = (
        sqlite3.connect(tmp_path / "hitrate.db")
        .execute("SELECT job_no, model, requirements FROM hitrates")
        .fetchall()
    )
    assert len(rows) == 1 and rows[0][:2] == ("J1", "fake")
    assert {r["item"] for r in json.loads(rows[0][2])} >= {"SQL", "Airflow"}
