"""用 Claude 訂閱額度(claude -p)重評舊職缺 —— 只限有全文(raw_detail)的。

拆成兩段,刻意不讓長時間的評分握著 jobs.db:

  run    逐筆評分,結果**追加**到 local_data/claude_rescore/results.jsonl。
         可隨時中斷,再跑會跳過已完成的。只讀 jobs.db 快照、不連 104。
  apply  把 JSONL 匯入 jobs.db 的 scores 表(新增一列,run_id 為 NULL;
         舊的 Gemini 分數保留)。UI 取每筆最新的 deep 分數,所以匯入即生效。
         只花幾秒;寫回前若發現 jobs.db 被排程動過就放棄,不互相覆蓋。
  status 已完成 / 待評 / 已匯入。

為什麼不直接在 run 裡寫 jobs.db:一批要跑約 2 小時,碰上 08:00 排程時
兩邊都會把自己的副本 os.replace 回去,後寫的把先寫的整個蓋掉。

    python scripts/claude_rescore.py status
    python scripts/claude_rescore.py run --limit 135     # 一天約週額度 13%
    python scripts/claude_rescore.py apply --dry-run
    python scripts/claude_rescore.py apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobfinder.config import load_config  # noqa: E402
from jobfinder.models import ScoredJob  # noqa: E402
from jobfinder.normalize import normalize_detail_response  # noqa: E402
from jobfinder.scoring.claude_cli import ClaudeCliClient, ClaudeQuotaExhausted  # noqa: E402
from jobfinder.scoring.resume import load_resume  # noqa: E402
from jobfinder.scoring.stage2 import deep_score  # noqa: E402
from jobfinder.storage.db import Database  # noqa: E402
from jobfinder.storage.repo import JobRepo  # noqa: E402
from jobfinder.webui.snapshot import connect_readonly, take_snapshot  # noqa: E402

OUT_DIR = ROOT / "local_data" / "claude_rescore"
RESULTS = OUT_DIR / "results.jsonl"
#: 連續失敗幾筆就停 —— 多半是 CLI 本身壞了,不是個別職缺的問題
MAX_CONSECUTIVE_FAILURES = 3

#: 排除已經有 Claude 深評的 —— 切換後每日排程評的新職缺本來就是 Claude 口徑
TARGET_SQL = """
SELECT job_no, raw_summary, raw_detail, matched_keywords
  FROM jobs j
 WHERE raw_detail IS NOT NULL AND raw_summary IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM scores s
        WHERE s.job_no = j.job_no AND s.stage = 'deep' AND s.model LIKE 'claude-cli:%'
   )
 ORDER BY first_seen_at DESC
"""
#: 暫時性錯誤(API 忙碌/逾時)等一下再試一次。2026-10-07 夜間 145 筆裡失敗 7 筆,
#: 事後單獨重跑同一筆就成功
RETRY_DELAY_SECONDS = 60

log = logging.getLogger("claude_rescore")


def load_done() -> dict[str, dict]:
    if not RESULTS.exists():
        return {}
    done = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            done[rec["job_no"]] = rec
    return done


def load_targets(cfg) -> list:
    db_path = ROOT / "local_data" / cfg.paths.db_filename
    conn = connect_readonly(take_snapshot(db_path, OUT_DIR / ".snapshot"))
    try:
        rows = conn.execute(TARGET_SQL).fetchall()
    finally:
        conn.close()
    targets = []
    for row in rows:
        summary = JobRepo._summary_from_raw(
            json.loads(row["raw_summary"]), json.loads(row["matched_keywords"] or "[]")
        )
        if summary is not None:
            targets.append((row["job_no"], summary, row["raw_detail"]))
    return targets


def _prevent_sleep() -> None:
    """跑的期間阻止 Windows 閒置睡眠(螢幕仍會關)。程序結束即自動解除,不改電源設定。

    睡著時程序只是暫停,但進行中的 claude -p 連線會斷,醒來容易連續失敗而整批停下;
    而且睡著的時間完全沒在跑 —— 一批 200 筆要 3 個多小時,閒置 15 分鐘就睡的電腦跑不完。
    """
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


async def cmd_run(args, cfg) -> int:
    _prevent_sleep()
    done = load_done()
    pending = [t for t in load_targets(cfg) if t[0] not in done]
    batch = pending[: args.limit]
    print(f"有全文 {len(done) + len(pending)} 筆:已完成 {len(done)}、待評 {len(pending)}")
    print(f"這次評 {len(batch)} 筆({cfg.llm.claude.deep_model},並行 {args.concurrency})")
    if not batch:
        return 0

    resume = load_resume(ROOT / cfg.paths.resume, cfg.paths.references)
    llm = cfg.llm.model_copy(
        update={"deep": cfg.llm.deep.model_copy(update={"model": cfg.llm.claude.deep_model})}
    )
    # 每個 worker 各一個 client:共用的話 calls[n_before:] 會切到別的 worker 的呼叫,
    # usage 被重複計算(實測並行 2 時 $0.11 記成 $0.17)
    clients = [
        ClaudeCliClient(timeout_seconds=cfg.llm.claude.timeout_seconds, max_concurrent=1)
        for _ in range(args.concurrency)
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queue: asyncio.Queue = asyncio.Queue()
    for item in batch:
        queue.put_nowait(item)
    state = {"ok": 0, "failed": 0, "consecutive": 0, "stop": None}

    async def worker(client: ClaudeCliClient) -> None:
        while not queue.empty() and state["stop"] is None:
            job_no, summary, raw_detail = queue.get_nowait()
            detail = normalize_detail_response(json.loads(raw_detail), job_no)
            n_before = len(client.calls)
            try:
                try:
                    scored, _, raw = await deep_score(
                        client, llm, cfg.scoring, resume, summary, detail
                    )
                except ClaudeQuotaExhausted:
                    raise
                except Exception as first:  # noqa: BLE001
                    print(f"  … {job_no} 失敗,{RETRY_DELAY_SECONDS}s 後重試:{str(first)[:200]}")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    scored, _, raw = await deep_score(
                        client, llm, cfg.scoring, resume, summary, detail
                    )
            except ClaudeQuotaExhausted as exc:
                state["stop"] = f"額度用完:{exc}"
                return
            except Exception as exc:  # noqa: BLE001 —— 重試後仍失敗:記下來繼續
                state["failed"] += 1
                state["consecutive"] += 1
                print(f"  ✗ {job_no} 重試後仍失敗:{str(exc)[:300]}")
                if state["consecutive"] >= MAX_CONSECUTIVE_FAILURES:
                    state["stop"] = f"連續 {MAX_CONSECUTIVE_FAILURES} 筆失敗"
                continue
            state["ok"] += 1
            state["consecutive"] = 0
            usage = [c.as_dict() for c in client.calls[n_before:]]
            # detail 含整份原始 JSON,jobs.db 已經有了,這裡不必再存一份
            record = {
                "job_no": job_no,
                "scored_at": datetime.now().isoformat(timespec="seconds"),
                "scored": json.loads(scored.model_copy(update={"detail": None}).model_dump_json()),
                "raw_response": raw,
                "usage": usage,
            }
            with RESULTS.open("a", encoding="utf-8") as f:  # 每筆立刻落地,中斷不丟
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            done_n = state["ok"] + state["failed"]
            print(f"  [{done_n}/{len(batch)}] {job_no} → {scored.total}  {summary.job_name[:28]}")

    await asyncio.gather(*(worker(c) for c in clients))

    cost = sum(call.cost_usd for c in clients for call in c.calls)
    print(f"\n完成 {state['ok']}、失敗 {state['failed']};等價成本 ${cost:.2f}")
    if state["stop"]:
        print(f"⚠️ 提前停止:{state['stop']}")
        return 1
    return 0


def applied_job_nos(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT job_no FROM scores WHERE stage = 'deep' AND model LIKE 'claude-cli:%'"
        )
    }


def cmd_apply(args, cfg) -> int:
    done = load_done()
    db = Database(ROOT / "local_data", cfg.paths.db_filename)
    mtime_before = db.remote_path.stat().st_mtime_ns
    db.open()
    try:
        repo = JobRepo(db.conn, cfg.dedupe)
        existing = {r[0] for r in db.conn.execute("SELECT job_no FROM jobs")}
        already = applied_job_nos(db.conn)
        todo = [rec for no, rec in done.items() if no in existing and no not in already]
        print(f"JSONL {len(done)} 筆;已匯入 {len(already)};這次要匯入 {len(todo)}")
        if args.dry_run or not todo:
            return 0
        for rec in todo:
            repo.save_score(
                ScoredJob.model_validate(rec["scored"]),
                run_id=None,
                raw_response=rec["raw_response"],
                now=datetime.fromisoformat(rec["scored_at"]),
            )
        # 期間排程動過 jobs.db 的話,這份副本是舊的 —— 寫回去會蓋掉排程的成果
        if db.remote_path.stat().st_mtime_ns != mtime_before:
            print("✗ jobs.db 在匯入期間被改過(排程在跑?),放棄寫回。稍後再跑一次 apply。")
            return 1
        db.checkpoint()
        print(f"✓ 已匯入 {len(todo)} 筆")
        return 0
    finally:
        db.close()


def cmd_status(args, cfg) -> int:
    done = load_done()
    targets = load_targets(cfg)
    pending = sum(1 for t in targets if t[0] not in done)
    conn = connect_readonly(
        take_snapshot(ROOT / "local_data" / cfg.paths.db_filename, OUT_DIR / ".snapshot")
    )
    try:
        applied = len(applied_job_nos(conn))
    finally:
        conn.close()
    cost = sum(u["cost_usd"] for rec in done.values() for u in rec.get("usage", []))
    print(
        f"尚無 Claude 深評的有全文職缺 {len(targets)} 筆"
        f"(其中 {len(targets) - pending} 筆已評、待 apply);"
        f"JSONL 共 {len(done)} 筆;待評 {pending};已匯入 jobs.db {applied}"
    )
    print(f"累計等價成本 ${cost:.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="評分並追加到 results.jsonl")
    run.add_argument("--limit", type=int, required=True, help="這次最多評幾筆(1–200)")
    run.add_argument("--concurrency", type=int, default=1, help="並行數(1–2)")
    apply = sub.add_parser("apply", help="把 results.jsonl 匯入 jobs.db")
    apply.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="進度")
    args = parser.parse_args()

    if args.cmd == "run":
        if not 1 <= args.limit <= 200:
            parser.error("--limit 必須在 1–200 之間(一天約 135 筆 ≈ 週額度 13%)")
        if not 1 <= args.concurrency <= 2:
            parser.error("--concurrency 只能 1 或 2 —— 訂閱有速率限制")

    logging.basicConfig(level=logging.WARNING)
    cfg = load_config(ROOT / "config" / "config.yaml")
    if args.cmd == "run":
        return asyncio.run(cmd_run(args, cfg))
    if args.cmd == "apply":
        return cmd_apply(args, cfg)
    return cmd_status(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
