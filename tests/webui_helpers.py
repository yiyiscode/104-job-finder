"""webui 測試共用的假資料工廠。刻意用 104 的真實欄位名,別憑印象改(見 CLAUDE.md)。"""

from __future__ import annotations

import json
from typing import Any

from jobfinder.webui.candidates import build_row
from jobfinder.webui.rows import JobRow


def summary(**overrides: Any) -> dict[str, Any]:
    base = {
        "jobName": "資料工程師",
        "description": "負責資料管線與 SQL 報表",
        "employeeCount": 800,
        "coIndustry": 1001006001,
        "coIndustryDesc": "半導體製造業",
        "period": 0,
        "s10": 10,
        "salaryLow": 0,
        "salaryHigh": 0,
        "languageRequirements": [],
        "pcSkills": [],
    }
    base.update(overrides)
    return base


def detail(jd: str = "建置 ETL 與資料倉儲", categories: tuple[str, ...] = ("資料工程師",)) -> dict:
    return {
        "data": {
            "jobDetail": {
                "jobDescription": jd,
                "jobCategory": [{"code": "0", "description": c} for c in categories],
            },
            "condition": {"other": "", "skill": [], "specialty": []},
        }
    }


def record(
    job_no: str = "1",
    *,
    title: str | None = None,
    company: str = "台積電",
    area: str = "新竹市",
    first_seen: str = "2026-09-22T08:00:00+08:00",
    status: str = "scored",
    score: int | None = 80,
    summary_overrides: dict[str, Any] | None = None,
    detail_payload: dict | None = None,
) -> dict[str, Any]:
    s = summary(**(summary_overrides or {}))
    if title is not None:
        s["jobName"] = title
    return {
        "job_no": job_no,
        "job_name": s["jobName"],
        "cust_name": company,
        "job_url": f"https://www.104.com.tw/job/{job_no}",
        "area_desc": area,
        "edu_desc": "碩士以上",
        "first_seen_at": first_seen,
        "status": status,
        "raw_summary": json.dumps(s, ensure_ascii=False),
        "raw_detail": json.dumps(detail_payload, ensure_ascii=False) if detail_payload else None,
        "total_score": score,
        "one_liner": "",
    }


def row(**kwargs: Any) -> JobRow:
    return build_row(record(**kwargs))
