"""不打 API 的補算路徑:匯出待算職缺 → 人(或 Claude Code)判讀 → 匯入。

使用者要求舊資料由 Claude Code 直接判讀補齊,不額外呼叫 OpenRouter。
匯入時走跟 API 同一條路:schema 驗證 → 截斷 → **程式算分** → 存進 hitrate.db,
``model`` 另外標記,UI 上看得出哪些是 API 算的、哪些是手動判讀的。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from ..webui.rows import JobRow
from .compute import MAX_REQUIREMENTS, HitRateCheck, hit_rate
from .store import HitRateStore

MANUAL_MODEL = "claude-code"
#: 每個匯出檔的職缺數 —— 一次讀得完、判讀品質不掉
EXPORT_BATCH = 15
#: JD 太長的截斷點。條件幾乎都在前段,後段多是公司介紹與福利
MAX_JD_CHARS = 2500


def export_pending(targets: list[JobRow], out_dir: Path) -> list[Path]:
    """把待算職缺寫成 Markdown 批次檔,回傳檔案路徑。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, start in enumerate(range(0, len(targets), EXPORT_BATCH), start=1):
        batch = targets[start : start + EXPORT_BATCH]
        lines = []
        for r in batch:
            jd = (r.detail_text or "").strip()
            if len(jd) > MAX_JD_CHARS:
                jd = jd[:MAX_JD_CHARS] + "\n…(截斷)"
            lines += [f"### {r.job_no} | {r.company} | {r.title}", "", jd, "", "---", ""]
        path = out_dir / f"pending_{n:03d}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


def import_judgments(
    path: Path, store: HitRateStore, resume_hash: str, now: datetime
) -> tuple[int, list[str]]:
    """匯入 ``[{"job_no": ..., "requirements": [...]}, ...]``。回傳 (成功筆數, 錯誤訊息)。

    單筆格式錯誤只跳過那一筆,不中斷整批。
    """
    items = json.loads(path.read_text(encoding="utf-8"))
    ok, errors = 0, []
    for item in items:
        job_no = str(item.get("job_no", "")).strip()
        try:
            check = HitRateCheck(requirements=item.get("requirements", []))
        except ValidationError as exc:
            errors.append(f"{job_no or '?'}: {exc.error_count()} 個欄位錯誤")
            continue
        if not job_no:
            errors.append("缺 job_no")
            continue
        requirements = check.requirements[:MAX_REQUIREMENTS]
        store.save(
            job_no, resume_hash, hit_rate(requirements), requirements, MANUAL_MODEL, 0.0, now
        )
        ok += 1
    return ok, errors
