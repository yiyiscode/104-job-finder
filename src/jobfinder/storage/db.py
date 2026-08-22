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


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


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
