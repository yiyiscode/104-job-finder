"""把投遞清單 CSV 匯入 shortlist.db + decisions.db。不連 104。

CSV 欄位(第一列是標題)::

    detail_id,company,title,decision,week,note
    7j5sn,台積電,IT Data Engineer,apply,2026-09-28,僅接受官網投遞

* ``decision``:``apply`` / ``pending`` / 空白(只加進清單、不標記)
* ``week``:排定那週的**週一**(YYYY-MM-DD),空白 = 不排

**只播種、不覆蓋**:某筆已經有標記或週次(不論是上次匯入的,還是之後在 UI 改的),
就不再寫。所以重跑匯入是安全的,不會把你在 UI 的改動蓋回去。
標記一律用 detail_id 當鍵,UI 讀取時會用連結尾段併到 jobs.db 的 job_no 上。
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..webui.decisions import DecisionStore
from .store import ShortlistStore

COLUMNS = ("detail_id", "company", "title", "decision", "week", "note")
IMPORTABLE_DECISIONS = ("apply", "pending")


@dataclass
class ImportReport:
    added: int = 0
    updated: int = 0
    decided: int = 0
    planned: int = 0
    errors: list[str] = field(default_factory=list)


def import_csv(
    path: Path,
    shortlist: ShortlistStore,
    decisions: DecisionStore,
    now: datetime,
    aliases: Mapping[str, str] | None = None,
) -> ImportReport:
    """``aliases``(detail_id → job_no):jobs.db 已有的職缺,你可能已經在 UI 用 job_no 標過。"""
    aliases = aliases or {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            return ImportReport(errors=[f"CSV 少了欄位:{'、'.join(sorted(missing))}"])
        rows = list(reader)

    report = ImportReport()
    has_decision = set(decisions.latest(aliases))
    has_plan = set(decisions.latest_plans(aliases))
    for lineno, raw in enumerate(rows, start=2):
        row = {k: (raw.get(k) or "").strip() for k in COLUMNS}
        try:
            week = date.fromisoformat(row["week"]) if row["week"] else None
            if row["decision"] and row["decision"] not in IMPORTABLE_DECISIONS:
                raise ValueError(
                    f"decision 只能是 {' / '.join(IMPORTABLE_DECISIONS)} 或空白"
                    "(不投要選原因,請在 UI 標)"
                )
            if week is not None and week.weekday() != 0:
                raise ValueError(f"week 要填週一的日期,{week} 不是週一")
            is_new = shortlist.add(row["detail_id"], row["company"], row["title"], now)
        except ValueError as exc:
            report.errors.append(f"第 {lineno} 列:{exc}")
            continue

        detail_id = row["detail_id"]
        key = aliases.get(detail_id, detail_id)
        report.added += is_new
        report.updated += not is_new
        if row["decision"] and key not in has_decision:
            decisions.append(detail_id, row["decision"], None, row["note"], now)
            report.decided += 1
        if week is not None and key not in has_plan:
            decisions.plan(detail_id, week, now)
            report.planned += 1
    return report
