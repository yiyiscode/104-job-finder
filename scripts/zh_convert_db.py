"""把已存進資料庫的 LLM 文字從簡體轉成台灣繁體(一次性補救,不連 104、不打 LLM)。

新的回應在解析時就會轉(``jobfinder.zh``),這支只處理 2026-10-15 以前存進去的。
預設只預覽;加 ``--apply`` 才寫入,寫入前先把三個 db 備份到 local_data/backup-zh-<時間>/。

不動 ``raw_response``:那是模型原始輸出,留著做稽核。
⚠️ 別在 08:00 排程跑的時候執行 —— pipeline 結束時會整檔蓋回 jobs.db。
寫入前會比對 mtime,期間檔案被改過就放棄。

    python scripts/zh_convert_db.py            # 預覽
    python scripts/zh_convert_db.py --apply    # 寫入
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobfinder.zh import has_simplified, to_traditional  # noqa: E402

#: (db 檔, 表, 要轉的欄位)
TARGETS = [
    ("jobs.db", "scores", ["one_liner", "highlights", "red_flags", "resume_tip"]),
    ("shortlist.db", "scores", ["one_liner", "highlights", "red_flags", "resume_tip"]),
    ("hitrate.db", "hitrates", ["requirements"]),
]


def plan(data_dir: Path) -> list[tuple[str, str, str, int, str, str]]:
    changes = []
    for db, table, cols in TARGETS:
        path = data_dir / db
        if not path.exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for col in cols:
            rows = conn.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL")
            for rowid, text in rows:
                if isinstance(text, str) and has_simplified(text):
                    new = to_traditional(text)
                    if new != text:
                        changes.append((db, table, col, rowid, text, new))
        conn.close()
    return changes


def apply(data_dir: Path, changes) -> None:
    backup = data_dir / f"backup-zh-{datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir()
    dbs = sorted({c[0] for c in changes})
    mtimes = {}
    for db in dbs:
        shutil.copy2(data_dir / db, backup / db)
        mtimes[db] = (data_dir / db).stat().st_mtime_ns
    print(f"已備份到 {backup}")

    for db in dbs:
        if (data_dir / db).stat().st_mtime_ns != mtimes[db]:
            sys.exit(f"{db} 在備份後被改過(排程在跑?),放棄寫入")
        conn = sqlite3.connect(data_dir / db)
        with conn:
            for _, table, col, rowid, _, new in (c for c in changes if c[0] == db):
                conn.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (new, rowid))
        conn.close()
        print(f"{db}:寫入 {sum(1 for c in changes if c[0] == db)} 個欄位")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=str(ROOT / "local_data"))
    parser.add_argument("--apply", action="store_true", help="真的寫入(預設只預覽)")
    parser.add_argument("--samples", type=int, default=5, help="預覽印幾筆前後對照")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = Path(args.data_dir)
    changes = plan(data_dir)
    counts: dict[tuple[str, str], int] = {}
    for db, _, col, *_ in changes:
        counts[(db, col)] = counts.get((db, col), 0) + 1
    print(f"含簡體、要轉換的欄位:{len(changes)} 個")
    for (db, col), n in sorted(counts.items()):
        print(f"  {db:<13} {col:<13} {n}")
    for db, _, col, rowid, old, new in changes[: args.samples]:
        print(f"\n[{db} {col} #{rowid}]\n  前:{old[:120]}\n  後:{new[:120]}")

    if args.apply and changes:
        apply(data_dir, changes)
    elif changes:
        print("\n只是預覽。確認沒問題後加 --apply 寫入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
