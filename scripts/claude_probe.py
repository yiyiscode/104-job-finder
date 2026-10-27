"""用 Claude 訂閱額度(claude -p)重評 N 筆舊職缺,量額度消耗並對照原本的 Gemini 分數。

只讀 jobs.db 的快照、不連 104、不寫回 jobs.db。結果存 local_data/claude_probe/。

量額度的方式:CLI 在 -p 模式下拿不到「訂閱用了幾 %」,所以這裡記 token 與
API 等價成本(穩定、可外推);真正的 % 請在跑之前與之後各看一次 Claude Code 的
``/usage``,而且**期間不要用 Claude 做別的事**,否則差值會混進其他用量。

    python scripts/claude_probe.py --dry-run          # 只列出會評哪 10 筆
    python scripts/claude_probe.py                    # sonnet 深評 10 筆 + 粗篩 1 批
    python scripts/claude_probe.py --model haiku --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobfinder.config import load_config  # noqa: E402
from jobfinder.normalize import normalize_detail_response  # noqa: E402
from jobfinder.scoring.claude_cli import ClaudeCliClient  # noqa: E402
from jobfinder.scoring.resume import load_resume  # noqa: E402
from jobfinder.scoring.stage1 import screen  # noqa: E402
from jobfinder.scoring.stage2 import deep_score  # noqa: E402
from jobfinder.storage.repo import JobRepo  # noqa: E402
from jobfinder.webui.snapshot import connect_readonly, take_snapshot  # noqa: E402

#: 每筆職缺取「最近一次」深評當對照組;只挑有全文的,否則深評沒東西可看
PICK_SQL = """
SELECT j.job_no, j.job_name, j.cust_name, j.raw_summary, j.raw_detail, j.matched_keywords,
       s.total_score AS old_total, s.verdict AS old_verdict, s.model AS old_model
  FROM jobs j
  JOIN scores s ON s.job_no = j.job_no AND s.stage = 'deep'
 WHERE j.raw_detail IS NOT NULL
   AND s.id = (SELECT MAX(id) FROM scores WHERE job_no = j.job_no AND stage = 'deep')
 ORDER BY s.id DESC
 LIMIT ?
"""


def load_targets(db_path: Path, cache_dir: Path, limit: int):
    conn = connect_readonly(take_snapshot(db_path, cache_dir))
    try:
        rows = conn.execute(PICK_SQL, (limit,)).fetchall()
    finally:
        conn.close()
    targets = []
    for row in rows:
        summary = JobRepo._summary_from_raw(
            json.loads(row["raw_summary"]), json.loads(row["matched_keywords"] or "[]")
        )
        if summary is None:
            continue
        detail = normalize_detail_response(json.loads(row["raw_detail"]), row["job_no"])
        targets.append((summary, detail, dict(row)))
    return targets


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(ROOT / "config" / "config.yaml")
    data_dir = ROOT / "local_data"
    targets = load_targets(
        data_dir / cfg.paths.db_filename, data_dir / "claude_probe" / ".snapshot", args.limit
    )
    print(f"挑到 {len(targets)} 筆(有全文、有舊深評,由新到舊)")
    for summary, _, row in targets:
        print(
            f"  {summary.job_no:>10}  舊 {row['old_total']:>3}"
            f"  {summary.cust_name[:12]}  {summary.job_name[:30]}"
        )
    if args.dry_run or not targets:
        return 0

    resume = load_resume(ROOT / cfg.paths.resume, cfg.paths.references)
    llm = cfg.llm.model_copy(
        update={
            "deep": cfg.llm.deep.model_copy(update={"model": args.model}),
            "screen": cfg.llm.screen.model_copy(update={"model": args.screen_model}),
        }
    )
    client = ClaudeCliClient(effort=args.effort)
    results = []
    try:
        if not args.skip_screen:
            print(f"\n粗篩 1 批({len(targets)} 筆,{args.screen_model})…")
            outcome = await screen(client, llm, cfg.scoring, resume, [t[0] for t in targets])
            u = client.calls[-1]
            print(
                f"  留 {len(outcome.keep)} / 刷 {len(outcome.dropped)}"
                f"  in={u.input_tokens} out={u.output_tokens} ${u.cost_usd:.4f} {u.duration_s}s"
            )

        print(f"\n深評 {len(targets)} 筆({args.model},序列)…")
        for summary, detail, row in targets:
            n_before = len(client.calls)
            scored, cost, _ = await deep_score(client, llm, cfg.scoring, resume, summary, detail)
            usage = client.calls[n_before:]
            delta = scored.total - (row["old_total"] or 0)
            tokens_in = sum(
                u.input_tokens + u.cache_read_tokens + u.cache_creation_tokens for u in usage
            )
            print(
                f"  {summary.job_no:>10}  舊 {row['old_total']:>3} → 新 {scored.total:>3}"
                f" ({delta:+d})  in={tokens_in}"
                f" out={sum(u.output_tokens for u in usage)} ${cost:.4f}"
                f" {sum(u.duration_s for u in usage):.0f}s"
            )
            results.append(
                {
                    "job_no": summary.job_no,
                    "job_name": summary.job_name,
                    "cust_name": summary.cust_name,
                    "old_model": row["old_model"],
                    "old_total": row["old_total"],
                    "old_verdict": row["old_verdict"],
                    "new_total": scored.total,
                    "new_verdict": scored.verdict,
                    "one_liner": scored.one_liner,
                    "adjustments": scored.adjustments,
                }
            )
    finally:
        await client.aclose()
        out_dir = data_dir / "claude_probe"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"probe-{datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps({
            "model": args.model,
            "screen_model": None if args.skip_screen else args.screen_model,
            "effort": args.effort,
            "calls": [c.as_dict() for c in client.calls],
            "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")  # fmt: skip
        print(f"\n明細存到 {out.relative_to(ROOT)}")

    if deep_calls := [c for c in client.calls if c.model == args.model]:
        n = len(results) or 1
        cost = sum(c.cost_usd for c in deep_calls)
        print(
            f"\n深評平均每筆:${cost / n:.4f} 等價成本、"
            f"{sum(c.duration_s for c in deep_calls) / n:.0f}s;"
            f"總計 ${client.tracker.total:.4f}(含粗篩)"
        )
        diffs = [abs(r["new_total"] - (r["old_total"] or 0)) for r in results]
        if diffs:
            print(f"與舊分數平均絕對差 {sum(diffs) / len(diffs):.1f} 分(雜訊帶約 ±8)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model", default="sonnet", help="深評模型(claude CLI 的別名或完整 id)")
    parser.add_argument("--screen-model", default="haiku", help="粗篩模型")
    parser.add_argument("--skip-screen", action="store_true", help="只跑深評")
    parser.add_argument("--effort", default=None, help="claude --effort(low/medium/high)")
    parser.add_argument("--dry-run", action="store_true", help="只列出會評哪些,不呼叫 Claude")
    args = parser.parse_args()
    if not 1 <= args.limit <= 30:
        parser.error("--limit 必須在 1–30 之間(這是探測,不是重評)")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
