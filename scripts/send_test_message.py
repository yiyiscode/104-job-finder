"""Telegram 連通性測試。

先確認這條路通,再去碰 104 —— 因為被擋時的告警就是走這條路送出來的。
告警管道自己壞掉的話,你只會看到「今天沒收到日報」,卻不知道為什麼。

    python scripts/send_test_message.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobfinder.config import Secrets, load_config  # noqa: E402
from jobfinder.errors import JobFinderError  # noqa: E402
from jobfinder.logging_conf import setup_logging, use_utf8_console  # noqa: E402
from jobfinder.models import JobSummary, ScoreBreakdown, ScoredJob  # noqa: E402
from jobfinder.notify.telegram import TelegramNotifier  # noqa: E402

SAMPLE = ScoredJob(
    summary=JobSummary(
        job_no="sample",
        # 詳細頁 API 只吃 detail_id(連結尾段),與 job_no 是不同的東西
        detail_id="94234",
        # 刻意放進 ( ) & < 這些字元:這正是 MarkdownV2 會炸掉、HTML 必須跳脫的地方
        job_name="AI工程師(LLM應用) <測試> & 驗證",
        cust_name="示範金融科技股份有限公司",
        cust_no="cust001",
        job_url="https://www.104.com.tw/jobs/search/",
        area_desc="台北市南港區",
        salary_desc="月薪 60,000~90,000 元",
        edu_desc="碩士以上",
        period_desc="經歷不拘",
        matched_keywords=["AI工程師", "LLM工程師"],
    ),
    breakdown=ScoreBreakdown(
        tech_fit=32, exp_fit=22, domain_fit=14, growth_fit=12, practical_fit=7
    ),
    total=87,
    verdict="strong_apply",
    one_liner="這是一則測試訊息,用來確認排版、跳脫與按鈕都正常。",
    highlights=[
        "如果你看得到這三點,HTML 跳脫沒問題",
        "標題裡的 < > & ( ) 都應該正常顯示",
        "下方應該有兩顆可點的按鈕",
    ],
    red_flags=["這不是真的職缺,不要投"],
    resume_tip="收到這則訊息就表示 Telegram 設定完成了。",
    model_used="test",
)


async def send(dry_run: bool = False) -> int:
    cfg = load_config("config/config.yaml")

    if dry_run:
        from jobfinder.notify.format import render_alert, render_job_card, strip_tags

        print(strip_tags(render_alert("Job Finder 連線測試", ["(dry-run,沒有真的送出)"])))
        print()
        print("─" * 60)
        print(strip_tags(render_job_card(SAMPLE, threshold=cfg.scoring.threshold)[0]))
        print()
        print("(dry-run:以上只是渲染結果,一則訊息都沒有送出)")
        return 0

    secrets = Secrets()
    secrets.require_all()

    notifier = TelegramNotifier(
        secrets.telegram_bot_token,
        secrets.telegram_chat_id,
        notify=cfg.notify,
        scoring=cfg.scoring,
    )

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await notifier.send_alert(
        "Job Finder 連線測試",
        [
            f"時間:{stamp}",
            f"門檻:{cfg.scoring.threshold} 分,每日上限 {cfg.scoring.max_per_day} 則",
            f"關鍵字:{'、'.join(cfg.search.keywords)}",
            "",
            "接下來會再送一則職缺卡片範例。",
        ],
    )
    await notifier.send_job(SAMPLE)

    print("✅ 已送出兩則訊息,請到 Telegram 確認:")
    print("   1. 一則告警格式的訊息")
    print("   2. 一則職缺卡片(標題含 < > & 應正常顯示,底下有兩顆按鈕)")
    return 0


def main() -> int:
    # ⚠️ 一定要先 parse。少了 argparse 的話,`--help` 不會印說明而是「把整個腳本跑一遍」——
    #    這支腳本會真的送 Telegram 訊息,曾經因此一口氣重複送了 6 次。
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只渲染訊息內容印到終端,不真的送出(不需要 .env)",
    )
    args = parser.parse_args()

    use_utf8_console()
    setup_logging("INFO")
    try:
        return asyncio.run(send(args.dry_run))
    except JobFinderError as exc:
        print(f"❌ {exc}")
        print(
            "\n設定步驟:"
            "\n 1. Telegram 搜尋 @BotFather → /newbot → 取得 TELEGRAM_BOT_TOKEN"
            "\n 2. 【重要】主動對你的新 bot 送一則訊息,否則 bot 無法主動發訊給你"
            "\n 3. 瀏覽器開 https://api.telegram.org/bot<TOKEN>/getUpdates"
            "\n    從 result[0].message.chat.id 取得 TELEGRAM_CHAT_ID"
            "\n 4. 複製 .env.example 成 .env 並填入"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
