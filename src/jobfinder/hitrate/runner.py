"""挑職缺 → 逐筆問 LLM → 算命中率 → 存檔。"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..webui.candidates import load_rows
from ..webui.rows import JobRow
from ..webui.snapshot import connect_readonly, take_snapshot
from .compute import MAX_REQUIREMENTS, HitRateCheck, hit_rate
from .prompts import SYSTEM_HITRATE, build_hitrate_user
from .store import HitRateStore

log = logging.getLogger(__name__)

#: 連續失敗這麼多次就中止 —— 多半是 API key、額度或模型下架,繼續打只是浪費
MAX_CONSECUTIVE_FAILURES = 3
#: 用來挑職缺的 jobs.db 快照放這,跟 Web UI 的分開,避免互相清掉對方的快照
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "jobfinder-hitrate"


class StructuredClient(Protocol):
    async def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_model: type[Any],
        schema_name: str,
        temperature: float = 0.2,
    ) -> tuple[Any, float]: ...


@dataclass
class HitRateReport:
    candidates: int = 0  # 符合條件且尚未計算的
    computed: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    stopped_reason: str = ""
    rates: list[tuple[str, float | None]] = field(default_factory=list)

    def summary_line(self) -> str:
        line = (
            f"命中率:待算 {self.candidates} 筆 → 完成 {self.computed} 筆、失敗 {self.failed} 筆"
            f" · 成本 ${self.cost_usd:.4f}"
        )
        return line + (f" · 中止:{self.stopped_reason}" if self.stopped_reason else "")


def pick_targets(rows: list[JobRow], done: set[str], limit: int) -> tuple[list[JobRow], int]:
    """有全文、沒卡在第 1 道、還沒用這版履歷算過的;由新到舊。回傳 (本次要算的, 總待算數)。"""
    pending = [
        r for r in rows if r.detail_text and r.gates.passes_through(1) and r.job_no not in done
    ]
    pending.sort(key=lambda r: (r.first_seen, r.job_no), reverse=True)
    return pending[:limit], len(pending)


def load_job_rows(jobs_db: Path) -> list[JobRow]:
    conn = connect_readonly(take_snapshot(jobs_db, SNAPSHOT_DIR))
    try:
        return load_rows(conn)
    finally:
        conn.close()


async def run_hitrate(
    *,
    rows: list[JobRow],
    store: HitRateStore,
    client: StructuredClient,
    model: str,
    resume_text: str,
    resume_hash: str,
    limit: int,
    cost_cap_usd: float,
    now: datetime,
    dry_run: bool = False,
) -> HitRateReport:
    report = HitRateReport()
    done = set(store.for_resume(resume_hash))
    targets, report.candidates = pick_targets(rows, done, limit)
    if dry_run:
        report.rates = [(r.job_no, None) for r in targets]
        report.stopped_reason = "dry-run,未呼叫 LLM"
        return report

    consecutive_failures = 0
    for row in targets:
        if report.cost_usd >= cost_cap_usd:
            report.stopped_reason = f"達成本上限 ${cost_cap_usd:.2f}"
            break
        try:
            check, cost = await client.structured(
                model=model,
                system=SYSTEM_HITRATE,
                user=build_hitrate_user(resume_text, row.title, row.company, row.detail_text or ""),
                schema_model=HitRateCheck,
                schema_name="hit_rate_check",
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 —— 單筆失敗不該拖垮整批
            report.failed += 1
            consecutive_failures += 1
            log.warning("%s 命中率計算失敗:%s", row.job_no, exc)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                report.stopped_reason = f"連續失敗 {consecutive_failures} 次"
                break
            continue

        consecutive_failures = 0
        report.cost_usd += cost
        requirements = check.requirements[:MAX_REQUIREMENTS]
        result = hit_rate(requirements)
        store.save(row.job_no, resume_hash, result, requirements, model, cost, now)
        report.computed += 1
        report.rates.append((row.job_no, result.rate))
        log.info(
            "%s %s|%s:必備 %d 條,命中率 %s",
            row.job_no,
            row.company,
            row.title,
            result.required,
            "—" if result.rate is None else f"{result.rate:.0%}",
        )
    return report
