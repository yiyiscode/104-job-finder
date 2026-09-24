"""把本週選定的職缺匯出成 Markdown / CSV,貼進投遞追蹤用。"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence

from .decisions import Decision
from .rows import JobRow

COLUMNS = ["公司", "職稱", "地點", "員工數", "經歷", "薪資", "英文要求", "深評", "備註", "104 連結"]


def _values(row: JobRow, note: str) -> list[str]:
    return [
        row.company,
        row.title,
        row.area,
        "" if row.employees is None else str(row.employees),
        row.experience,
        row.salary_text,
        "是" if row.english else "否",
        "" if row.deep_score is None else str(row.deep_score),
        note,
        row.url,
    ]


def _md_cell(text: str) -> str:
    # 職稱常含 |(例:「資料工程師 | 金融」),不跳脫會把 Markdown 表格切歪
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(rows: Sequence[JobRow], latest: Mapping[str, Decision]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        note = latest[r.job_no].note if r.job_no in latest else ""
        lines.append("| " + " | ".join(_md_cell(v) for v in _values(r, note)) + " |")
    return "\n".join(lines) + "\n"


def to_csv(rows: Sequence[JobRow], latest: Mapping[str, Decision]) -> bytes:
    """UTF-8 with BOM —— 不帶 BOM 的話,Excel 會用 cp950 開,中文全變亂碼。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        note = latest[r.job_no].note if r.job_no in latest else ""
        writer.writerow(_values(r, note))
    return buf.getvalue().encode("utf-8-sig")
