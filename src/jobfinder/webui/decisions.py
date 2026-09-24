"""使用者的「投／不投／待確認」標記。存在獨立的 decisions.db,**不在 jobs.db**。

* 獨立檔案:pipeline 會整檔覆蓋 jobs.db,也會 prune + CASCADE(見 docs/adr/0001)
* 只追加:改判也是新增一列,UI 取最新。「待確認 → 投」的軌跡是之後檢討閘門的資料
* 短交易:每個操作自己開連線、提交、關閉,不長期持有檔案
* 「不投必填原因」由 DB 的 CHECK 與 ``append`` 的驗證兩邊一起擋
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STATUSES = ("apply", "skip", "pending")
STATUS_LABELS = {"apply": "✅ 投", "skip": "⛔ 不投", "pending": "❔ 待確認"}
SKIP_REASONS = (
    "SI接案",
    "無新人培訓",
    "職務打雜",
    "命中率不足",
    "薪資低於底線",
    "要求英文",
    "其他",
)

_REASON_LIST = ", ".join(f"'{r}'" for r in SKIP_REASONS)
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no     TEXT NOT NULL,   -- 不設外鍵:jobs 在另一個檔案,且會被 prune
    status     TEXT NOT NULL CHECK (status IN ('apply', 'skip', 'pending')),
    reason     TEXT CHECK (reason IN ({_REASON_LIST})),
    note       TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL,
    -- 不投 ⇔ 有原因。原因是檢討閘門的唯一資料,不能漏;其他狀態帶原因則是資料錯亂
    CHECK ((status = 'skip') = (reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_decisions_job ON decisions (job_no, id);
"""


@dataclass(frozen=True)
class Decision:
    id: int
    job_no: str
    status: str
    reason: str | None
    note: str
    decided_at: str


class DecisionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> _Conn:
        return _Conn(self.path)

    def append(
        self, job_no: str, status: str, reason: str | None, note: str, now: datetime
    ) -> None:
        if status not in STATUSES:
            raise ValueError(f"未知的標記狀態:{status}")
        if status == "skip" and reason not in SKIP_REASONS:
            raise ValueError("標「不投」必須選一個原因")
        if status != "skip" and reason is not None:
            raise ValueError("只有「不投」可以帶原因")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO decisions (job_no, status, reason, note, decided_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (job_no, status, reason, note.strip(), now.isoformat(timespec="seconds")),
            )

    def latest(self) -> dict[str, Decision]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.* FROM decisions d
                JOIN (SELECT job_no, MAX(id) AS id FROM decisions GROUP BY job_no) m
                  ON m.id = d.id
                """
            ).fetchall()
        return {r["job_no"]: Decision(**dict(r)) for r in rows}

    def history(self, job_no: str) -> list[Decision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE job_no = ? ORDER BY id", (job_no,)
            ).fetchall()
        return [Decision(**dict(r)) for r in rows]


class _Conn:
    """開 → 交易 → 提交或回滾 → **關閉**。``sqlite3`` 內建的 context manager 不會關連線。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
