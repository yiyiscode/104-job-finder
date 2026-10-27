"""companies.db:每家公司一列,以 :func:`company_key` 為鍵。

獨立檔案、短交易、每次操作開關連線 —— 理由同 decisions.db(docs/adr/0001)。
Web UI 只讀這個檔;寫入端只有 ``jobfinder company review``。

``status`` 跟 ``review`` 分開存:重查中或重查失敗時,舊的評價照樣看得到。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .schema import CompanyReview, company_key

COMPANIES_DB = "companies.db"
#: 「查詢中」超過這麼久就當作卡死(排程逾時被砍、程序當掉),允許再查一次
RUNNING_STALE = timedelta(minutes=20)

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_reviews (
    company_key TEXT PRIMARY KEY,
    company     TEXT NOT NULL,      -- 查詢時用的顯示名
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    review      TEXT,               -- JSON(CompanyReview);最後一次成功的結果
    reviewed_at TEXT,               -- review 的查詢時間
    error       TEXT,
    model       TEXT,
    updated_at  TEXT NOT NULL       -- status 最後一次變動
);
"""


@dataclass(frozen=True)
class StoredCompany:
    key: str
    company: str
    status: str
    review: CompanyReview | None
    reviewed_at: datetime | None
    error: str | None
    model: str | None
    updated_at: datetime

    def running(self, now: datetime) -> bool:
        return self.status == "running" and now - self.updated_at < RUNNING_STALE

    def fresh(self, now: datetime, ttl_days: int) -> bool:
        return self.reviewed_at is not None and now - self.reviewed_at < timedelta(days=ttl_days)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


class CompanyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _upsert_status(self, name: str, status: str, now: datetime, **cols: str | None) -> None:
        sets = ", ".join(f"{c} = excluded.{c}" for c in ("company", "status", "updated_at", *cols))
        names = ("company_key", "company", "status", "updated_at", *cols)
        conn = _connect(self.path)
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO company_reviews ({', '.join(names)})"
                    f" VALUES ({', '.join('?' * len(names))})"
                    f" ON CONFLICT(company_key) DO UPDATE SET {sets}",
                    (company_key(name), name, status, _ts(now), *cols.values()),
                )
        finally:
            conn.close()

    def mark_running(self, name: str, now: datetime) -> None:
        self._upsert_status(name, "running", now)

    def save_ok(self, name: str, review: CompanyReview, model: str, now: datetime) -> None:
        self._upsert_status(
            name,
            "ok",
            now,
            review=review.model_dump_json(),
            reviewed_at=_ts(now),
            error=None,
            model=model,
        )

    def save_error(self, name: str, error: str, now: datetime) -> None:
        self._upsert_status(name, "error", now, error=error[:500])

    def all(self) -> dict[str, StoredCompany]:
        if not self.path.exists():
            return {}
        conn = _connect(self.path)
        try:
            rows = conn.execute("SELECT * FROM company_reviews").fetchall()
        finally:
            conn.close()
        return {
            r["company_key"]: StoredCompany(
                key=r["company_key"],
                company=r["company"],
                status=r["status"],
                review=CompanyReview.model_validate_json(r["review"]) if r["review"] else None,
                reviewed_at=datetime.fromisoformat(r["reviewed_at"]) if r["reviewed_at"] else None,
                error=r["error"],
                model=r["model"],
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for r in rows
        }
