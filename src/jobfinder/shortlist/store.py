"""``shortlist.db``:投遞清單的成員與補抓回來的詳細頁。

跟 decisions.db 同一套原則(見 docs/adr/0001):

* 獨立檔案,**不寫 jobs.db** —— pipeline 會整檔覆蓋 jobs.db
* 短交易:每個操作自己開連線、提交、關閉
* 鍵是 **detail_id**(連結尾段):詳細頁 API 沒有 jobNo。pipeline 日後自己抓到同一筆時,
  UI 以連結尾段把兩邊對起來,jobs.db 那一筆優先
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..webui.decisions import _Conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    detail_id TEXT PRIMARY KEY,
    company   TEXT NOT NULL DEFAULT '',   -- 清單上寫的,還沒抓到詳細頁時顯示用
    title     TEXT NOT NULL DEFAULT '',
    added_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    detail_id  TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_detail TEXT,                      -- 成功:詳細頁原始 JSON
    error      TEXT,                      -- 失敗原因
    gone       INTEGER NOT NULL DEFAULT 0, -- 1 = 104 說職務不存在,不再重抓
    CHECK ((raw_detail IS NULL) != (error IS NULL)),
    CHECK (gone IN (0, 1) AND (gone = 0 OR error IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_fetches_detail ON fetches (detail_id, id);
"""

SHORTLIST_DB = "shortlist.db"

#: 104 的 detail_id:小寫英數,4–8 碼(實測 7j5sn、94234、8ve8x)
DETAIL_ID_RX = re.compile(r"[0-9a-z]{4,8}")


@dataclass(frozen=True)
class Entry:
    detail_id: str
    company: str
    title: str
    added_at: str


@dataclass(frozen=True)
class Fetch:
    detail_id: str
    fetched_at: str
    raw_detail: str | None
    error: str | None
    gone: bool


class ShortlistStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with _Conn(path) as conn:
            conn.executescript(SCHEMA)

    def add(self, detail_id: str, company: str, title: str, now: datetime) -> bool:
        """加進清單;已經在的只更新公司/職稱。回傳是否為新加入。"""
        if not DETAIL_ID_RX.fullmatch(detail_id):
            raise ValueError(f"不像 104 的職缺代碼:{detail_id!r}(要的是連結尾段,例如 7j5sn)")
        with _Conn(self.path) as conn:
            existed = conn.execute(
                "SELECT 1 FROM entries WHERE detail_id = ?", (detail_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO entries (detail_id, company, title, added_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (detail_id) DO UPDATE SET company = excluded.company,"
                " title = excluded.title",
                (detail_id, company.strip(), title.strip(), now.isoformat(timespec="seconds")),
            )
        return existed is None

    def entries(self) -> list[Entry]:
        with _Conn(self.path) as conn:
            # rowid = 加入順序,也就是清單上的排序(週次內的先後)
            rows = conn.execute("SELECT * FROM entries ORDER BY rowid").fetchall()
        return [Entry(**dict(r)) for r in rows]

    def record_fetch(
        self,
        detail_id: str,
        now: datetime,
        *,
        raw_detail: str | None = None,
        error: str | None = None,
        gone: bool = False,
    ) -> None:
        with _Conn(self.path) as conn:
            conn.execute(
                "INSERT INTO fetches (detail_id, fetched_at, raw_detail, error, gone)"
                " VALUES (?, ?, ?, ?, ?)",
                (detail_id, now.isoformat(timespec="seconds"), raw_detail, error, int(gone)),
            )

    def latest_fetches(self) -> dict[str, Fetch]:
        """每筆最新一次的抓取。成功過的優先 —— 之後一次暫時失敗不該讓已有的全文消失。"""
        with _Conn(self.path) as conn:
            rows = conn.execute(
                "SELECT detail_id, fetched_at, raw_detail, error, gone FROM fetches"
                " ORDER BY (raw_detail IS NOT NULL), id"
            ).fetchall()
        return {r["detail_id"]: Fetch(**{**dict(r), "gone": bool(r["gone"])}) for r in rows}

    def to_fetch(self, known: set[str]) -> list[Entry]:
        """還需要抓的:不在 jobs.db(``known``)、沒成功抓過、104 也沒說已關閉。"""
        fetches = self.latest_fetches()
        return [
            e
            for e in self.entries()
            if e.detail_id not in known
            and not ((f := fetches.get(e.detail_id)) and (f.raw_detail or f.gone))
        ]
