"""程式端的評分校正。

SPEC.md §6.4:**能用規則做的事就用規則做**。便宜模型的數值評分校準很差,而且
使用者「專案豐富但正式年資 0–1 年」是 LLM 最容易誤判的組合 —— 模型看到滿滿的
BERT/YOLO/RAG 專案,會把他當成資深工程師,於是給「要求 5 年」的職缺高分。

這裡的三道校正都是純函式,不依賴 LLM,所以可以被完整測試。
"""

from __future__ import annotations

import re

from ..config import ScoringCfg
from ..models import JobDetail, JobSummary, ScoreBreakdown, Verdict
from .schemas import VERDICTS, DeepScore

#: 「5年以上」「3 年以上工作經驗」
_YEARS_ABOVE = re.compile(r"(\d+)\s*年(?:以上|＋|\+)")
#: 「1~3年」「1-3 年」— 取下界
_YEARS_RANGE = re.compile(r"(\d+)\s*[~\-～]\s*\d+\s*年")
_NO_REQUIREMENT = ("不拘", "無經驗可", "應屆", "新鮮人", "無須經驗", "經歷不限")


def required_years(summary: JobSummary, detail: JobDetail | None) -> int | None:
    """取得這則職缺要求的最低年資。

    優先讀搜尋列表換算好的 ``min_years``(``period`` - 1,見 ``normalize.period_to_years``),
    比對中文字串做正規表達式可靠得多。解析文字只是 fallback。
    """
    if summary.min_years is not None:
        return summary.min_years
    return min_years_required(detail.work_exp if detail else None, summary.period_desc)


def min_years_required(*texts: str | None) -> int | None:
    """從中文的經歷欄位解析出最低年資要求(fallback 用)。

    回傳 None 代表無法判斷 —— 那種情況不套用 hard rule,交給 LLM。
    """
    for text in texts:
        if not text:
            continue
        if any(marker in text for marker in _NO_REQUIREMENT):
            return 0
        if (m := _YEARS_ABOVE.search(text)) is not None:
            return int(m.group(1))
        if (m := _YEARS_RANGE.search(text)) is not None:
            return int(m.group(1))
    return None


def normalize_verdict(raw: str) -> Verdict | None:
    """把模型給的 verdict 正規化。**無法對應時回 None,而不是 "maybe"**。

    這個區別很重要:`maybe` 是一個**負面訊號**,會讓達標的分數被降級。
    但模型把 verdict 寫成 `worth_a_shot` 只代表它沒照格式寫,
    **不代表它認為這個職缺不好** —— 拿它當降級依據等於因為用字而懲罰職缺。

    回 None 的意思是「這次沒有取得定性判斷」,交叉驗證那一關就跳過。
    """
    value = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value in VERDICTS:
        return value  # type: ignore[return-value]
    return None


def apply_guardrails(
    score: DeepScore,
    summary: JobSummary,
    detail: JobDetail | None,
    cfg: ScoringCfg,
) -> tuple[ScoreBreakdown, int, Verdict, list[str]]:
    """套用三道程式端校正,回傳 (維度分數, 總分, verdict, 修正紀錄)。

    1. 年資 hard rule:JD 要求 ≥ cutoff 年時,強制壓低 ``exp_fit``
    2. 總分重算:不信任模型的加法
    3. verdict 交叉驗證:分數與定性判斷矛盾時以 verdict 為準降級
    """
    adjustments: list[str] = []

    breakdown = ScoreBreakdown(
        tech_fit=score.tech_fit,
        exp_fit=score.exp_fit,
        domain_fit=score.domain_fit,
        growth_fit=score.growth_fit,
        practical_fit=score.practical_fit,
    )
    verdict = normalize_verdict(score.verdict)
    if verdict is None:
        adjustments.append(
            f"verdict {score.verdict!r} 不在合法值,本次跳過 verdict 交叉驗證(不當成負面訊號)"
        )

    # ① 年資 hard rule
    required = required_years(summary, detail)
    if (
        required is not None
        and required >= cfg.senior_years_cutoff
        and breakdown.exp_fit > cfg.senior_exp_fit_cap
    ):
        adjustments.append(
            f"JD 要求 {required} 年經驗,exp_fit 由 {breakdown.exp_fit}"
            f" 強制壓到 {cfg.senior_exp_fit_cap}(年資 hard rule)"
        )
        breakdown = breakdown.model_copy(update={"exp_fit": cfg.senior_exp_fit_cap})

    # ② 總分重算
    total = breakdown.total
    if abs(total - score.total_score) > 5:
        adjustments.append(
            f"模型給的 total_score={score.total_score} 與五維相加 {total} 差距過大,採用重算值"
        )

    # ③ verdict 交叉驗證 —— verdict 無效時跳過,不拿它當降級理由
    if verdict is not None and total >= cfg.threshold and verdict in ("skip", "maybe"):
        capped = cfg.threshold - 1
        adjustments.append(f"總分 {total} 達標但模型判定為 {verdict},以定性判斷為準降到 {capped}")
        total = capped

    return breakdown, total, verdict or "maybe", adjustments


def select_for_notification(scored: list, cfg: ScoringCfg) -> tuple[list, list]:
    """依門檻或相對排序挑出要推播的,回傳 (推播, 未達標)。

    ``mode: top_n`` 是風險 3 的備案:如果絕對分數校準不了,改成固定推前 N 名。

    **為什麼 top_n 還是要有下限:** 深評分數的雜訊實測約 ±8 分(同一份資料、
    同一個 prompt 跑兩次,最好的那則 80 / 88),所以任何硬切點都會讓邊緣職缺
    隨機進出。top_n 的價值是「雜訊只影響排序,不會讓整天變空」—— 但它不該
    變成「職缺荒的日子把 30 分的垃圾也推出來」。``top_n_floor`` 訂在遠低於
    雜訊帶的位置,只砍真正的垃圾,不參與邊緣判斷。
    """
    ordered = sorted(scored, key=lambda s: s.total, reverse=True)

    if cfg.mode == "top_n":
        picked = [s for s in ordered[: cfg.top_n] if s.total >= cfg.top_n_floor]
        rejected = [s for s in ordered if s not in picked]
        return picked, rejected

    passing = [s for s in ordered if s.total >= cfg.threshold]
    rejected = [s for s in ordered if s.total < cfg.threshold]
    # 超過每日上限的,退回未達標清單裡讓使用者仍看得到標題
    if len(passing) > cfg.max_per_day:
        rejected = passing[cfg.max_per_day :] + rejected
        passing = passing[: cfg.max_per_day]
    return passing, rejected
