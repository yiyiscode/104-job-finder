"""封鎖偵測、熔斷器狀態機、以及匿名身分守門。

三件事都是 SPEC.md §9 的核心護欄:

* :func:`detect_block` — 認出 Cloudflare 挑戰/封鎖,讓呼叫端**零重試**中止(規則 1)
* :func:`cooldown_for` / :func:`trip` — 熔斷器,被擋過就冷卻(規則 2)
* :func:`assert_anonymous` — 確認沒有 104 身分 cookie(規則 9)

這些函式刻意都是純函式或只依賴傳入物件,好讓 `tests/test_guardrails.py` 能完整覆蓋。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from ..config import CircuitCfg
from ..errors import ChallengeBlocked, CircuitOpen, LoggedInDetected

log = logging.getLogger(__name__)

# ─── 封鎖訊號 ────────────────────────────────────────────────────────

#: HTTP 狀態碼。503 也算 —— Cloudflare 的挑戰頁常以 503 回。
BLOCKED_STATUSES = frozenset({403, 429, 503})

#: 頁面標題中的挑戰標記(小寫比對)
BLOCK_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "access denied",
    "請稍候",
)

#: 內文中的挑戰/封鎖標記(小寫比對)
BLOCK_BODY_MARKERS = (
    "enable javascript and cookies",
    "checking your browser",
    "cf-error-details",
    "error code: 1020",
    "error code: 1015",
    "ray id",
    "verify you are human",
)

#: Turnstile 互動框出現 = 已被降級為高風險訪客,比自動挑戰更嚴重
TURNSTILE_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "#cf-turnstile",
    "div.cf-turnstile",
)


def looks_like_challenge(title: str = "", body: str = "") -> bool:
    """內容是不是真的 Cloudflare 挑戰頁(而不是單純的 origin 阻斷)。"""
    text = f"{title} {body}".lower()
    return any(m in text for m in BLOCK_TITLE_MARKERS) or any(m in text for m in BLOCK_BODY_MARKERS)


def classify_403(body: str) -> Literal["challenge", "misconfigured"]:
    """403 有兩種完全不同的意思,處置也完全相反。

    * **body 空的** → origin 層的 ``Referer`` 白名單擋的。這是**我方請求少了東西**,
      冷卻 24 小時對它毫無幫助 —— 該做的是告警請人檢查設定(:class:`SourceMisconfigured`)。
    * **body 有內容** → 104 真的把防護掛上來了。熔斷,並考慮切換瀏覽器模式。

    已實測:不帶 Referer 打搜尋 API 回 403 且 body 為 **0 bytes**;
    而 ``/sitemap.xml`` 回的 403 body 有 5KB 的 Turnstile 挑戰頁。兩者長得完全不一樣。

    ⚠️ **只有在確實讀得到 body 的情境才能用這個函式**(也就是 HTTP 模式)。
    瀏覽器模式讀 body 可能失敗而得到空字串,那時必須 fail-closed ——
    所以 :func:`detect_block` 維持「403 一律視為封鎖」。
    """
    return "misconfigured" if not body.strip() else "challenge"


def detect_block(
    *,
    status: int | None = None,
    title: str = "",
    body: str = "",
    turnstile_present: bool = False,
) -> str | None:
    """回傳封鎖原因字串,沒被擋則回 None。

    純函式,好測。呼叫端拿到非 None 就必須丟 :class:`ChallengeBlocked` 並中止整次執行 ——
    **不要重試**。重試是把「暫時被挑戰」變成「永久封 IP」的頭號原因。

    **fail-closed**:403/429/503 一律視為封鎖,即使讀不到 body。
    「403 但其實只是 Referer 掉了」這種情況由 HTTP source 先用 :func:`classify_403`
    分流掉,不會走到這裡。
    """
    if status is not None and status in BLOCKED_STATUSES:
        return f"HTTP {status}"
    if turnstile_present:
        return "Cloudflare Turnstile 互動挑戰出現(已被視為高風險訪客)"

    title_l = title.lower()
    for marker in BLOCK_TITLE_MARKERS:
        if marker in title_l:
            return f"頁面標題為挑戰頁:{title!r}"

    body_l = body.lower()
    for marker in BLOCK_BODY_MARKERS:
        if marker in body_l:
            return f"內文含挑戰標記:{marker!r}"
    return None


def raise_if_blocked(
    *,
    status: int | None = None,
    title: str = "",
    body: str = "",
    turnstile_present: bool = False,
    url: str = "",
) -> None:
    reason = detect_block(
        status=status, title=title, body=body, turnstile_present=turnstile_present
    )
    if reason:
        raise ChallengeBlocked(reason, url=url, page_title=title)


# ─── 匿名身分守門(SPEC.md 規則 9)──────────────────────────────────

#: 明確代表已登入的 cookie 名稱片段(小寫比對)。
#:
#: ⚠️ `104_session` 在社群的 104 登入專案中是登入態 cookie。若實測發現匿名訪客
#:    也會拿到同名 cookie,請調整這份清單而不是拿掉整個守門機制 ——
#:    fail-closed(誤殺)是刻意的方向,漏放的代價是使用者的求職帳號。
IDENTITY_COOKIE_MARKERS = (
    "104_session",
    "arj",
    "token",
    "auth",
    "jwt",
    "member",
    "login",
    "passport",
    "uid",
    "userid",
    "accesskey",
)

#: 允許持久化到 Volume 的 cookie。只有 Cloudflare 的通關憑證與語系,
#: 其餘一律不存 —— 連存都不存,就不會在下次執行被帶回來。
PERSISTABLE_COOKIE_NAMES = frozenset(
    {"cf_clearance", "__cf_bm", "__cflb", "_cfuvid", "locale", "lang"}
)


def _is_104_domain(domain: str) -> bool:
    return "104.com.tw" in (domain or "").lower()


def is_identity_cookie(cookie: dict[str, Any]) -> bool:
    """這個 cookie 是否代表 104 的登入身分。"""
    if not _is_104_domain(str(cookie.get("domain", ""))):
        return False
    name = str(cookie.get("name", "")).lower()
    if name in PERSISTABLE_COOKIE_NAMES:
        return False
    return any(marker in name for marker in IDENTITY_COOKIE_MARKERS)


def find_identity_cookies(cookies: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("name", "?")) for c in cookies if is_identity_cookie(c)]


def filter_persistable_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """存檔前的過濾:只留 Cloudflare 通關憑證與語系偏好。

    用白名單而非黑名單 —— 未知的新 cookie 預設不存,才不會哪天 104 換個名字
    就把身分資料寫進 Volume。
    """
    return [c for c in cookies if str(c.get("name", "")).lower() in PERSISTABLE_COOKIE_NAMES]


class _Context(Protocol):
    async def cookies(self) -> list[dict[str, Any]]: ...


async def assert_anonymous(context: _Context) -> None:
    """確認瀏覽器 context 中沒有任何 104 身分 cookie。

    在 warmup 之後、進搜尋頁之前呼叫。發現登入態就**中止整次執行** ——
    帳號被停權比 IP 被鎖嚴重得多,而且無可替代。
    """
    cookies = await context.cookies()
    found = find_identity_cookies(cookies)
    if found:
        raise LoggedInDetected(
            f"偵測到 104 身分 cookie:{', '.join(found)}。"
            " 本專案必須全程以匿名訪客身分執行,絕不登入使用者的 104 帳號"
            "(見 SPEC.md 規則 9)。請刪除瀏覽器 profile 後重新 bootstrap,"
            " 且過程中不要登入。"
        )


# ─── 熔斷器(SPEC.md 規則 2)────────────────────────────────────────

CircuitStatus = Literal["closed", "open"]


@dataclass(frozen=True)
class CircuitState:
    state: CircuitStatus = "closed"
    tripped_at: datetime | None = None
    reason: str = ""
    consecutive_failures: int = 0

    @classmethod
    def closed(cls) -> CircuitState:
        return cls()


def cooldown_for(consecutive_failures: int, cfg: CircuitCfg) -> timedelta | None:
    """第 1 次被擋冷卻 24h,第 2 次 72h,第 3 次起無限期(回 None)。

    理由:如果 104 已經在擋你,隔天 08:00 再自動跑一次只會坐實「這個 IP 是機器人」。
    """
    if consecutive_failures <= 1:
        return timedelta(hours=cfg.cooldown_hours)
    if consecutive_failures == 2:
        return timedelta(hours=cfg.escalated_cooldown_hours)
    return None


def trip(state: CircuitState, reason: str, now: datetime) -> CircuitState:
    """記錄一次封鎖,回傳新的熔斷狀態。"""
    new = replace(
        state,
        state="open",
        tripped_at=now,
        reason=reason,
        consecutive_failures=state.consecutive_failures + 1,
    )
    log.warning("熔斷器 tripped(第 %d 次):%s", new.consecutive_failures, reason)
    return new


def reset(state: CircuitState) -> CircuitState:
    """成功執行後,或使用者手動 `jobfinder reset-circuit` 時歸零。"""
    return CircuitState.closed()


def assert_closed(state: CircuitState, cfg: CircuitCfg, now: datetime) -> CircuitState:
    """熔斷檢查。仍在冷卻期就丟 :class:`CircuitOpen`,pipeline 應短路且不開瀏覽器。

    回傳可能已自動恢復的狀態,呼叫端要存回 DB。
    """
    if state.state == "closed":
        return state

    cooldown = cooldown_for(state.consecutive_failures, cfg)
    if cooldown is None:
        raise CircuitOpen(
            f"熔斷器已連續觸發 {state.consecutive_failures} 次,進入無限期停用。"
            f" 最後原因:{state.reason}。"
            " 請確認 104 端狀況後手動執行 `jobfinder reset-circuit` 解除。",
            failures=state.consecutive_failures,
        )

    assert state.tripped_at is not None
    retry_at = state.tripped_at + cooldown
    if now < retry_at:
        raise CircuitOpen(
            f"熔斷器冷卻中(第 {state.consecutive_failures} 次觸發,原因:{state.reason})。"
            f" 下次可執行時間:{retry_at.isoformat()}",
            retry_after_iso=retry_at.isoformat(),
            failures=state.consecutive_failures,
        )

    # 冷卻期過了,自動半開:允許這次嘗試,但失敗次數保留,以便再被擋時升級冷卻期
    log.info("熔斷器冷卻期已過(%s),允許本次嘗試", retry_at.isoformat())
    return replace(state, state="closed")
