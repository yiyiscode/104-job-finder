"""去重與持久化。

去重是這個專案的核心邏輯:日報的價值在於「只告訴我沒看過的」。規則見 SPEC.md §5。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..config import DedupeCfg
from ..models import JobDetail, JobSummary, RejectedJob, RunReport, ScoredJob
from ..scrape.blocking import CircuitState

log = logging.getLogger(__name__)


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class JobRepo:
    """所有 SQL 都在這裡。上層只認領域模型。"""

    def __init__(self, conn: sqlite3.Connection, dedupe: DedupeCfg | None = None) -> None:
        self.conn = conn
        self.dedupe = dedupe or DedupeCfg()

    # ── 去重(SPEC.md §5)────────────────────────────────────────────

    def register(self, jobs: list[JobSummary], now: datetime) -> list[JobSummary]:
        """記錄所有看到的職缺,回傳其中判定為「今天新出現」的那些。

        判定規則:

        * 沒看過 → 新
        * 看過但 ``appear_date`` 沒變 → 不新
        * 看過、``appear_date`` 變了,但距上次判定為新未滿冷卻期 → 不新
          (避免雇主天天刷新上架日就天天被推播)
        * 看過、``appear_date`` 變了且已過冷卻期 → 新(視為重新開缺)
        * 曾被粗篩刷掉 → 不新
        """
        new_jobs: list[JobSummary] = []
        cooldown = timedelta(days=self.dedupe.repost_cooldown_days)
        iso_now = now.isoformat()

        for job in jobs:
            row = self.conn.execute(
                "SELECT appear_date, last_new_at, status, matched_keywords"
                " FROM jobs WHERE job_no = ?",
                (job.job_no,),
            ).fetchone()

            if row is None:
                self._insert_job(job, now)
                new_jobs.append(job)
                continue

            merged = sorted(set(_loads(row["matched_keywords"], [])) | set(job.matched_keywords))
            is_new = self._is_repost(row, job, now, cooldown)

            self.conn.execute(
                """
                UPDATE jobs
                   SET last_seen_at     = ?,
                       seen_count       = seen_count + 1,
                       appear_date      = ?,
                       matched_keywords = ?,
                       content_hash     = ?,
                       raw_summary      = ?,
                       last_new_at      = CASE WHEN ? THEN ? ELSE last_new_at END,
                       status           = CASE WHEN ? THEN 'new' ELSE status END
                 WHERE job_no = ?
                """,
                (
                    iso_now,
                    job.appear_date,
                    json.dumps(merged, ensure_ascii=False),
                    job.content_hash(),
                    json.dumps(job.raw, ensure_ascii=False),
                    is_new,
                    iso_now,
                    is_new,
                    job.job_no,
                ),
            )

            if is_new:
                new_jobs.append(job.model_copy(update={"matched_keywords": merged}))

        self.conn.commit()
        return new_jobs

    def _is_repost(
        self,
        row: sqlite3.Row,
        job: JobSummary,
        now: datetime,
        cooldown: timedelta,
    ) -> bool:
        if row["status"] == "screened_out":
            # LLM 已經判定過不適合,重新上架也不必再煩使用者。
            #
            # ⚠️ `filtered_out`(產業/規模規則層濾掉的)**刻意不放進來**。
            # 那一層的判定依據是設定檔,不是職缺本身 —— 使用者哪天放寬
            # `targeting` 之後,這些職缺應該要能重新被考慮。而在設定沒變的情況下
            # 它們反正會再被濾一次,短路與否結果相同。
            return False
        if not job.appear_date or job.appear_date == row["appear_date"]:
            return False
        try:
            last_new = datetime.fromisoformat(row["last_new_at"])
        except (TypeError, ValueError):
            return True
        return (now - last_new) >= cooldown

    def _insert_job(self, job: JobSummary, now: datetime) -> None:
        iso = now.isoformat()
        self.conn.execute(
            """
            INSERT INTO jobs (
                job_no, job_name, cust_name, cust_no, job_url, area_desc,
                salary_desc, salary_low, salary_high, period_desc, edu_desc,
                appear_date, first_seen_at, last_seen_at, last_new_at,
                seen_count, content_hash, matched_keywords, raw_summary, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,'new')
            """,
            (
                job.job_no,
                job.job_name,
                job.cust_name,
                job.cust_no,
                job.job_url,
                job.area_desc,
                job.salary_desc,
                job.salary_low,
                job.salary_high,
                job.period_desc,
                job.edu_desc,
                job.appear_date,
                iso,
                iso,
                iso,
                job.content_hash(),
                json.dumps(job.matched_keywords, ensure_ascii=False),
                json.dumps(job.raw, ensure_ascii=False),
            ),
        )

    # ── 狀態與內容 ──────────────────────────────────────────────────

    def mark_status(self, job_nos: list[str], status: str) -> None:
        if not job_nos:
            return
        self.conn.executemany(
            "UPDATE jobs SET status = ? WHERE job_no = ?",
            [(status, n) for n in job_nos],
        )
        self.conn.commit()

    def detail_index(self) -> dict[str, tuple[str, bool]]:
        """detail_id(連結尾段)→ (job_no, 有沒有全文)。投遞清單用 detail_id 對到這裡。"""
        rows = self.conn.execute(
            "SELECT job_no, job_url, raw_detail IS NOT NULL AND raw_detail != '' AS has_detail"
            " FROM jobs"
        ).fetchall()
        return {
            r["job_url"].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]: (
                r["job_no"],
                bool(r["has_detail"]),
            )
            for r in rows
        }

    def save_detail(self, job_no: str, raw: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE jobs SET raw_detail = ? WHERE job_no = ?",
            (json.dumps(raw, ensure_ascii=False), job_no),
        )
        self.conn.commit()

    def save_score(
        self,
        scored: ScoredJob,
        *,
        run_id: int | None,
        stage: str = "deep",
        raw_response: str = "",
        now: datetime | None = None,
    ) -> None:
        b = scored.breakdown
        self.conn.execute(
            """
            INSERT INTO scores (
                job_no, run_id, stage, model, total_score,
                tech_fit, exp_fit, domain_fit, growth_fit, practical_fit,
                verdict, one_liner, highlights, red_flags, resume_tip,
                adjustments, raw_response, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scored.job_no,
                run_id,
                stage,
                scored.model_used,
                scored.total,
                b.tech_fit,
                b.exp_fit,
                b.domain_fit,
                b.growth_fit,
                b.practical_fit,
                scored.verdict,
                scored.one_liner,
                json.dumps(scored.highlights, ensure_ascii=False),
                json.dumps(scored.red_flags, ensure_ascii=False),
                scored.resume_tip,
                json.dumps(scored.adjustments, ensure_ascii=False),
                raw_response,
                (now or datetime.now()).isoformat(),
            ),
        )
        self.conn.commit()

    def save_screen_drops(
        self,
        dropped: list[RejectedJob],
        *,
        run_id: int | None,
        model: str = "",
        now: datetime | None = None,
    ) -> None:
        """粗篩刷掉的職缺寫進 ``scores``(``stage='screen'``)。

        **只寫被刷掉的。** 留下來的馬上就會有一筆 deep 紀錄,再存一次是冗餘。

        欄位比 deep 少很多 —— 粗篩只給 ``rough`` 分數與一句理由,沒有五維拆解,
        所以那幾欄留 NULL。存這個的用途是日後回頭看 ``llm.screen.rough_threshold``
        設得對不對:沒有它,被刷掉的職缺在 DB 裡完全沒有痕跡,只剩當天摘要裡的一行字。
        """
        if not dropped:
            return
        stamp = (now or datetime.now()).isoformat()
        self.conn.executemany(
            """
            INSERT INTO scores (job_no, run_id, stage, model, total_score, one_liner, created_at)
            VALUES (?, ?, 'screen', ?, ?, ?, ?)
            """,
            [(r.job_no, run_id, model, r.total, r.reason, stamp) for r in dropped],
        )
        self.conn.commit()

    def mark_notified(self, job_nos: list[str]) -> None:
        self.mark_status(job_nos, "notified")

    # ── 補深評佇列 ──────────────────────────────────────────────────

    #: 詳細頁暫時失敗幾次就放棄 —— 免得一筆壞掉的職缺每天吃掉一個名額
    BACKFILL_MAX_ATTEMPTS = 3

    def enqueue_backfill(self, items: dict[str, int], now: datetime) -> int:
        """把 {job_no: 優先度} 排進佇列。已有深評或不在 jobs 表的跳過;已排過的不重複排。"""
        added = 0
        for job_no, priority in items.items():
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO backfill_queue (job_no, priority, queued_at)
                SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM jobs WHERE job_no = ?)
                  AND NOT EXISTS (SELECT 1 FROM scores WHERE job_no = ? AND stage = 'deep')
                """,
                (job_no, priority, now.isoformat(), job_no, job_no),
            )
            added += cur.rowcount
        self.conn.commit()
        return added

    def pending_backfill(self) -> list[JobSummary]:
        """待補的職缺,優先度高的在前。"""
        rows = self.conn.execute(
            """
            SELECT j.raw_summary, j.matched_keywords
              FROM backfill_queue b JOIN jobs j USING (job_no)
             WHERE b.done_at IS NULL AND j.raw_summary IS NOT NULL
             ORDER BY b.priority DESC, b.queued_at
            """
        ).fetchall()
        out = []
        for row in rows:
            summary = self._summary_from_raw(
                _loads(row["raw_summary"], {}), _loads(row["matched_keywords"], [])
            )
            if summary is not None:
                out.append(summary)
        return out

    def finish_backfill(self, job_no: str, outcome: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE backfill_queue SET done_at = ?, outcome = ? WHERE job_no = ?",
            (now.isoformat(), outcome, job_no),
        )
        self.conn.commit()

    def backfill_failed(self, job_no: str, now: datetime) -> None:
        """詳細頁或深評暫時失敗:記一次,滿上限就放棄。"""
        self.conn.execute(
            "UPDATE backfill_queue SET attempts = attempts + 1 WHERE job_no = ?", (job_no,)
        )
        self.conn.execute(
            "UPDATE backfill_queue SET done_at = ?, outcome = 'gave_up'"
            " WHERE job_no = ? AND attempts >= ?",
            (now.isoformat(), job_no, self.BACKFILL_MAX_ATTEMPTS),
        )
        self.conn.commit()

    def backfill_status(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT b.job_no, b.priority, b.attempts, b.done_at, b.outcome,
                   j.job_name, j.cust_name
              FROM backfill_queue b LEFT JOIN jobs j USING (job_no)
             ORDER BY b.done_at IS NOT NULL, b.priority DESC
            """
        ).fetchall()

    def mark_scored(self, job_nos: list[str]) -> None:
        """深評過但沒推播。**不覆蓋 notified** —— `--replay` 重評時,曾經推過的職缺
        這次沒被選上,也不該抹掉「使用者收過這張卡片」的事實。"""
        if not job_nos:
            return
        self.conn.executemany(
            "UPDATE jobs SET status = 'scored' WHERE job_no = ? AND status != 'notified'",
            [(n,) for n in job_nos],
        )
        self.conn.commit()

    # ── 執行紀錄 ────────────────────────────────────────────────────

    def start_run(self, now: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'success')",
            (now.isoformat(),),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, report: RunReport) -> None:
        self.conn.execute(
            """
            UPDATE runs
               SET finished_at = ?, status = ?, jobs_fetched = ?, jobs_new = ?,
                   jobs_filtered_out = ?,
                   jobs_screened_in = ?, jobs_deep_scored = ?, jobs_notified = ?,
                   requests_used = ?, llm_cost_usd = ?, schema_drift_count = ?,
                   error_kind = ?, error_detail = ?
             WHERE id = ?
            """,
            (
                (report.finished_at or datetime.now()).isoformat(),
                report.status,
                report.jobs_fetched,
                report.jobs_new,
                report.jobs_filtered_out,
                report.jobs_screened_in,
                report.jobs_deep_scored,
                len(report.notified),
                report.requests_used,
                report.llm_cost_usd,
                report.schema_drift_count,
                report.error_kind,
                (report.error_detail or "")[:2000] or None,
                report.run_id,
            ),
        )
        self.conn.commit()

    def has_run_today(self, now: datetime) -> bool:
        """SPEC.md 規則 3:每日僅執行一次。

        只有「真的碰到 104」的執行才算數 —— 被熔斷擋下(skipped)、或一個請求都沒
        送到 104 就斷網(NetworkUnavailable 且 requests_used = 0)都不該用掉今天的額度。
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM runs"
            " WHERE date(started_at) = date(?) AND status != 'skipped'"
            " AND NOT (COALESCE(error_kind, '') = 'NetworkUnavailable'"
            " AND COALESCE(requests_used, 0) = 0)",
            (now.isoformat(),),
        ).fetchone()
        return bool(row and row["n"] > 0)

    def load_replay(self, run_id: int) -> list[tuple[JobSummary, JobDetail | None]]:
        """拿某次 run 的原始 JSON 重跑評分,不必重爬。

        這不只是方便 —— 它是防封鎖策略的一部分:調 prompt 時完全不需要碰 104。
        """
        rows = self.conn.execute(
            """
            SELECT j.job_no, j.raw_summary, j.raw_detail, j.matched_keywords
              FROM jobs j
              JOIN scores s ON s.job_no = j.job_no
             WHERE s.run_id = ?
             GROUP BY j.job_no
            """,
            (run_id,),
        ).fetchall()

        from ..normalize import normalize_detail_response  # 延遲載入,避免循環相依

        out: list[tuple[JobSummary, JobDetail | None]] = []
        for row in rows:
            raw_summary = _loads(row["raw_summary"], None)
            if not raw_summary:
                continue
            keywords = _loads(row["matched_keywords"], [])
            page = self._summary_from_raw(raw_summary, keywords)
            if page is None:
                continue
            raw_detail = _loads(row["raw_detail"], None)
            detail = normalize_detail_response(raw_detail, row["job_no"]) if raw_detail else None
            out.append((page, detail))
        return out

    @staticmethod
    def _summary_from_raw(raw: dict[str, Any], keywords: list[str]) -> JobSummary | None:
        from ..normalize import normalize_search_response

        # 搜尋回應的 data 本身就是陣列(沒有 data.list),所以重播時也照這個形狀包
        page = normalize_search_response({"data": [raw]}, "")
        if not page.jobs:
            return None
        return page.jobs[0].model_copy(update={"matched_keywords": keywords})

    # ── 熔斷器 ──────────────────────────────────────────────────────

    def get_circuit(self) -> CircuitState:
        row = self.conn.execute(
            "SELECT state, tripped_at, reason, consecutive_failures FROM circuit_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return CircuitState.closed()
        tripped_at = None
        if row["tripped_at"]:
            try:
                tripped_at = datetime.fromisoformat(row["tripped_at"])
            except ValueError:
                tripped_at = None
        return CircuitState(
            state=row["state"],
            tripped_at=tripped_at,
            reason=row["reason"] or "",
            consecutive_failures=int(row["consecutive_failures"] or 0),
        )

    def save_circuit(self, state: CircuitState) -> None:
        self.conn.execute(
            """
            UPDATE circuit_state
               SET state = ?, tripped_at = ?, reason = ?, consecutive_failures = ?
             WHERE id = 1
            """,
            (
                state.state,
                state.tripped_at.isoformat() if state.tripped_at else None,
                state.reason,
                state.consecutive_failures,
            ),
        )
        self.conn.commit()

    # ── 維護 ────────────────────────────────────────────────────────

    def prune(self, now: datetime) -> int:
        cutoff = (now - timedelta(days=self.dedupe.retain_days)).isoformat()
        cur = self.conn.execute("DELETE FROM jobs WHERE last_seen_at < ?", (cutoff,))
        # 還被 scores.run_id 引用的舊執行要留著:職缺若一直被抓到,它的評分不會被刪,
        # 指向的那次執行一刪就撞外鍵(FOREIGN KEY constraint failed),整次執行以錯誤結束。
        # 不改成 ON DELETE SET NULL —— 那要整表重建 scores,而 scores 是最怕被連帶刪光的表。
        self.conn.execute(
            "DELETE FROM runs WHERE started_at < ?"
            " AND id NOT IN (SELECT run_id FROM scores WHERE run_id IS NOT NULL)",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount or 0
