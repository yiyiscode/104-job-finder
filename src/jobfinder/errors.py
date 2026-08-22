"""專案例外階層。

分類的用意是讓呼叫端能區分「可以重試的」與「絕對不能重試的」。
見 SPEC.md 硬性規則 1:對封鎖訊號重試是把暫時挑戰變成永久封鎖的頭號原因。
"""


class JobFinderError(Exception):
    """所有本專案例外的基底。"""


# ─── 絕對不可重試 ────────────────────────────────────────────────────


class FatalScrapeError(JobFinderError):
    """抓取層的致命錯誤。捕捉到就必須中止整次執行,不得重試、不得續跑下一個關鍵字。"""


class ChallengeBlocked(FatalScrapeError):
    """偵測到 Cloudflare 挑戰或封鎖訊號(403/429/503、Just a moment、Turnstile、1020)。

    這會觸發熔斷器。**絕不重試** —— 重試只會坐實「這個 IP 是機器人」。
    """

    def __init__(self, message: str, *, url: str = "", page_title: str = ""):
        super().__init__(message)
        self.url = url
        self.page_title = page_title


class LoggedInDetected(FatalScrapeError):
    """在瀏覽器 context 中偵測到 104 的身分 cookie。

    SPEC.md 規則 9:全程必須是匿名訪客。帳號被停權比 IP 被鎖嚴重得多,
    所以這是硬性中止,不是警告。
    """


class ForbiddenPath(FatalScrapeError):
    """嘗試導覽到黑名單路徑(應徵表單、收藏、追蹤公司等)。

    SPEC.md 規則 10:這些是「已登入使用者的動作」,即使未登入被觸發也是異常訊號。
    會走到這裡代表程式有 bug,直接中止。
    """


# ─── 正常收尾,不算失敗 ──────────────────────────────────────────────


class BudgetExhausted(JobFinderError):
    """請求預算用完。

    這**不是**錯誤:pipeline 應該拿已抓到的資料正常往下走去評分推播,
    也不觸發熔斷。見 SPEC.md 規則 3。
    """


class CircuitOpen(JobFinderError):
    """熔斷器仍在冷卻期。pipeline 應短路,連瀏覽器都不要開。"""

    def __init__(self, message: str, *, retry_after_iso: str = "", failures: int = 0):
        super().__init__(message)
        self.retry_after_iso = retry_after_iso
        self.failures = failures


class SourceMisconfigured(JobFinderError):
    """104 回 403 但沒有任何挑戰頁特徵(body 為空)。

    這代表 **我方的請求少了東西**(最常見是 ``Referer`` 掉了),而不是被 104 封鎖。
    兩者的處置完全相反:

    * 這個 → 告警請使用者檢查設定,**不觸發熔斷**(冷卻 24 小時對設定錯誤毫無幫助)
    * :class:`ChallengeBlocked` → 熔斷,退場

    分辨方式見 :func:`jobfinder.scrape.blocking.detect_block`。
    """


class ScrapeDisabled(JobFinderError):
    """緊急開關已啟動(config `scrape.enabled: false` 或 JOBFINDER_SCRAPE_DISABLED)。"""


class AlreadyRanToday(JobFinderError):
    """今天已經跑過了。SPEC.md 規則 3:每日僅執行一次。"""


# ─── 可重試 / 局部失敗 ───────────────────────────────────────────────


class TransientScrapeError(JobFinderError):
    """一般網路錯誤(逾時、連線中斷)。允許重試 1 次,或跳過續跑下一個關鍵字。"""


class SchemaDrift(JobFinderError):
    """104 回傳的 JSON 結構與預期不符。單筆記錄後續跑;比例過高才告警。"""


class LLMParseError(JobFinderError):
    """LLM 回應無法解析成預期 schema。"""

    def __init__(self, raw: str):
        super().__init__(f"無法解析 LLM 輸出:{raw[:500]}")
        self.raw = raw


class NotifyFailed(JobFinderError):
    """Telegram 推送失敗。"""


class ConfigError(JobFinderError):
    """設定違反硬性限制或格式錯誤。啟動時就該炸,不要跑到一半才發現。"""
