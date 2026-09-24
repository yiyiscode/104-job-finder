"""hitrate.db:命中率結果。以 (job_no, resume_hash) 為鍵,同一版履歷重算會覆蓋。

獨立檔案、短交易、每次操作開關連線 —— 理由同 decisions.db(docs/adr/0001)。
Web UI 只讀這個檔。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .compute import HitRate, Requirement

SCHEMA = """
CREATE TABLE IF NOT EXISTS hitrates (
    job_no       TEXT NOT NULL,
    resume_hash  TEXT NOT NULL,
    rate         REAL,              -- NULL = JD 沒有可辨識的必備條件
    required_n   INTEGER NOT NULL,
    met_n        INTEGER NOT NULL,
    partial_n    INTEGER NOT NULL,
    requirements TEXT NOT NULL,     -- JSON:逐條判定與證據,UI 展開檢查用
    model        TEXT NOT NULL,
    cost_usd     REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (job_no, resume_hash)
);
"""


@dataclass(frozen=True)
class StoredHitRate:
    job_no: str
    rate: float | None
    required_n: int
    met_n: int
    partial_n: int
    requirements: list[Requirement]
    model: str
    created_at: str


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


class HitRateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def save(
        self,
        job_no: str,
        resume_hash: str,
        result: HitRate,
        requirements: list[Requirement],
        model: str,
        cost: float,
        now: datetime,
    ) -> None:
        conn = _connect(self.path)
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO hitrates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_no,
                        resume_hash,
                        result.rate,
                        result.required,
                        result.met,
                        result.partial,
                        json.dumps([r.model_dump() for r in requirements], ensure_ascii=False),
                        model,
                        cost,
                        now.isoformat(timespec="seconds"),
                    ),
                )
        finally:
            conn.close()

    def for_resume(self, resume_hash: str) -> dict[str, StoredHitRate]:
        if not self.path.exists():
            return {}
        conn = _connect(self.path)
        try:
            rows = conn.execute(
                "SELECT * FROM hitrates WHERE resume_hash = ?", (resume_hash,)
            ).fetchall()
        finally:
            conn.close()
        return {
            r["job_no"]: StoredHitRate(
                job_no=r["job_no"],
                rate=r["rate"],
                required_n=r["required_n"],
                met_n=r["met_n"],
                partial_n=r["partial_n"],
                requirements=[Requirement(**x) for x in json.loads(r["requirements"])],
                model=r["model"],
                created_at=r["created_at"],
            )
            for r in rows
        }
