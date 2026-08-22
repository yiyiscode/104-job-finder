"""建立乾淨的瀏覽器 profile,並人工看著它通過一次 Cloudflare 挑戰。

⚠️ **過程中絕對不要登入你的 104 帳號。** 腳本結束前會檢查,偵測到登入態就把整個
profile 刪掉並要求重做(SPEC.md 規則 9)。

這個 profile 是專案專用的,存在 `local_data/chrome-profile`,與你日常用的 Chrome
完全無關 —— 那裡面有你真實的 104 登入 cookie,絕不能沿用。

    python scripts/bootstrap_profile.py

順帶一提:cf_clearance 綁 client IP,所以這份 profile 上傳到 Modal 之後,
Cloudflare 憑證那部分幾乎一定會失效。它的價值在於「有正常瀏覽歷史的 profile 結構」,
真正過挑戰還是靠每次執行時的 warmup。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobfinder.config import load_config  # noqa: E402
from jobfinder.errors import JobFinderError, LoggedInDetected  # noqa: E402
from jobfinder.logging_conf import setup_logging, use_utf8_console  # noqa: E402
from jobfinder.scrape.blocking import assert_anonymous, find_identity_cookies  # noqa: E402
from jobfinder.scrape.browser import browser_context  # noqa: E402
from jobfinder.scrape.budget import RequestBudget  # noqa: E402

DATA_DIR = Path("local_data")


async def bootstrap() -> int:
    cfg = load_config("config/config.yaml")
    budget = RequestBudget(cfg.scrape)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    async with browser_context(cfg, budget, data_dir=DATA_DIR, headless=False) as context:
        # browser_context 內部已經跑過 warmup 與 assert_anonymous 了,
        # 走到這裡就代表挑戰通過、而且是匿名狀態。
        cookies = await context.cookies()
        names = sorted({c.get("name", "?") for c in cookies})
        print(f"\n✅ 已通過 Cloudflare。目前 cookie:{', '.join(names) or '(無)'}")

        identity = find_identity_cookies(cookies)
        if identity:
            raise LoggedInDetected(f"偵測到身分 cookie:{', '.join(identity)}")

        await assert_anonymous(context)

    print(
        f"\n✅ profile 已建立並打包。\n"
        f"   - {DATA_DIR / cfg.paths.profile_tarball}\n"
        f"   - {DATA_DIR / cfg.paths.storage_state}(只含 Cloudflare 憑證,已濾掉身分 cookie)\n"
        f"\n上傳到 Modal:\n"
        f"   modal volume put job-finder-data"
        f" {DATA_DIR / cfg.paths.profile_tarball} /{cfg.paths.profile_tarball}\n"
    )
    return 0


def main() -> int:
    # 一定要先 parse:少了這步,打任何參數(包括 --help)都會直接開瀏覽器連 104。
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使目前是 http 模式也照樣建 profile",
    )
    args = parser.parse_args()

    use_utf8_console()
    setup_logging("INFO")

    cfg = load_config("config/config.yaml")
    if cfg.scrape.mode != "browser" and not args.force:
        print(
            "\n目前 scrape.mode 是 'http',**不需要瀏覽器 profile**。\n"
            "104 的搜尋 API 沒有 Cloudflare,只要帶對 Referer 就能直接取得資料。\n"
            "\n這個腳本只在切換到 browser 備援模式時才有用。\n"
            "真的要建請加 --force。\n"
        )
        return 0

    print(
        "\n⚠️  接下來會開啟一個瀏覽器視窗連到 104。\n"
        "    請【不要登入】你的 104 帳號、不要點應徵、不要收藏職缺。\n"
        "    你只需要看著它自動通過 Cloudflare 挑戰即可(約 3–8 秒)。\n"
    )

    try:
        return asyncio.run(bootstrap())
    except LoggedInDetected as exc:
        profile = Path(__file__).resolve().parents[1] / "local_data" / "chrome-profile"
        shutil.rmtree(profile, ignore_errors=True)
        for name in ("chrome-profile.tar.gz", "storage_state.json"):
            (DATA_DIR / name).unlink(missing_ok=True)
        print(
            f"\n❌ {exc}\n\n"
            "已刪除這個 profile。請重跑一次,過程中不要登入。\n"
            "理由:登入會把自動化行為綁定到你的真實求職身分 —— "
            "IP 被鎖還能換網路,帳號被停權就沒得救了。"
        )
        return 1
    except JobFinderError as exc:
        print(f"\n❌ {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
