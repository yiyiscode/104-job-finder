"""產業格與職稱類別:把 104 的代碼與自由文字職稱歸成可篩選的幾類。"""

from __future__ import annotations

import re
import unicodedata

# ── 產業格 ─────────────────────────────────────────────────────────
# 比對 coIndustry 代碼前綴,不比對中文名 —— 104 改名稱時代碼不會變(CLAUDE.md)。
INDUSTRY_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("半導體/電子", ("1001006", "1001005", "1001004", "1001003")),
    ("金融", ("1004",)),
    ("軟體", ("1001001",)),
]
INDUSTRY_NAMES = [name for name, _ in INDUSTRY_PREFIXES] + ["其他"]
DEFAULT_INDUSTRIES = ["半導體/電子"]


def industry_group(code: str | int | None) -> str:
    text = str(code or "")
    for name, prefixes in INDUSTRY_PREFIXES:
        if text.startswith(prefixes):
            return name
    return "其他"


# ── 職稱類別 ───────────────────────────────────────────────────────
# 由上往下「第一個命中」為準。資料工程排最前面 ——「AI 數據開發工程師」這種
# 實際做資料管線的缺(規格評 ~75% 命中)要歸資料工程,不是 AI。
TITLE_GROUPS: list[tuple[str, re.Pattern[str]]] = [
    (
        "資料工程",
        re.compile(
            r"資料工程|數據工程|data.{0,10}engineer|資料倉儲|數據倉儲|etl|數據開發|資料開發|"
            r"資料.{0,4}平台|數據平台|大數據|big ?data|資料庫|數據庫|dba|database|資料處理",
            re.I,
        ),
    ),
    (
        "資料科學/ML",
        re.compile(
            r"資料科學|數據科學|data ?scien|機器學習|machine ?learning|\bml\b|深度學習", re.I
        ),
    ),
    ("資料分析/BI", re.compile(r"分析師|analyst|\bbi\b|報表|數據分析|資料分析|analytics", re.I)),
    (
        "AI/演算法",
        re.compile(r"ai|人工智慧|演算法|algorithm|llm|生成式|agent|影像處理|電腦視覺", re.I),
    ),
    (
        "軟體開發",
        re.compile(
            r"軟體|software|資訊工程|後端|前端|backend|frontend|full.?stack|\bsre\b|devops|"
            r"程式|developer|系統|\bsw\b",
            re.I,
        ),
    ),
]
TITLE_GROUP_NAMES = [name for name, _ in TITLE_GROUPS] + ["其他"]
DEFAULT_TITLE_GROUPS = ["資料工程"]


def normalize_title(title: str) -> str:
    """NFKC 正規化。真實資料裡有「資料⼯程師」—— 那個「⼯」是康熙部首字元,
    不正規化的話任何「工程」的比對都會漏掉它。"""
    return unicodedata.normalize("NFKC", title)


def title_group(title: str) -> str:
    text = normalize_title(title)
    for name, rx in TITLE_GROUPS:
        if rx.search(text):
            return name
    return "其他"


def title_matches(title: str, query: str) -> bool:
    """職稱關鍵字搜尋。``|`` 分隔表示「或」,不分大小寫,空查詢視為全部符合。

    使用者輸入一律當字面字串(``re.escape``)—— 輸入 ``C++`` 不該變成錯誤的正則。
    """
    terms = [re.escape(t.strip()) for t in query.split("|") if t.strip()]
    if not terms:
        return True
    return re.search("|".join(terms), normalize_title(title), re.I) is not None
