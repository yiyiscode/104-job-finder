"""候選清單:jobs LEFT JOIN 最新一筆深評 → ``JobRow`` → 篩選/排序。

母體是**全部職缺**,不只 pipeline 放行的 —— 規格的硬門檻(≥30 人、不限產業)比
pipeline 規則層(≥500 人 + 半導體/金融)寬,只看放行的會讓軟體業永遠看不到。
pipeline 的判定另外顯示在 ``pipeline_status``。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from ..normalize import (
    OPEN_ENDED_SALARY,
    format_experience,
    format_salary,
    monthly_equivalent,
    period_to_years,
)
from . import gates
from .decisions import Decision
from .groups import industry_group, title_group, title_matches
from .jd_view import detail_sections
from .rows import JobRow
from .skill_match import skill_match
from .skills import detail_skill_text, summary_skill_text

if TYPE_CHECKING:
    from ..hitrate.store import StoredHitRate

TAIPEI = ZoneInfo("Asia/Taipei")
ENGLISH_CODE = 1  # languageRequirements[].language;已對照詳細頁的「英文」驗證
_ENGLISH_TEXT = ("英文", "english", "toeic", "多益")

CANDIDATE_SQL = """
SELECT j.job_no, j.job_name, j.cust_name, j.job_url, j.area_desc, j.edu_desc,
       j.first_seen_at, j.status, j.raw_summary, j.raw_detail,
       s.total_score, s.one_liner
FROM jobs j
LEFT JOIN (
    SELECT job_no, total_score, one_liner,
           ROW_NUMBER() OVER (PARTITION BY job_no ORDER BY id DESC) AS rn
    FROM scores WHERE stage = 'deep'
) s ON s.job_no = j.job_no AND s.rn = 1
"""


def load_rows(
    conn: sqlite3.Connection,
    hit_rates: Mapping[str, StoredHitRate] | None = None,
    have_skills: frozenset[str] | None = None,
) -> list[JobRow]:
    """``hit_rates`` 是 hitrate.db 裡「目前這版履歷」的結果;不給就全部視為未計算。
    ``have_skills`` 是履歷的技能集合(程式版命中率用);不給就不算。"""
    hit_rates = hit_rates or {}
    return [
        build_row(dict(r), hit_rates.get(r["job_no"]), have_skills)
        for r in conn.execute(CANDIDATE_SQL)
    ]


def build_row(
    record: Mapping[str, Any],
    hit: StoredHitRate | None = None,
    have_skills: frozenset[str] | None = None,
) -> JobRow:
    summary = _loads(record.get("raw_summary")) or {}
    detail_payload = _loads(record.get("raw_detail"))
    detail = (
        detail_payload.get("data", detail_payload) if isinstance(detail_payload, dict) else None
    )

    s10 = summary.get("s10")
    low = summary.get("salaryLow") or None
    high = summary.get("salaryHigh") or None
    open_ended = bool(high and high >= OPEN_ENDED_SALARY)

    if detail:
        jd = "\n".join(
            [
                (detail.get("jobDetail") or {}).get("jobDescription") or "",
                (detail.get("condition") or {}).get("other") or "",
            ]
        )
        job_category = " ".join(
            c.get("description", "")
            for c in (detail.get("jobDetail") or {}).get("jobCategory") or []
        )
    else:
        jd = summary.get("description") or ""
        job_category = ""

    period = period_to_years(summary.get("period"))
    score = record.get("total_score")
    row = JobRow(
        job_no=record["job_no"],
        company=record.get("cust_name") or "",
        title=record.get("job_name") or "",
        title_group=title_group(record.get("job_name") or ""),
        url=record.get("job_url") or "",
        area=record.get("area_desc") or "",
        employees=summary.get("employeeCount") or None,
        period=period,
        experience=format_experience(period),
        education=record.get("edu_desc") or "",
        salary_text=format_salary(low, high, s10),
        salary_low_monthly=_monthly(low, s10),
        salary_high_monthly=None if open_ended else _monthly(high, s10),
        salary_open_ended=open_ended,
        english=_requires_english(summary, jd),
        industry=industry_group(summary.get("coIndustry")),
        industry_desc=summary.get("coIndustryDesc") or "",
        first_seen=datetime.fromisoformat(record["first_seen_at"]).astimezone(TAIPEI).date(),
        pipeline_status=record.get("status") or "",
        has_detail=detail is not None,
        jd=jd,
        job_category=job_category,
        summary_text=summary_skill_text(summary),
        detail_text=detail_skill_text(detail) if detail else None,
        deep_score=int(score) if score is not None else None,
        one_liner=record.get("one_liner") or "",
    )
    if detail:
        row.jd_description, row.jd_conditions = detail_sections(detail)
    if have_skills is not None:
        row.skill_match = skill_match(have_skills, summary, detail)
        row.prog_rate = row.skill_match.rate
    _attach_hit_rate(row, hit)
    row.gates = gates.evaluate(row)
    return row


def _attach_hit_rate(row: JobRow, hit: StoredHitRate | None) -> None:
    if hit is not None:
        row.hit_rate = hit.rate
        row.hit_rate_status = "已計算" if hit.rate is not None else "無明列必備"
        row.hit_requirements = list(hit.requirements)
        row.hit_core_missed = hit.core_missed
        row.hit_model = hit.model
    elif not row.has_detail:
        row.hit_rate_status = "無全文"
    else:
        row.hit_rate_status = "未計算"


def _monthly(value: int | None, s10: int | None) -> int | None:
    if not value or value >= OPEN_ENDED_SALARY:
        return None
    return monthly_equivalent(value, s10)


def _requires_english(summary: Mapping[str, Any], jd: str) -> bool:
    langs = [x.get("language") for x in summary.get("languageRequirements") or []]
    if ENGLISH_CODE in langs:
        return True
    lowered = jd.lower()
    return any(word in lowered for word in _ENGLISH_TEXT)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


# ── 篩選與排序 ──────────────────────────────────────────────────────
GATE_FILTERS = ("全部", "通過第 1 道", "卡在第 1 道", "命中率達標", "卡在第 3 道")
SORT_KEYS = {
    "深評分數": "deep_score",
    "首次出現": "first_seen",
    "員工數": "employees",
    "命中率(LLM)": "hit_rate",
    "命中率(程式)": "prog_rate",
}


def week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


@dataclass
class CandidateFilter:
    start: date
    end: date
    industries: list[str] = field(default_factory=list)
    title_groups: list[str] = field(default_factory=list)
    title_query: str = ""
    gate: str = "通過第 1 道"
    no_english_only: bool = False
    pipeline_statuses: list[str] | None = None  # None = 不限
    hide_skipped: bool = True


def in_range(rows: Iterable[JobRow], start: date, end: date) -> list[JobRow]:
    return [r for r in rows if start <= r.first_seen <= end]


def apply_filter(
    rows: Iterable[JobRow], f: CandidateFilter, latest: Mapping[str, Decision]
) -> list[JobRow]:
    out = []
    for r in in_range(rows, f.start, f.end):
        if r.industry not in f.industries or r.title_group not in f.title_groups:
            continue
        if not title_matches(r.title, f.title_query):
            continue
        if f.gate == "通過第 1 道" and not r.gates.passes_through(1):
            continue
        if f.gate == "卡在第 1 道" and r.gates.stuck_at != 1:
            continue
        # 「達標」要求真的算過且 ≥ 門檻;未計算不算達標(但也不算卡住)
        if f.gate == "命中率達標" and not (
            r.gates.passes_through(1) and r.gates.gate3 is gates.Light.PASS
        ):
            continue
        if f.gate == "卡在第 3 道" and r.gates.stuck_at != 3:
            continue
        if f.no_english_only and r.english:
            continue
        if f.pipeline_statuses is not None and r.pipeline_status not in f.pipeline_statuses:
            continue
        decision = latest.get(r.job_no)
        if f.hide_skipped and decision is not None and decision.status == "skip":
            continue
        out.append(r)
    return out


def sort_rows(rows: list[JobRow], key: str) -> list[JobRow]:
    """由大到小;沒有值(未評分、未提供、未計算)的一律排最後。"""
    attr = SORT_KEYS[key]
    present = [r for r in rows if getattr(r, attr) is not None]
    missing = [r for r in rows if getattr(r, attr) is None]
    return sorted(present, key=lambda r: getattr(r, attr), reverse=True) + missing


def picks(
    rows: Iterable[JobRow], latest: Mapping[str, Decision], start: date, end: date
) -> list[JobRow]:
    """匯出用:最新標記是「投」且首次出現在期間內。"""
    return [
        r
        for r in in_range(rows, start, end)
        if (d := latest.get(r.job_no)) is not None and d.status == "apply"
    ]
