"""SQLite 連線與 Modal Volume 之間的檔案搬運。

**Volume 只當檔案倉庫。** Modal Volume 是網路檔案系統,不支援 POSIX 檔案鎖、
mmap 行為不保證,而 SQLite 的頁寫入正是隨機寫 —— 直接在 Volume 上開 db 會壞。

所以流程固定是:Volume → 本地暫存 → 全程對本地操作 → 原子換名寫回 → volume.commit()。

本機開發時 `volume=None`,commit 自動跳過,其餘行為完全相同 —— 不需要 `if IS_MODAL` 分支。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    # 不要 WAL:-shm 檔用 mmap,而這個 db 檔會被搬到 Volume 去
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


#: 舊 db 缺的欄位。``CREATE TABLE IF NOT EXISTS`` 對既有的表完全沒作用,
#: 所以新增欄位一定要走這裡 —— 否則舊 db 會在 UPDATE 時炸 "no such column"。
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("runs", "jobs_filtered_out", "INTEGER NOT NULL DEFAULT 0"),
)

#: ``jobs.status`` 的 CHECK 裡必須出現的值。SQLite **不能** ALTER 掉 CHECK 約束,
#: 所以放寬它只能整張表重建(見 :func:`_widen_jobs_status_check`)。
REQUIRED_STATUS_VALUES = ("filtered_out",)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    rebuilt = _apply_migrations(conn)
    if rebuilt:
        # 重建會把表連同它的索引一起丟掉,重跑一次 schema 把索引補回來。
        # schema.sql 全是 IF NOT EXISTS,重跑是安全的。
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def _apply_migrations(conn: sqlite3.Connection) -> bool:
    """回傳是否發生過整表重建(呼叫端要據此補回索引)。"""
    for table, column, decl in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            log.info("遷移:%s 補上欄位 %s", table, column)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return _widen_jobs_status_check(conn)


def _widen_jobs_status_check(conn: sqlite3.Connection) -> bool:
    """把 ``jobs.status`` 的 CHECK 放寬到含新的狀態值。

    SQLite 沒有 ``ALTER TABLE ... DROP CONSTRAINT``,放寬 CHECK 只能走
    「建新表 → 搬資料 → 丟舊表 → 改名」。兩個必須注意的地方:

    * ``scores.job_no`` 對 ``jobs`` 有 ``ON DELETE CASCADE``。**外鍵沒關掉就
      DROP TABLE jobs 會把 scores 整張連帶刪光。** 所以全程 ``foreign_keys=OFF``。
    * ``PRAGMA foreign_keys`` 在交易裡是 no-op,所以要在 BEGIN 之前設。

    舊資料**不回填** —— 過去的 ``screened_out`` 分不出是規則層還是 LLM 刷的,
    猜一個值比留著「分不出來」更糟。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None:
        return False
    current_sql = row[0] or ""
    if all(value in current_sql for value in REQUIRED_STATUS_VALUES):
        return False

    log.info("遷移:重建 jobs 表以放寬 status 的 CHECK(新增 %s)", ", ".join(REQUIRED_STATUS_VALUES))
    columns = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    column_list = ", ".join(columns)
    new_table = _jobs_ddl_from_schema().replace(
        "CREATE TABLE IF NOT EXISTS jobs (", "CREATE TABLE jobs_migrating (", 1
    )

    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scores_before = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.executescript(new_table)
        conn.execute(f"INSERT INTO jobs_migrating ({column_list}) SELECT {column_list} FROM jobs")
        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_migrating RENAME TO jobs")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scores_after = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    if after != before or scores_after != scores_before:
        raise RuntimeError(
            f"jobs 表重建後筆數不符:jobs {before}→{after}、scores {scores_before}→{scores_after}"
        )
    log.info("遷移完成:jobs %d 筆、scores %d 筆都在", after, scores_after)
    return True


def _jobs_ddl_from_schema() -> str:
    """從 schema.sql 取出 jobs 的建表語句 —— DDL 只留一份,不在程式裡複製一遍。"""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    start = text.index("CREATE TABLE IF NOT EXISTS jobs (")
    end = text.index(");", start) + 2
    return text[start:end]


class Database:
    """管理 db 檔在 Volume 與本地之間的生命週期。

    用法::

        with Database(data_dir, "jobs.db", volume=vol) as db:
            repo = JobRepo(db.conn)
            ...
            db.checkpoint()   # 重要節點顯式存檔
    """

    def __init__(
        self,
        data_dir: str | Path,
        filename: str = "jobs.db",
        *,
        volume: Any = None,
        local_dir: str | Path | None = None,
    ) -> None:
        self.remote_path = Path(data_dir) / filename
        self.volume = volume
        base = Path(local_dir) if local_dir else Path(tempfile.gettempdir()) / "jobfinder"
        base.mkdir(parents=True, exist_ok=True)
        # 檔名帶上倉庫路徑的雜湊,避免兩個不同的 data_dir 共用同一個暫存檔
        tag = hashlib.sha256(str(self.remote_path.resolve()).encode()).hexdigest()[:8]
        self.local_path = base / f"{Path(filename).stem}-{tag}{Path(filename).suffix}"
        self._conn: sqlite3.Connection | None = None

    # ── 生命週期 ────────────────────────────────────────────────────

    def open(self) -> Database:
        self.remote_path.parent.mkdir(parents=True, exist_ok=True)
        # 先清掉本地殘留。少了這步,倉庫端沒有 db 時會靜默沿用上一次跑剩的檔案 ——
        # 在 Modal 上就是「暖容器把已刪掉的舊狀態(含熔斷紀錄)復活」。
        self.local_path.unlink(missing_ok=True)
        if self.remote_path.exists():
            shutil.copy2(self.remote_path, self.local_path)
            log.debug("db 從 %s 複製到本地 %s", self.remote_path, self.local_path)
        self._conn = connect(self.local_path)
        ensure_schema(self._conn)
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database 尚未 open()")
        return self._conn

    def checkpoint(self) -> None:
        """把本地 db 寫回 Volume。

        呼叫時機(見 CLAUDE.md):抓取+入庫後、推播並標記完成後。
        不要在迴圈裡每筆呼叫 —— Volume commit 有網路成本。
        """
        if self._conn is None:
            return
        self._conn.commit()

        tmp = self.remote_path.with_suffix(self.remote_path.suffix + ".tmp")
        shutil.copy2(self.local_path, tmp)
        os.replace(tmp, self.remote_path)  # 原子換名,避免半寫入的 db 留在 Volume

        if self.volume is not None:
            self.volume.commit()
            log.debug("volume.commit() 完成")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # ── context manager ────────────────────────────────────────────

    def __enter__(self) -> Database:
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            # 即使發生例外也要存檔:已經抓到的資料與熔斷器狀態不能丟。
            # 熔斷狀態尤其重要 —— 丟了就會在冷卻期內又跑一次。
            self.checkpoint()
        except Exception:  # pragma: no cover - 存檔失敗不該蓋掉原本的例外
            log.exception("checkpoint 失敗")
        finally:
            self.close()
