"""徵才官網的 Google 搜尋(Serper)。結果當作「候選」塞進 claude 的 prompt,由它 WebFetch 確認。

- Google 官方的 Custom Search JSON API 已不收新申請(2027-01-01 停用),所以走 Serper
- **用 urllib 不用 httpx**:company/ 不得 import httpx(tests/test_webui_boundary.py)——
  UI 按鈕會觸發這個套件,不能讓它順手長出對外抓取的能力。這裡只打 google.serper.dev
- 失敗一律回空清單(fail-open):沒有 Google 結果時 claude 會自己搜,評價照樣產生
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from .schema import JOB_BOARDS, _on

log = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"
#: 給 claude 看的候選數。再多只是讓它多 WebFetch 幾次
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class Candidate:
    title: str
    url: str
    snippet: str


def career_query(company: str) -> str:
    return f"{company} 徵才 官網 人才招募"


def _post(api_key: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        SERPER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 固定的 https 網址
        return json.loads(resp.read().decode("utf-8"))


def parse_candidates(payload: dict) -> list[Candidate]:
    """丟掉人力銀行/求職平台(它們不是「公司自己的」徵才官網)。"""
    out = []
    for item in payload.get("organic") or []:
        url = str(item.get("link") or "")
        if not url.startswith(("http://", "https://")) or _on(url, JOB_BOARDS):
            continue
        out.append(Candidate(str(item.get("title") or ""), url, str(item.get("snippet") or "")))
    return out[:MAX_CANDIDATES]


def google_candidates(
    company: str,
    api_key: str,
    *,
    timeout: float = 15.0,
    post: Callable[[str, dict, float], dict] = _post,
) -> list[Candidate]:
    if not api_key:
        return []
    try:
        payload = post(api_key, {"q": career_query(company), "gl": "tw", "hl": "zh-tw"}, timeout)
    except Exception as exc:  # noqa: BLE001 —— 額度用完、網路斷都不該擋住評價
        log.warning("%s Google 搜尋失敗,改用 claude 自己搜:%s", company, exc)
        return []
    return parse_candidates(payload)


def format_candidates(cands: list[Candidate]) -> str:
    if not cands:
        return ""
    lines = [f"{i}. {c.title} — {c.url}\n   {c.snippet}" for i, c in enumerate(cands, 1)]
    return "\n\n## Google 搜尋結果(徵才官網候選,**優先**從這裡挑)\n" + "\n".join(lines)
