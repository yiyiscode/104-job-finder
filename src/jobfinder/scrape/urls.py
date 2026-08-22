"""104 的 URL 組裝、禁止導覽的路徑黑名單、以及 robots.txt 的禁用參數。

兩件實測確認的事,寫在這裡免得日後有人改回去:

1. **詳細頁只吃連結尾段,不吃 `jobNo`。** 同一則職缺 `jobNo=15305872`、連結是
   `/job/94234`;拿 `jobNo` 去打 `/job/ajax/content/` 會回 404「職務不存在」。
2. **搜尋 API 走 `/jobs/search/api/jobs`,不是 `/jobs/search/?...`。**
   後者正好命中 robots.txt 的 `Disallow: /jobs/search/?*page=*`;前者落在 `Allow: /jobs/` 之下。
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from ..config import SearchCfg
from ..errors import ForbiddenPath

BASE = "https://www.104.com.tw"
SEARCH_PAGE = f"{BASE}/jobs/search/"
SEARCH_API = f"{BASE}/jobs/search/api/jobs"
DETAIL_API = f"{BASE}/job/ajax/content"

#: 任一 pattern 命中就拒絕請求。寧可誤殺也不要誤放。
FORBIDDEN_URL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"apply",  # 應徵表單 /job/*apply=form*(robots.txt 亦明文禁止)
        r"savejob",  # 收藏職缺
        r"favorite",
        r"/follow",  # 追蹤公司
        r"/login",
        r"/logout",
        r"/member",
        r"/my104",
        r"/resume",
        r"/vip",
        r"/pop-up",
        r"/messenger",  # 站內信
    )
)

#: robots.txt 明文 `Disallow: *<name>=*` 的 query 參數。
#: 我們的功能一個都不需要它們,所以直接禁止出現在 query string 裡。
FORBIDDEN_QUERY_PARAMS = frozenset(
    {
        "kwop",
        "hotjob",
        "recommendjob",
        "irstag",
        "expansiontype",
        "langstatus",
        "langflag",
        "excludeindustrycat",
        "excludecompanybycustno",
    }
)


def assert_path_allowed(url: str) -> None:
    """請求前的守門。命中黑名單就丟 ForbiddenPath —— 這是 bug 不是可預期的狀況。"""
    for pattern in FORBIDDEN_URL_PATTERNS:
        if pattern.search(url):
            raise ForbiddenPath(
                f"嘗試請求黑名單路徑(命中 /{pattern.pattern}/):{url}。"
                " 見 SPEC.md 規則 10 — 只讀不互動。"
            )

    query = url.partition("?")[2]
    for pair in query.split("&"):
        name = pair.partition("=")[0].strip().lower()
        if name in FORBIDDEN_QUERY_PARAMS:
            raise ForbiddenPath(
                f"query string 含 robots.txt 禁用的參數 `{name}`:{url}。"
                " 這些參數本專案一個都不需要。"
            )


def build_search_api_url(cfg: SearchCfg, keyword: str, page: int = 1) -> str:
    """組搜尋 **API** 的 URL(HTTP 模式用)。

    刻意不帶 `order`:`order=15` 是「符合度」排序不是日期,且 104 改版後數值會漂移。
    新鮮度一律靠 `isnew` + `appearDate` + DB 去重來保證。
    """
    params: list[tuple[str, str]] = [
        ("jobsource", "joblist_search"),
        ("keyword", keyword),
        ("mode", cfg.mode),
        ("page", str(page)),
        ("area", ",".join(cfg.areas)),
        # jobexp 是級距值不是上限值,多選以逗號串接
        ("jobexp", ",".join(cfg.jobexp)),
    ]
    if cfg.isnew is not None:
        params.append(("isnew", str(cfg.isnew)))

    url = f"{SEARCH_API}?{urlencode(params)}"
    assert_path_allowed(url)
    return url


def build_search_page_url(cfg: SearchCfg, keyword: str, page: int = 1) -> str:
    """組搜尋**頁面**的 URL(瀏覽器備援模式用)。

    ⚠️ 這條路徑命中 robots.txt 的 `Disallow: /jobs/search/?*page=*`。
    只有在 `scrape.mode: browser` 的備援情境才會走到,正常情況請用
    :func:`build_search_api_url`。
    """
    params: list[tuple[str, str]] = [
        ("jobsource", "joblist_search"),
        ("keyword", keyword),
        ("mode", cfg.mode),
        ("page", str(page)),
        ("area", ",".join(cfg.areas)),
        ("jobexp", ",".join(cfg.jobexp)),
    ]
    if cfg.isnew is not None:
        params.append(("isnew", str(cfg.isnew)))
    params.append(("searchJobs", "1"))

    url = f"{SEARCH_PAGE}?{urlencode(params)}"
    assert_path_allowed(url)
    return url


def _check_detail_id(detail_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", detail_id):
        raise ValueError(f"detail_id 格式不合法:{detail_id!r}")
    return detail_id


def build_job_url(detail_id: str) -> str:
    """職缺頁 URL(給人點的,也是詳細頁 API 的 Referer)。

    ⚠️ 參數是 **detail_id**(連結尾段),不是 `job_no`。
    """
    url = f"{BASE}/job/{_check_detail_id(detail_id)}"
    assert_path_allowed(url)
    return url


def build_detail_api_url(detail_id: str) -> str:
    """詳細頁 API。同樣只吃 detail_id —— 拿 job_no 打會 404。"""
    url = f"{DETAIL_API}/{_check_detail_id(detail_id)}"
    assert_path_allowed(url)
    return url


def company_url(cust_no: str | None) -> str | None:
    if not cust_no or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cust_no):
        return None
    return f"{BASE}/company/{cust_no}"


def normalize_job_link(link: str | None) -> str | None:
    """104 現在給的是絕對網址,但舊資料可能是 protocol-relative。兩種都吃。"""
    if not link:
        return None
    link = link.strip()
    if link.startswith("//"):
        return f"https:{link}"
    if link.startswith("/"):
        return f"{BASE}{link}"
    return link


def extract_detail_id(link: str | None) -> str | None:
    """從職缺連結尾段取出 detail_id(`https://www.104.com.tw/job/94234` → `94234`)。"""
    if not link:
        return None
    m = re.search(r"/job/([A-Za-z0-9_-]+)", link)
    return m.group(1) if m else None
