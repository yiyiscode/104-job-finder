"""要查哪些公司:標了「投」的,加上投遞清單裡標「待確認」的(= 已標投遞頁的清單候補)。

UI 的公司評價頁與 ``jobfinder company review`` 共用這一份,兩邊看到的清單才會一樣。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ..webui.decisions import Decision
from ..webui.rows import JobRow
from .schema import company_key, is_placeholder
from .store import StoredCompany


@dataclass
class InterestedCompany:
    key: str
    name: str  # 同一家有多種寫法時取最長的(通常是含「股份有限公司」的正式名)
    jobs: list[JobRow] = field(default_factory=list)
    applied: int = 0  # 其中標「投」的筆數

    @property
    def best_score(self) -> int:
        return max((j.deep_score or 0 for j in self.jobs), default=0)


def interested_companies(
    rows: Iterable[JobRow], latest: Mapping[str, Decision]
) -> list[InterestedCompany]:
    """標投的公司排前面,再依該公司最高深評分。"""
    found: dict[str, InterestedCompany] = {}
    for r in rows:
        decision = latest.get(r.job_no)
        if decision is None or is_placeholder(r.company):
            continue
        applied = decision.status == "apply"
        if not (applied or (decision.status == "pending" and r.in_shortlist)):
            continue
        key = company_key(r.company)
        c = found.setdefault(key, InterestedCompany(key, r.company))
        if len(r.company) > len(c.name):
            c.name = r.company
        c.jobs.append(r)
        c.applied += applied
    return sorted(found.values(), key=lambda c: (c.applied == 0, -c.best_score, c.name))


def pick_targets(
    companies: list[InterestedCompany],
    stored: Mapping[str, StoredCompany],
    now: datetime,
    ttl_days: int,
    limit: int,
) -> tuple[list[InterestedCompany], int]:
    """還沒查過、或結果超過 ttl_days 的;查詢中的不重複發。回傳 (本次要查的, 總待查數)。"""
    pending = [
        c
        for c in companies
        if not (s := stored.get(c.key)) or not (s.fresh(now, ttl_days) or s.running(now))
    ]
    return pending[:limit], len(pending)
