"""``shortlist.db``:投遞清單的成員與補抓回來的詳細頁。

跟 decisions.db 同一套原則(見 docs/adr/0001):

* 獨立檔案,**不寫 jobs.db** —— pipeline 會整檔覆蓋 jobs.db
* 短交易:每個操作自己開連線、提交、關閉
* 鍵是 **detail_id**(連結尾段):詳細頁 API 沒有 jobNo。pipeline 日後自己抓到同一筆時,
  UI 以連結尾段把兩邊對起來,jobs.db 那一筆優先
* 深評存在 ``scores``,欄位名跟 jobs.db 的 ``scores`` 一樣 —— UI 組列時同一套程式就能讀
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..webui.decisions import _Conn

if TYPE_CHECKING:
    from ..models import ScoredJob

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
CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    detail_id     TEXT NOT NULL,
    scored_at     TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    total_score   INTEGER NOT NULL,
    tech_fit      INTEGER, exp_fit INTEGER, domain_fit INTEGER,
    growth_fit    INTEGER, practical_fit INTEGER,
    verdict       TEXT,
    one_liner     TEXT,
    highlights    TEXT,                   -- JSON 陣列
    red_flags     TEXT,                   -- JSON 陣列
    resume_tip    TEXT,
    adjustments   TEXT,                   -- JSON 陣列
    raw_response  TEXT                    -- 模型原始回應,覺得分數怪時回頭看
);
CREATE INDEX IF NOT EXISTS idx_scores_detail ON scores (detail_id, id);
"""

#: ``latest_scores()`` 給 UI 的欄位 —— 名稱與 jobs.db 候選清單查詢的欄位一致
SCORE_FIELDS = (
    "total_score", "one_liner", "resume_tip", "highlights", "red_flags",
    "tech_fit", "exp_fit", "domain_fit", "growth_fit", "practical_fit",
    "verdict", "adjustments",
)  # fmt: skip

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
        """還需要抓的:jobs.db 沒有全文(``known`` = 已有全文的)、沒成功抓過、104 也沒說已關閉。"""
        fetches = self.latest_fetches()
        return [
            e
            for e in self.entries()
            if e.detail_id not in known
            and not ((f := fetches.get(e.detail_id)) and (f.raw_detail or f.gone))
        ]

    def record_score(
        self, detail_id: str, scored: ScoredJob, raw_response: str, now: datetime
    ) -> None:
        """只追加:重評會多一列,UI 取最新的。"""
        b = scored.breakdown
        dumps = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
        with _Conn(self.path) as conn:
            conn.execute(
                "INSERT INTO scores (detail_id, scored_at, model, total_score, tech_fit,"
                " exp_fit, domain_fit, growth_fit, practical_fit, verdict, one_liner,"
                " highlights, red_flags, resume_tip, adjustments, raw_response)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    detail_id, now.isoformat(timespec="seconds"), scored.model_used,
                    scored.total, b.tech_fit, b.exp_fit, b.domain_fit, b.growth_fit,
                    b.practical_fit, scored.verdict, scored.one_liner,
                    dumps(scored.highlights), dumps(scored.red_flags), scored.resume_tip,
                    dumps(scored.adjustments), raw_response,
                ),
            )  # fmt: skip

    def latest_scores(self) -> dict[str, dict[str, Any]]:
        """detail_id → 最新一次深評(``SCORE_FIELDS`` + ``score_model``)。"""
        with _Conn(self.path) as conn:
            rows = conn.execute(
                f"SELECT detail_id, model AS score_model, {', '.join(SCORE_FIELDS)}"
                " FROM scores ORDER BY id"
            ).fetchall()
        return {r["detail_id"]: {k: r[k] for k in ("score_model", *SCORE_FIELDS)} for r in rows}

    def to_score(self, known: set[str], rescore: bool = False) -> list[Entry]:
        """有全文、不在 jobs.db(那邊由 pipeline 評)、還沒深評過的。``rescore`` 連評過的也算。"""
        fetches, scored = self.latest_fetches(), set() if rescore else set(self.latest_scores())
        return [
            e
            for e in self.entries()
            if e.detail_id not in known
            and e.detail_id not in scored
            and (f := fetches.get(e.detail_id)) is not None
            and f.raw_detail
        ]
