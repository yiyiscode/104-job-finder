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

from ..hitrate.compute import MAX_REQUIREMENTS, hit_rate
from ..normalize import (
    OPEN_ENDED_SALARY,
    detail_as_summary,
    format_experience,
    format_salary,
    monthly_equivalent,
    normalize_detail_response,
    period_to_years,
)
from . import gates
from .decisions import Decision
from .groups import industry_group, title_group, title_matches
from .jd_view import detail_sections
from .major import major_requirement
from .rows import JobRow
from .skill_match import skill_match
from .skills import detail_skill_text, summary_skill_text

if TYPE_CHECKING:
    from ..hitrate.store import StoredHitRate
    from ..shortlist.store import Entry, Fetch, ShortlistStore

TAIPEI = ZoneInfo("Asia/Taipei")
ENGLISH_CODE = 1  # languageRequirements[].language;已對照詳細頁的「英文」驗證
_ENGLISH_TEXT = ("英文", "english", "toeic", "多益")

CANDIDATE_SQL = """
SELECT j.job_no, j.job_name, j.cust_name, j.job_url, j.area_desc, j.edu_desc,
       j.first_seen_at, j.status, j.raw_summary, j.raw_detail,
       s.total_score, s.one_liner, s.resume_tip, s.highlights, s.red_flags,
       s.tech_fit, s.exp_fit, s.domain_fit, s.growth_fit, s.practical_fit
FROM jobs j
LEFT JOIN (
    SELECT job_no, total_score, one_liner, resume_tip, highlights, red_flags,
           tech_fit, exp_fit, domain_fit, growth_fit, practical_fit,
           ROW_NUMBER() OVER (PARTITION BY job_no ORDER BY id DESC) AS rn
    FROM scores WHERE stage = 'deep'
) s ON s.job_no = j.job_no AND s.rn = 1
"""


def load_rows(
    conn: sqlite3.Connection,
    hit_rates: Mapping[str, StoredHitRate] | None = None,
    have_skills: frozenset[str] | None = None,
    shortlist: ShortlistStore | None = None,
) -> list[JobRow]:
    """``hit_rates`` 是 hitrate.db 裡「目前這版履歷」的結果;不給就全部視為未計算。
    ``have_skills`` 是履歷的技能集合(程式版命中率用);不給就不算。
    ``shortlist`` 給了就把投遞清單裡、jobs.db 沒有的職缺也組成列。"""
    hit_rates = hit_rates or {}
    rows = [
        build_row(dict(r), hit_rates.get(r["job_no"]), have_skills)
        for r in conn.execute(CANDIDATE_SQL)
    ]
    if shortlist is None:
        return rows
    return merge_shortlist(
        rows, shortlist.entries(), shortlist.latest_fetches(), hit_rates, have_skills
    )


# ── 投遞清單 ────────────────────────────────────────────────────────
SHORTLIST_STATUS = "shortlist"
_JOB_URL = "https://www.104.com.tw/job/"


def detail_id_of(url: str) -> str:
    """職缺連結的尾段(``https://www.104.com.tw/job/7j5sn`` → ``7j5sn``)。"""
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def detail_aliases(rows: Iterable[JobRow]) -> dict[str, str]:
    """detail_id → job_no,只收 jobs.db 的列。投遞清單時期用 detail_id 存的標記靠它併過來。"""
    return {detail_id_of(r.url): r.job_no for r in rows if r.pipeline_status != SHORTLIST_STATUS}


def merge_shortlist(
    rows: list[JobRow],
    entries: Iterable[Entry],
    fetches: Mapping[str, Fetch],
    hit_rates: Mapping[str, StoredHitRate],
    have_skills: frozenset[str] | None,
) -> list[JobRow]:
    """清單裡已在 jobs.db 的只打上標記(那邊資料比較完整);其餘用詳細頁組出新列。"""
    by_detail = {detail_id_of(r.url): r for r in rows}
    out = list(rows)
    for entry in entries:
        if (existing := by_detail.get(entry.detail_id)) is not None:
            existing.in_shortlist = True
            continue
        fetch = fetches.get(entry.detail_id)
        out.append(shortlist_row(entry, fetch, hit_rates.get(entry.detail_id), have_skills))
    return out


def shortlist_row(
    entry: Entry,
    fetch: Fetch | None,
    hit: StoredHitRate | None = None,
    have_skills: frozenset[str] | None = None,
) -> JobRow:
    """只有詳細頁(或什麼都還沒抓)的職缺 → 跟 jobs.db 同形狀的 record → ``build_row``。

    這樣閘門、命中率、JD 原文全部沿用同一套程式,不必為清單另寫一份。
    """
    payload = _loads(fetch.raw_detail) if fetch and fetch.raw_detail else None
    summary = detail_as_summary(payload) if payload else {}
    record = {
        "job_no": entry.detail_id,
        "job_name": summary.get("jobName") or entry.title,
        "cust_name": summary.get("custName") or entry.company,
        "job_url": _JOB_URL + entry.detail_id,
        "area_desc": summary.get("jobAddrNoDesc") or "",
        "edu_desc": (normalize_detail_response(payload, entry.detail_id).edu or "")
        if payload
        else "",
        "first_seen_at": entry.added_at,
        "status": SHORTLIST_STATUS,
        "raw_summary": json.dumps(summary, ensure_ascii=False),
        "raw_detail": fetch.raw_detail if payload else None,
    }
    row = build_row(record, hit, have_skills)
    row.in_shortlist = True
    if payload is None:
        row.fetch_note = (fetch.error or "") if fetch else "尚未抓取"
    return row


#: 深評五個維度與滿分(與 scoring/prompts.py 的配分一致)
SCORE_PARTS = (
    ("tech_fit", "技術", 35),
    ("exp_fit", "年資", 25),
    ("domain_fit", "領域", 15),
    ("growth_fit", "成長", 15),
    ("practical_fit", "實務", 10),
)
_COMPANY_PREFIX = "https://www.104.com.tw/company/"


def _score_parts(record: Mapping[str, Any]) -> list[tuple[str, int, int]]:
    """(名稱, 得分, 滿分);沒有深評或欄位缺漏時回空的。"""
    parts = [(label, record.get(col), full) for col, label, full in SCORE_PARTS]
    if any(v is None for _, v, _ in parts):
        return []
    return [(label, int(v), full) for label, v, full in parts]


def _welfare_text(detail: Mapping[str, Any] | None) -> str:
    """詳細頁的福利制度原文(`welfare.welfare`)。第 2 道的新人培訓多半寫在這裡。"""
    welfare = (detail or {}).get("welfare")
    text = welfare.get("welfare") if isinstance(welfare, dict) else None
    return text if isinstance(text, str) else ""


def _company_url(summary: Mapping[str, Any]) -> str:
    """列表 JSON 的 ``link.cust``。只收 104 公司頁,其他一律不給(UI 會把它做成連結)。"""
    link = summary.get("link")
    url = link.get("cust") if isinstance(link, dict) else None
    if isinstance(url, str) and url.startswith("//"):
        url = "https:" + url
    return url if isinstance(url, str) and url.startswith(_COMPANY_PREFIX) else ""


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
        resume_tip=record.get("resume_tip") or "",
        highlights=_loads(record.get("highlights")) or [],
        red_flags=_loads(record.get("red_flags")) or [],
        score_parts=_score_parts(record),
        company_url=_company_url(summary),
        industry_code=str(summary.get("coIndustry") or ""),
        welfare=_welfare_text(detail),
    )
    if detail:
        row.jd_description, row.jd_conditions = detail_sections(detail)
    if have_skills is not None:
        row.skill_match = skill_match(have_skills, summary, detail)
        row.prog_rate = row.skill_match.rate
    majors = (detail.get("condition") or {}).get("major") or [] if detail else []
    _attach_hit_rate(row, hit, majors)
    row.gates = gates.evaluate(row)
    return row


def _attach_hit_rate(row: JobRow, hit: StoredHitRate | None, majors: list[str]) -> None:
    if hit is not None:
        # 科系要求由程式端比對、附加成一條必備,再重算 —— 已存的 LLM／手動判讀不必重跑
        requirements = list(hit.requirements[:MAX_REQUIREMENTS])
        if (major := major_requirement(majors)) is not None:
            requirements.append(major)
        result = hit_rate(requirements, limit=None)
        row.hit_rate = result.rate
        row.hit_rate_status = "已計算" if result.rate is not None else "無明列必備"
        row.hit_requirements = requirements
        row.hit_core_missed = result.core_missed
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


# ── pipeline 狀態(每日 pipeline 的處理結果,與閘門無關)──
#: 設定處:pipeline.py 的 filtered_out / screened_out / scored / notified;new 是 register 的預設值
PIPELINE_STATUS_HELP = {
    "new": "已登記、還沒評過(主要是 8/22 首次執行登記後就沒評的舊資料)",
    "filtered_out": "被規則層濾掉:產業不在半導體/電子/金融,或員工 <500。LLM 沒看過",
    "screened_out": "LLM 粗篩刷掉(粗分 <45),沒抓全文、沒深評。"
    "⚠️ 9/24 以前規則層濾掉的也標這個(當時兩種原因還沒分開)",
    "scored": "粗篩通過、抓了全文、深評過,但分數沒擠進當天推播(前 5 名且 ≥60 分)",
    "notified": "深評後進了當天前 5 名且 ≥60 分,已推播到 Telegram",
    SHORTLIST_STATUS: "不在 pipeline 裡,是投遞清單手動加的(`jobfinder shortlist fetch` 補抓詳細頁)",
}
PIPELINE_STATUS_LEGEND = " · ".join(f"{k}:{v}" for k, v in PIPELINE_STATUS_HELP.items())


# ── 篩選與排序 ──────────────────────────────────────────────────────
GATE_FILTERS = ("全部", "通過第 1 道", "卡在第 1 道", "卡在第 2 道", "命中率達標", "卡在第 3 道")
SORT_KEYS = {
    "深評分數": "deep_score",
    "首次出現": "first_seen",
    "員工數": "employees",
    "③命中率": "hit_rate",
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
    #: 對照 Telegram 日報用:只看推播過的,**其他條件全部不套用**(只留日期)。
    #: 否則預設的產業/職稱/閘門會把推播過的金融職缺藏起來,跟日報對不上。
    notified_only: bool = False


def in_range(rows: Iterable[JobRow], start: date, end: date) -> list[JobRow]:
    return [r for r in rows if start <= r.first_seen <= end]


def apply_filter(
    rows: Iterable[JobRow], f: CandidateFilter, latest: Mapping[str, Decision]
) -> list[JobRow]:
    out = []
    for r in in_range(rows, f.start, f.end):
        if f.notified_only:
            if r.pipeline_status == "notified":
                out.append(r)
            continue
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
        if f.gate == "卡在第 2 道" and r.gates.stuck_at != 2:
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
