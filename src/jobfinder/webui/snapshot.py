"""jobs.db 的唯讀快照。

為什麼不直接開 jobs.db(見 docs/adr/0001):pipeline 的 ``Database`` 會把 jobs.db
整個複製到暫存區操作,結束時 ``os.replace`` 蓋回來。在 Windows 上,只要 UI 還開著
那個檔案,``os.replace`` 就會失敗 —— 而 checkpoint 失敗只記 log,當次抓到的資料與
熔斷器狀態會一起遺失。所以 UI 只在「檔案變了」的時候花幾毫秒複製一份,之後全讀副本。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


def take_snapshot(db_path: Path, cache_dir: Path) -> Path:
    """回傳一份與 ``db_path`` 目前內容相同的副本路徑。

    副本以 (mtime, size) 命名 —— 沒變就重用,不重複讀原檔。
    pipeline 正在 checkpoint 時(``jobs.db.tmp`` 存在)不去碰原檔,沿用上一份副本。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(cache_dir.glob("jobs-*.db"), key=lambda p: p.stat().st_mtime)

    checkpoint_tmp = db_path.with_suffix(db_path.suffix + ".tmp")
    if checkpoint_tmp.exists() and existing:
        return existing[-1]
    if not db_path.exists():
        raise FileNotFoundError(f"找不到 {db_path} —— pipeline 還沒跑過?")

    stat = db_path.stat()
    snap = cache_dir / f"jobs-{stat.st_mtime_ns}-{stat.st_size}.db"
    if not snap.exists():
        partial = snap.with_suffix(".partial")
        shutil.copyfile(db_path, partial)  # 讀完就關,原檔只被持有這幾毫秒
        partial.replace(snap)
    for old in existing:
        if old != snap:
            old.unlink(missing_ok=True)
    return snap


def connect_readonly(snapshot: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
