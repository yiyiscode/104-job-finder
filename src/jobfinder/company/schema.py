"""LLM 回傳的公司評價結構,以及存檔前的清洗。純 pydantic,UI 也讀這個。"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..zh import ZhText

Tone = Literal["good", "mixed", "bad", "unknown"]
TONE_ICONS: dict[str, str] = {"good": "🟢", "mixed": "🟡", "bad": "🔴", "unknown": "⚪"}

#: (欄位名, 顯示標題)—— 順序就是 UI 的顯示順序
FACETS: tuple[tuple[str, str], ...] = (
    ("culture", "工作氛圍/主管/加班"),
    ("pay", "薪資福利/年終"),
    ("interview", "面試流程與題型"),
    ("data_team", "資料/AI 團隊現況"),
)

#: 人力銀行與求職平台 —— 「徵才官網」指公司自己的招募頁,這些不算
JOB_BOARDS = (
    "104.com.tw",
    "1111.com.tw",
    "yes123.com.tw",
    "518.com.tw",
    "linkedin.com",
    "cakeresume.com",
    "cake.me",
    "indeed.com",
    "glassdoor.com",
    "yourator.co",
)
#: 來源裡不收 104:它的頁面本身就是職缺,不是評價;而且這個專案不碰 104 的頁面
BLOCKED_SOURCES = ("104.com.tw",)


class Facet(BaseModel):
    tone: Tone
    summary: ZhText = Field(description="2–3 句;資料不足就寫「資料不足」並給 unknown")


class Source(BaseModel):
    title: ZhText = Field(description="網站名 + 標題,例如「PTT Tech_Job:光寶面試心得」")
    url: str
    date: str = Field(description="YYYY-MM;看不出來寫「不明」")


class CompanyReview(BaseModel):
    overall: ZhText = Field(description="一句話整體印象")
    tone: Tone
    culture: Facet
    pay: Facet
    interview: Facet
    data_team: Facet
    career_url: str = Field(description="公司自己的徵才/招募頁網址;找不到給空字串")
    career_note: ZhText = Field(description="怎麼投:官網系統要另建履歷、只收 104、等")
    #: career_url 從哪來。舊資料沒有這欄 → 預設空字串
    career_source: Literal["google", "websearch", ""] = Field(
        default="", description="google = 取自提供的 Google 搜尋結果;websearch = 自己搜到的"
    )
    sources: list[Source]


def company_key(name: str) -> str:
    """「光寶科技」與「光寶科技股份有限公司」是同一家 —— 清單匯入的名稱常是簡稱。"""
    key = unicodedata.normalize("NFKC", name).strip()
    # 104 的「品牌_正式名」(玉山銀行_玉山商業銀行股份有限公司):取有「公司」的那段;
    # 「台達電子工業股份有限公司 _DELTA ELECTRONICS INC.」則是前段
    parts = [p.strip() for p in key.split("_") if p.strip()]
    key = next((p for p in parts if "公司" in p), parts[0] if parts else key)
    key = re.sub(r"\(.*?\)$", "", key).strip() or key  # 結尾的「(台積電)」「(總公司)」
    for suffix in ("股份有限公司", "有限公司", "公司"):
        if key.endswith(suffix) and len(key) > len(suffix):
            key = key[: -len(suffix)]
            break
    return "".join(key.split()).lower()


def is_placeholder(name: str) -> bool:
    """清單職缺還沒抓到全文時的佔位名稱(webui/candidates.py),不是公司。"""
    return not name.strip() or name.lstrip().startswith("(")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _on(url: str, domains: tuple[str, ...]) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def sanitize(review: CompanyReview) -> CompanyReview:
    """模型偶爾會把 104 的公司頁當成「徵才官網」,或拿職缺頁當評價來源。"""
    sources = [
        s
        for s in review.sources
        if s.url.startswith(("http://", "https://")) and not _on(s.url, BLOCKED_SOURCES)
    ]
    career_url, note = review.career_url.strip(), review.career_note
    if career_url and (
        not career_url.startswith(("http://", "https://")) or _on(career_url, JOB_BOARDS)
    ):
        where = _host(career_url) or career_url
        note = f"只找到人力銀行/求職平台上的頁面({where}),沒有公司自己的徵才官網。"
        career_url = ""
    source = review.career_source if career_url else ""
    return review.model_copy(
        update={
            "sources": sources,
            "career_url": career_url,
            "career_note": note,
            "career_source": source,
        }
    )
