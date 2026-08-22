"""瀏覽器啟動、profile 進出、Cloudflare warmup。

三個設計決定,每個都有踩過的坑在背後:

1. **不依賴 cookie 過關。** ``cf_clearance`` 綁 client IP,而 Modal 每次冷啟動 egress IP
   都不同,本機 bootstrap 出來的憑證搬上去一定失效。所以一定要有 :func:`warmup` ——
   讓真實瀏覽器當場過 Managed Challenge。profile 是 optimization,不是 dependency。

2. **Chrome profile 不直接放 Volume。** LevelDB + 檔案鎖 + mmap 在網路檔案系統上會
   ``SingletonLock`` 卡死或損毀。改用 tarball 進出,還原時清掉鎖檔。

3. **profile 裡不打包任何 cookie 資料庫。** Chrome 的 Cookies 是二進位 SQLite,
   沒辦法在打包時逐一過濾身分 cookie。與其冒險,不如完全不存它,另外存一份
   **經過白名單過濾的** ``storage_state.json``(SPEC.md 規則 9)。
   反正 cf_clearance 到了 Modal 也是無效的,損失極小。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import shutil
import tarfile
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import ChallengeBlocked
from .blocking import (
    TURNSTILE_SELECTORS,
    assert_anonymous,
    filter_persistable_cookies,
    raise_if_blocked,
)
from .budget import RequestBudget

log = logging.getLogger(__name__)

HOME_URL = "https://www.104.com.tw/"

#: Chrome 啟動旗標刻意保持極簡。Patchright 已經處理好反指紋偵測,
#: 自己加 --disable-blink-features 之類的旗標反而會製造出可辨識的特徵。
LAUNCH_ARGS: list[str] = ["--no-sandbox", "--disable-dev-shm-usage"]

#: 打包進 Volume 的 profile 子集。**刻意不含任何 Cookies 檔**。
PROFILE_KEEP = ("Default/Preferences", "Local State")

_LOCK_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _async_playwright():
    """優先用 Patchright(維護中的 stealth fork),沒裝才退回原版 Playwright。"""
    try:
        from patchright.async_api import async_playwright  # type: ignore

        return async_playwright, True
    except ImportError:  # pragma: no cover - 部署環境一定有 patchright
        from playwright.async_api import async_playwright  # type: ignore

        log.warning("找不到 patchright,退回原版 playwright,Cloudflare 通過率會明顯下降")
        return async_playwright, False


# ─── profile 進出 ───────────────────────────────────────────────────


def restore_profile(data_dir: str | Path, tarball_name: str, local: Path) -> Path:
    local.mkdir(parents=True, exist_ok=True)
    tar = Path(data_dir) / tarball_name
    if tar.exists():
        try:
            with tarfile.open(tar, "r:gz") as t:
                t.extractall(local.parent, filter="data")
            log.info("已還原 Chrome profile")
        except Exception:
            log.exception("還原 profile 失敗,改用全新 profile")

    # 殘留的鎖檔會讓 Chrome 拒絕啟動
    for name in _LOCK_FILES:
        with contextlib.suppress(OSError):
            (local / name).unlink(missing_ok=True)
    return local


def persist_profile(
    data_dir: str | Path, tarball_name: str, local: Path, volume: Any = None
) -> None:
    """只打包必要子集。整個 profile 有幾百 MB 的 cache,而且含二進位 cookie 庫。"""
    tmp_tar = Path(tempfile.gettempdir()) / tarball_name
    try:
        with tarfile.open(tmp_tar, "w:gz") as t:
            for rel in PROFILE_KEEP:
                path = local / rel
                if path.exists():
                    t.add(path, arcname=f"{local.name}/{rel}")
        shutil.copy2(tmp_tar, Path(data_dir) / tarball_name)
        if volume is not None:
            volume.commit()
    except Exception:
        log.exception("保存 profile 失敗(不影響本次執行)")


async def persist_storage_state(context: Any, data_dir: str | Path, filename: str) -> None:
    """存一份**過濾後**的 cookie。純 JSON,不含任何二進位鎖檔,永遠可讀。

    白名單只留 Cloudflare 憑證與語系 —— 身分 cookie 連存都不存。
    """
    try:
        state = await context.storage_state()
    except Exception:
        log.exception("取得 storage_state 失敗")
        return

    before = len(state.get("cookies", []))
    state["cookies"] = filter_persistable_cookies(state.get("cookies", []))
    state.pop("origins", None)  # localStorage 可能含身分資訊,一律不存
    log.debug("storage_state cookie 過濾:%d → %d", before, len(state["cookies"]))

    with contextlib.suppress(OSError):
        (Path(data_dir) / filename).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )


# ─── warmup ─────────────────────────────────────────────────────────


async def _turnstile_present(page: Any) -> bool:
    for selector in TURNSTILE_SELECTORS:
        with contextlib.suppress(Exception):
            if await page.query_selector(selector):
                return True
    return False


async def _page_state(page: Any) -> tuple[str, str]:
    title = ""
    body = ""
    with contextlib.suppress(Exception):
        title = await page.title()
    with contextlib.suppress(Exception):
        body = await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 600)"
        )
    return title, body


async def check_page_not_blocked(page: Any, response: Any = None) -> None:
    """導覽之後的封鎖檢查。被擋就丟 ChallengeBlocked —— 呼叫端絕不重試。"""
    title, body = await _page_state(page)
    raise_if_blocked(
        status=getattr(response, "status", None),
        title=title,
        body=body,
        turnstile_present=await _turnstile_present(page),
        url=getattr(page, "url", ""),
    )


async def warmup(context: Any, budget: RequestBudget, cfg: Config) -> None:
    """先開首頁,讓瀏覽器當場過 Cloudflare 的 Managed Challenge。

    這是整個抓取流程的成敗關鍵。挑戰通常 3–8 秒自動通過;超過
    ``warmup_max_seconds`` 還沒過就代表這個 IP 被判定為高風險,直接中止。
    """
    page = await context.new_page()
    try:
        response = await budget.navigate(page, HOME_URL, delay="none")

        deadline = asyncio.get_event_loop().time() + cfg.scrape.warmup_max_seconds
        while asyncio.get_event_loop().time() < deadline:
            title, body = await _page_state(page)
            if await _turnstile_present(page):
                raise ChallengeBlocked(
                    "首頁出現 Turnstile 互動挑戰,此 IP 已被視為高風險訪客",
                    url=HOME_URL,
                    page_title=title,
                )
            if not _looks_like_challenge(title, body):
                break
            await asyncio.sleep(1.5)
        else:
            title, _ = await _page_state(page)
            raise ChallengeBlocked(
                f"warmup 逾時({cfg.scrape.warmup_max_seconds}s)仍未通過 Cloudflare 挑戰",
                url=HOME_URL,
                page_title=title,
            )

        await check_page_not_blocked(page, response)

        # 一點點人味:真人不會載入完就立刻打 API
        with contextlib.suppress(Exception):
            await page.mouse.move(random.randint(200, 900), random.randint(200, 600))
            await page.mouse.wheel(0, random.randint(300, 900))
        await asyncio.sleep(random.uniform(1.0, 2.5))

        log.info("warmup 完成,已通過 Cloudflare")
    finally:
        with contextlib.suppress(Exception):
            await page.close()


def _looks_like_challenge(title: str, body: str) -> bool:
    text = f"{title} {body}".lower()
    return "just a moment" in text or "enable javascript and cookies" in text


# ─── context ────────────────────────────────────────────────────────


@asynccontextmanager
async def browser_context(
    cfg: Config,
    budget: RequestBudget,
    *,
    data_dir: str | Path,
    volume: Any = None,
    headless: bool | None = None,
) -> AsyncIterator[Any]:
    """開瀏覽器 → warmup → 確認匿名 → 交給呼叫端 → 收尾存檔。

    ``assert_anonymous`` 在 warmup 之後、任何搜尋之前執行:若偵測到 104 身分 cookie,
    整次執行立刻中止(SPEC.md 規則 9)。
    """
    async_playwright, is_patchright = _async_playwright()
    local_profile = restore_profile(
        data_dir, cfg.paths.profile_tarball, Path(tempfile.gettempdir()) / "chrome-profile"
    )

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(local_profile),
        "headless": cfg.scrape.headless if headless is None else headless,
        "args": LAUNCH_ARGS,
        "locale": cfg.scrape.locale,
        "timezone_id": cfg.scrape.timezone_id,
        "viewport": {
            "width": cfg.scrape.viewport[0],
            "height": cfg.scrape.viewport[1],
        },
        "extra_http_headers": {"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
    }
    # user_agent 一律不設 —— 用 Chromium 自己的真實 UA(SPEC.md 規則 7)
    if cfg.scrape.proxy is not None:
        launch_kwargs["proxy"] = cfg.scrape.proxy.model_dump(exclude_none=True)
        log.info("使用代理:%s", cfg.scrape.proxy.server)

    log.info("啟動瀏覽器(patchright=%s, headless=%s)", is_patchright, launch_kwargs["headless"])

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        try:
            await warmup(context, budget, cfg)
            # 硬性守門:確認我們是匿名訪客,不是登入狀態
            await assert_anonymous(context)
            yield context
        finally:
            with contextlib.suppress(Exception):
                await persist_storage_state(context, data_dir, cfg.paths.storage_state)
            with contextlib.suppress(Exception):
                await context.close()
            persist_profile(data_dir, cfg.paths.profile_tarball, local_profile, volume)
