"""raw JSON → 領域模型。

這是整個專案唯一認識 104 JSON 結構的地方。104 改版時只有這個檔案要改。

**欄位對應是對真實回應驗證過的**(2026-08-21),不是猜的。幾個容易踩回去的點:

* 搜尋回應的 ``data`` **本身就是陣列**,沒有 ``data.list``;總數在 ``metadata.pagination``
* 列表**沒有 ``salaryDesc``**,只有 ``salaryLow``/``salaryHigh``,薪資字串要自己組
* ``period`` 是**年資 + 1**(0 = 不拘,2 = 1年以上,3 = 2年以上),用 :func:`period_to_years` 轉。
  2026-09-24 以前一直當成年數,全站多算 1 年(對 369 筆詳細頁 ``workExp`` 驗證過)
* ``optionEdu`` 是 **int 陣列**,不是字串
* ``tags`` 是 **dict**,不是陣列

設計原則:**容忍但記錄**。104 多加欄位不該打掛我們;少了欄位則記一筆 schema drift,
比例過高時告警(見 SPEC.md 風險 2)。單筆解析失敗絕不中斷整輪。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .models import JobDetail, JobSummary
from .scrape.urls import extract_detail_id, normalize_job_link

log = logging.getLogger(__name__)

#: 已實測對照:optionEdu=[4] → 「大學」、[5] → 「碩士」、[3,4,5,6] → 「專科以上」
EDU_CODES: dict[int, str] = {1: "國中", 2: "高中", 3: "專科", 4: "大學", 5: "碩士", 6: "博士"}

#: 薪資類型。搜尋列表把它藏在混淆過的欄位名 ``s10`` 裡,詳細頁則叫 ``salaryType``
#: ——三筆樣本交叉驗證完全吻合。
#:
#: ⚠️ **不看這個欄位會出大錯**:年薪職缺的 ``salaryLow`` 是 567,000,
#: 硬套「月薪」前綴就會在日報上寫出「月薪 56 萬」。
SALARY_TYPES: dict[int, str] = {
    10: "面議",
    20: "時薪",
    30: "日薪",
    40: "論件計酬",
    50: "月薪",
    60: "年薪",
}

#: 把各種薪資類型換算成可比較的月薪基準(用於「薪資是否達標」這類判斷)
#: 104 用這個值代表「以上」型薪資沒有上限。它不是真的上限,不能拿來顯示或統計。
OPEN_ENDED_SALARY = 9_999_999

_TO_MONTHLY: dict[int, float] = {20: 176.0, 30: 22.0, 50: 1.0, 60: 1 / 12}


def pick(data: Any, path: str, default: Any = None) -> Any:
    """安全地取巢狀值,例如 ``pick(payload, "data.jobDetail.jobDescription")``。"""
    node = data
    for key in path.split("."):
        if isinstance(node, dict):
            node = node.get(key)
        else:
            return default
    return default if node is None else node


def _as_int(value: Any) -> int | None:
    """104 的薪資「面議」會給 0,那不是真的 0 元 —— 轉成 None。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n or None


def _as_plain_int(value: Any) -> int | None:
    """跟 :func:`_as_int` 不同:0 是有意義的值(例如年資 0 = 不拘)。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_list(value: Any) -> list[str]:
    """104 的清單有三種形狀:字串陣列、物件陣列、以及以參數名為 key 的 dict。"""
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(
                item.get("description")
                or item.get("desc")
                or item.get("name")
                or item.get("param")
                or ""
            )
        else:
            text = str(item)
        text = text.strip()
        if text:
            out.append(text)
    return out


def _as_tag_list(value: Any) -> list[str]:
    """``tags`` 是以參數名為 key 的 dict,例如 ``{"wf2": {"desc": "", "param": "wf2"}}``。

    只取有內容的 ``desc``。``desc`` 為空時**不要**退回 ``param`` —— 那是 104 的內部代碼
    (`wf1`、`wf7` 之類),放進 prompt 只是噪音。
    """
    items = value.values() if isinstance(value, dict) else value
    if not isinstance(items, (list, tuple)) and not hasattr(items, "__iter__"):
        return []

    out: list[str] = []
    for item in items:
        text = item if isinstance(item, str) else ""
        if isinstance(item, dict):
            text = str(item.get("desc") or item.get("description") or "")
        text = text.strip()
        if text and text not in out:
            out.append(text)
    return out


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def format_salary(low: int | None, high: int | None, salary_type: int | None = None) -> str:
    """104 的搜尋列表沒給薪資字串,只有上下限與類型代碼,所以自己組。

    上下限都是 0 代表「面議」—— 那不是月薪 0 元。
    類型一定要看:年薪職缺的下限是 567,000,寫成「月薪」就變成離譜的 56 萬。
    """
    if not low and not high:
        return "待遇面議"
    unit = SALARY_TYPES.get(salary_type or 0, "待遇")
    if unit == "面議":
        unit = "待遇"
    if low and high and high >= OPEN_ENDED_SALARY:
        return f"{unit} {low:,} 元以上"
    if low and high and low != high:
        return f"{unit} {low:,}~{high:,} 元"
    return f"{unit} {(low or high):,} 元"


def monthly_equivalent(low: int | None, salary_type: int | None) -> int | None:
    """換算成月薪基準,好跟「月薪 45K 以上」這種門檻公平比較。

    面議(或無法換算的類型)回 None —— 那是「不知道」,不是「零」。
    """
    if not low or salary_type not in _TO_MONTHLY:
        return None
    return int(low * _TO_MONTHLY[salary_type])


def period_to_years(period: int | None) -> int | None:
    """104 列表的 ``period`` → 要求的最低年資。

    ``period`` 是「年資 + 1」,0 代表不拘。對 369 筆真實詳細頁的 ``workExp`` 驗證:
    0 → 不拘(266 筆)、2 → 1年以上(59)、3 → 2年以上(41),各只有 1 筆例外(雇主上架後改過條件)。
    1 沒出現過,保守當成不拘。
    """
    if period is None:
        return None
    return max(period - 1, 0)


def format_experience(min_years: int | None) -> str:
    """``min_years`` 是年數(已由 :func:`period_to_years` 換算過)。0 = 不拘。"""
    if min_years is None:
        return "未提供"
    if min_years <= 0:
        return "經歷不拘"
    return f"{min_years}年以上"


def format_education(codes: list[int]) -> str:
    """104 自己的呈現慣例是「最低者 + 以上」(實測 [3,4,5,6] → 「專科以上」)。"""
    known = sorted(c for c in codes if c in EDU_CODES)
    if not known:
        return "學歷不拘"
    lowest = EDU_CODES[known[0]]
    return lowest if len(known) == 1 else f"{lowest}以上"


@dataclass
class SearchPage:
    jobs: list[JobSummary] = field(default_factory=list)
    total_count: int = 0
    total_page: int = 0
    #: 缺欄位/結構不符的紀錄。比例過高代表 104 改版了。
    drift: list[str] = field(default_factory=list)


def normalize_search_response(payload: dict[str, Any], keyword: str) -> SearchPage:
    """搜尋列表 API 的回應 → JobSummary 清單。"""
    page = SearchPage(
        total_count=int(pick(payload, "metadata.pagination.total", 0) or 0),
        total_page=int(pick(payload, "metadata.pagination.lastPage", 0) or 0),
    )

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        page.drift.append(
            f"回應的 data 不是陣列 —— 搜尋 API 結構可能已改版(實際型別 {type(rows).__name__})"
        )
        return page

    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            job = _normalize_summary_row(row, keyword, page.drift)
        except Exception as exc:  # 單筆壞掉不該打斷整輪
            page.drift.append(f"單筆職缺解析失敗:{exc}")
            log.warning("解析職缺失敗,略過:%s", exc)
            continue
        if job:
            page.jobs.append(job)

    return page


def _normalize_summary_row(
    row: dict[str, Any], keyword: str, drift: list[str]
) -> JobSummary | None:
    raw_link = row.get("link")
    if isinstance(raw_link, dict):
        raw_link = raw_link.get("job")
    link = normalize_job_link(raw_link if isinstance(raw_link, str) else None)

    job_no = _as_text(row.get("jobNo"))
    detail_id = extract_detail_id(link)

    if not job_no and not detail_id:
        drift.append("職缺同時缺少 jobNo 與可解析的 link.job")
        return None
    # 兩者互為備援,但語意不同:job_no 是去重主鍵,detail_id 才能打詳細頁 API
    job_no = job_no or detail_id
    if not detail_id:
        drift.append(f"{job_no}: 無法從 link 解析 detail_id,詳細頁將抓不到")
        detail_id = job_no

    if not link:
        link = f"https://www.104.com.tw/job/{detail_id}"
        drift.append(f"{job_no}: 缺少 link.job,以 detail_id 組回連結")

    job_name = _as_text(row.get("jobName"))
    cust_name = _as_text(row.get("custName"))
    if not job_name or not cust_name:
        drift.append(f"{job_no}: 缺少 jobName 或 custName")

    low, high = _as_int(row.get("salaryLow")), _as_int(row.get("salaryHigh"))
    # s10 是被混淆過的欄位名,內容就是詳細頁的 salaryType(已三筆交叉驗證)
    salary_type = _as_plain_int(row.get("s10")) or _as_plain_int(row.get("salaryType"))
    min_years = period_to_years(_as_plain_int(row.get("period")))
    edu_codes = [c for c in (row.get("optionEdu") or []) if isinstance(c, int)]

    return JobSummary(
        job_no=job_no,
        detail_id=detail_id,
        job_name=job_name or "(未提供職稱)",
        cust_name=cust_name or "(未提供公司)",
        cust_no=_as_text(row.get("custNo")),
        job_url=link,
        area_desc=_as_text(row.get("jobAddrNoDesc")) or _as_text(row.get("jobAddress")),
        salary_desc=format_salary(low, high, salary_type),
        salary_low=low,
        salary_high=high,
        salary_type=salary_type,
        monthly_low=monthly_equivalent(low, salary_type),
        min_years=min_years,
        period_desc=format_experience(min_years),
        edu_codes=edu_codes,
        edu_desc=format_education(edu_codes),
        appear_date=_as_text(row.get("appearDate")),
        desc_snippet=_as_text(row.get("description")) or _as_text(row.get("descSnippet")),
        apply_cnt=_as_plain_int(row.get("applyCnt")),
        tags=_as_tag_list(row.get("tags")),
        industry=_as_text(row.get("coIndustryDesc")),
        industry_code=_as_text(row.get("coIndustry")),
        employee_count=_as_plain_int(row.get("employeeCount")),
        remote_work_type=_as_plain_int(row.get("remoteWorkType")),
        hr_response_pr=(
            float(row["hrBehaviorPR"])
            if isinstance(row.get("hrBehaviorPR"), (int, float))
            else None
        ),
        matched_keywords=[keyword],
        raw=row,
    )


def merge_summaries(jobs: list[JobSummary]) -> list[JobSummary]:
    """多組關鍵字的結果合併去重。

    同一個 ``job_no`` 只留一筆,但把命中它的所有關鍵字合起來 —— 之後才能在
    日報上看出「這個缺同時符合 LLM 與 MLOps 兩個方向」。先到先贏,保留原順序。
    """
    merged: dict[str, JobSummary] = {}
    for job in jobs:
        existing = merged.get(job.job_no)
        if existing is None:
            merged[job.job_no] = job
            continue
        keywords = sorted(set(existing.matched_keywords) | set(job.matched_keywords))
        merged[job.job_no] = existing.model_copy(update={"matched_keywords": keywords})
    return list(merged.values())


def normalize_detail_response(
    payload: dict[str, Any], job_no: str, drift: list[str] | None = None
) -> JobDetail:
    """職缺詳細 API 的回應 → JobDetail。

    這層的欄位對應大致與原本的假設相符 —— ``condition.edu`` 與 ``condition.workExp``
    在詳細頁已經是**文字**,不需要再查代碼表。
    """
    drift = drift if drift is not None else []

    if isinstance(payload, dict) and payload.get("error"):
        message = pick(payload, "error.message", "未知錯誤")
        drift.append(f"{job_no}: 詳細頁 API 回錯誤 —— {message}")
        return JobDetail(job_no=job_no, raw=payload)

    detail = pick(payload, "data.jobDetail", {})
    condition = pick(payload, "data.condition", {})
    welfare = pick(payload, "data.welfare", {})

    if not isinstance(detail, dict) or not detail:
        drift.append(f"{job_no}: 缺少 data.jobDetail")
        detail = {}
    if not isinstance(condition, dict):
        condition = {}
    if not isinstance(welfare, dict):
        welfare = {}

    description = _as_text(detail.get("jobDescription"))
    if not description:
        drift.append(f"{job_no}: 缺少 jobDescription,深評品質會下降")

    return JobDetail(
        job_no=job_no,
        job_description=description,
        job_category=_as_str_list(detail.get("jobCategory")),
        salary=_as_text(detail.get("salary")),
        salary_min=_as_int(detail.get("salaryMin")),
        salary_max=_as_int(detail.get("salaryMax")),
        work_exp=_as_text(condition.get("workExp")),
        edu=_as_text(condition.get("edu")),
        major=_as_str_list(condition.get("major")),
        specialty=_as_str_list(condition.get("specialty")),
        skill=_as_str_list(condition.get("skill")),
        other=_as_text(condition.get("other")),
        welfare_tags=_as_str_list(welfare.get("tag")) + _as_str_list(welfare.get("legalTag")),
        welfare_text=_as_text(welfare.get("welfare")),
        industry=_as_text(pick(payload, "data.industry")),
        address=_as_text(detail.get("addressRegion")) or _as_text(detail.get("addressArea")),
        employment_type=_as_text(detail.get("manageResp")),
        raw=payload if isinstance(payload, dict) else {},
    )


# ── 詳細頁 → 列表形狀(投遞清單補抓用)─────────────────────────────────
#: 詳細頁 API 對下架/不存在的職缺回 ``{"error": {"code": 11201, "message": "職務不存在"}}``
DETAIL_NOT_FOUND = 11201
_WORK_EXP_RX = re.compile(r"(\d+)\s*年以上")
_EMPLOYEES_RX = re.compile(r"(\d+)人")


def detail_error(payload: dict[str, Any]) -> tuple[int | None, str] | None:
    """詳細頁回的是錯誤就給 (代碼, 訊息),正常回應給 None。"""
    if not isinstance(payload, dict) or not payload.get("error"):
        return None
    code = _as_plain_int(pick(payload, "error.code"))
    return code, str(pick(payload, "error.message", "未知錯誤"))


def work_exp_to_period(text: str | None) -> int | None:
    """詳細頁 ``condition.workExp`` 的文字 → 列表的 ``period``(年資 + 1)。

    :func:`period_to_years` 的反函數。對 387 筆同時有兩邊的真實資料驗證過:
    「不拘」↔ 0、「N年以上」↔ N+1,只有 3 筆例外(雇主上架後改過條件)。
    """
    if not text:
        return None
    if text.strip() == "不拘":
        return 0
    m = _WORK_EXP_RX.search(text)
    return int(m.group(1)) + 1 if m else None


def detail_as_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """詳細頁 API 回應 → 列表(搜尋)JSON 的**形狀**。

    只有「投遞清單」補抓的職缺會用到:它們不在 pipeline 的關鍵字裡,手上只有詳細頁,
    但 UI 的閘門讀的是列表欄位。對應關係都對 387 筆真實資料驗證過(2026-09-28):
    ``industryNo`` 等於 ``coIndustry``(387/387)、「以上」型薪資兩邊都是 9999999、
    ``employees`` 是「150人」/「暫不提供」這種文字、語言代碼兩邊一致。

    詳細頁**沒有 jobNo**,所以這份也沒有 —— 呼叫端用 detail_id 當鍵。
    """
    data = pick(payload, "data", {})
    data = data if isinstance(data, dict) else {}
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    detail = data.get("jobDetail") if isinstance(data.get("jobDetail"), dict) else {}
    condition = data.get("condition") if isinstance(data.get("condition"), dict) else {}

    employees = re.sub(r"[,\s]", "", str(data.get("employees") or ""))
    m = _EMPLOYEES_RX.fullmatch(employees)
    langs = condition.get("language") if isinstance(condition.get("language"), list) else []
    return {
        "jobName": _as_text(header.get("jobName")) or "",
        "custName": _as_text(header.get("custName")) or "",
        "coIndustry": _as_plain_int(data.get("industryNo")),
        "coIndustryDesc": _as_text(data.get("industry")) or "",
        # 列表的「暫不提供」是 0,build_row 會把 0 當成沒提供
        "employeeCount": int(m.group(1)) if m else 0,
        "s10": _as_plain_int(detail.get("salaryType")),
        "salaryLow": _as_plain_int(detail.get("salaryMin")) or 0,
        "salaryHigh": _as_plain_int(detail.get("salaryMax")) or 0,
        "period": work_exp_to_period(_as_text(condition.get("workExp"))),
        "languageRequirements": [{"language": x.get("code")} for x in langs if isinstance(x, dict)],
        "link": {"cust": _as_text(header.get("custUrl")) or ""},
        "description": _as_text(detail.get("jobDescription")) or "",
        "jobAddrNoDesc": _as_text(detail.get("addressRegion")) or "",
        "appearDate": _as_text(header.get("appearDate")) or "",
    }
