"""使用者的「投／不投／待確認」標記。存在獨立的 decisions.db,**不在 jobs.db**。

* 獨立檔案:pipeline 會整檔覆蓋 jobs.db,也會 prune + CASCADE(見 docs/adr/0001)
* 只追加:改判也是新增一列,UI 取最新。「待確認 → 投」的軌跡是之後檢討閘門的資料
* 短交易:每個操作自己開連線、提交、關閉,不長期持有檔案
* 「不投必填原因」由 DB 的 CHECK 與 ``append`` 的驗證兩邊一起擋
* 「想去程度」1–5 存在**另一張表** ``desires``:選填、跟投/不投各自獨立(可以只評分不標記),
  之後拿來分析偏好 —— 例如「很想去卻不投」的落差。同樣只追加、取最新
* 「排定週次」(``plans``)與「送出日期」(``submissions``):已標投遞頁的待辦用。
  同樣只追加、取最新;值為 NULL 的一列代表「取消」。回覆／面試不記在這裡(在 Notion)

**鍵**:pipeline 抓到的職缺用 ``job_no``;只在投遞清單裡的用 **detail_id**(詳細頁 API
沒有 jobNo)。pipeline 日後抓到同一筆時,讀取端傳 ``aliases``(detail_id → job_no)
把兩個鍵的紀錄合在一起,依寫入順序取最新 —— 不必搬資料。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
CREATE TABLE IF NOT EXISTS desires (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no   TEXT NOT NULL,
    desire   INTEGER NOT NULL CHECK (desire BETWEEN 1 AND 5),
    rated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desires_job ON desires (job_no, id);
CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no     TEXT NOT NULL,
    week_start TEXT,            -- 該週週一(YYYY-MM-DD);NULL = 取消排定
    planned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no      TEXT NOT NULL,
    sent_on     TEXT,           -- 實際送出的日期;NULL = 撤銷(勾錯了)
    recorded_at TEXT NOT NULL
);
"""

#: 想去程度的刻度。「未評」不存 —— 沒拉就不算給了分數,不能被當成 3 分
DESIRE_MIN, DESIRE_MAX = 1, 5
DESIRE_LABELS = {1: "1 不太想", 2: "2", 3: "3 普通", 4: "4", 5: "5 很想去"}


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

    def rate(self, job_no: str, desire: int, now: datetime) -> None:
        if not DESIRE_MIN <= desire <= DESIRE_MAX:
            raise ValueError(f"想去程度要在 {DESIRE_MIN}–{DESIRE_MAX} 之間")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO desires (job_no, desire, rated_at) VALUES (?, ?, ?)",
                (job_no, desire, now.isoformat(timespec="seconds")),
            )

    def plan(self, job_no: str, week_start: date | None, now: datetime) -> None:
        """排進某一週(傳該週週一);``None`` = 取消排定。"""
        if week_start is not None and week_start.weekday() != 0:
            raise ValueError(f"週次要用週一的日期,{week_start} 是週{week_start.isoweekday()}")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO plans (job_no, week_start, planned_at) VALUES (?, ?, ?)",
                (job_no, _iso(week_start), now.isoformat(timespec="seconds")),
            )

    def mark_sent(self, job_no: str, sent_on: date | None, now: datetime) -> None:
        """記下實際送出的日期;``None`` = 撤銷。"""
        if sent_on is not None and sent_on > now.date():
            raise ValueError(f"送出日期 {sent_on} 在未來 —— 還沒送的請用「排定週次」")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO submissions (job_no, sent_on, recorded_at) VALUES (?, ?, ?)",
                (job_no, _iso(sent_on), now.isoformat(timespec="seconds")),
            )

    # ── 讀取:全部依寫入順序折疊,最後一筆有效 ──

    def latest(self, aliases: Mapping[str, str] | None = None) -> dict[str, Decision]:
        rows = self._all("SELECT * FROM decisions ORDER BY id")
        return _fold(((r["job_no"], Decision(**dict(r))) for r in rows), aliases)

    def latest_desires(self, aliases: Mapping[str, str] | None = None) -> dict[str, int]:
        rows = self._all("SELECT job_no, desire FROM desires ORDER BY id")
        return _fold(((r["job_no"], r["desire"]) for r in rows), aliases)

    def latest_plans(self, aliases: Mapping[str, str] | None = None) -> dict[str, date]:
        rows = self._all("SELECT job_no, week_start FROM plans ORDER BY id")
        return _dates(_fold(((r["job_no"], r["week_start"]) for r in rows), aliases))

    def latest_sent(self, aliases: Mapping[str, str] | None = None) -> dict[str, date]:
        rows = self._all("SELECT job_no, sent_on FROM submissions ORDER BY id")
        return _dates(_fold(((r["job_no"], r["sent_on"]) for r in rows), aliases))

    def history(self, job_no: str, *also: str) -> list[Decision]:
        """``also``:同一筆職缺的其他鍵(例如投遞清單時期用的 detail_id)。"""
        keys = (job_no, *also)
        marks = ", ".join("?" * len(keys))
        rows = self._all(f"SELECT * FROM decisions WHERE job_no IN ({marks}) ORDER BY id", keys)
        return [Decision(**dict(r)) for r in rows]

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()


def _fold(items: Iterable[tuple[str, Any]], aliases: Mapping[str, str] | None) -> dict[str, Any]:
    aliases = aliases or {}
    out: dict[str, Any] = {}
    for key, value in items:
        out[aliases.get(key, key)] = value
    return out


def _dates(values: Mapping[str, str | None]) -> dict[str, date]:
    """去掉「取消」(NULL),其餘轉成 date。"""
    return {k: date.fromisoformat(v) for k, v in values.items() if v}


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


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
