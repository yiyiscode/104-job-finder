"""產業與公司規模的規則層過濾。

**為什麼在程式端而不是在 104 的搜尋參數做:** 搜尋請求數 = 關鍵字數 × 頁數,
跟有沒有帶產業參數完全無關 —— 在 104 端過濾**省不到任何請求預算**。
真正稀缺的是每次 18 個詳細頁名額,而這一層正好把它們全部留給目標職缺。
附帶好處:零額外請求、零封鎖風險、可以完全離線測試。

**判斷依據全部來自搜尋列表,不需要詳細頁。** ``coIndustry`` / ``employeeCount``
在列表回應裡就有(1,637 筆真實資料中缺失率 0),所以這一層跑在粗篩之前 ——
被過濾掉的職缺連 LLM 都不會看到。

**產業比對用代碼前綴不用中文名。** 104 的產業代碼是階層式的(``1001006`` =
半導體業,底下 ``...001`` IC設計 / ``...002`` 半導體製造 / ``...003`` 其他半導體),
前綴比對一次涵蓋整群,而且 104 改中文名稱時不會壞。

⚠️ **訊號消失時 fail-open,不是 fail-closed。** 如果 104 哪天不給 ``employeeCount``
了,fail-closed 會讓每天的日報靜靜變成 0 則 —— 看起來跟「今天沒新職缺」一模一樣,
可能好幾週才發現。所以某個欄位在整批裡缺超過一半時,**停用該條件並大聲告警**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import TargetingCfg
from .models import JobSummary

log = logging.getLogger(__name__)

#: 欄位缺失率超過這個比例就停用對應條件(見模組說明的 fail-open)
MISSING_RATIO_LIMIT = 0.5


@dataclass
class TargetingOutcome:
    """過濾結果。``dropped_*`` 是統計不是清單 —— 被丟掉的通常有數十筆,
    全列進 Telegram 摘要只會把真正要看的東西推到看不見的地方。"""

    keep: list[JobSummary] = field(default_factory=list)
    dropped: list[JobSummary] = field(default_factory=list)
    dropped_by_industry: int = 0
    dropped_by_size: int = 0
    warnings: list[str] = field(default_factory=list)
    #: 實際套用了哪幾條(fail-open 停用時會少)
    applied: list[str] = field(default_factory=list)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    def summary_line(self, total_in: int) -> str:
        if not self.applied:
            return f"產業／規模過濾:未套用(訊號缺失),{total_in} 筆全數保留"
        return (
            f"產業／規模過濾:{total_in} 筆 → 留 {len(self.keep)} 筆"
            f"(產業不符 {self.dropped_by_industry}、規模不足 {self.dropped_by_size})"
        )


def _industry_matches(code: str | None, prefixes: list[str]) -> bool:
    return bool(code) and any(code.startswith(p) for p in prefixes)  # type: ignore[union-attr]


def apply_targeting(jobs: list[JobSummary], cfg: TargetingCfg) -> TargetingOutcome:
    """把不在目標產業／規模的職缺濾掉。

    ``cfg.enabled=False`` 時原樣回傳 —— 要比較「有沒有這層」的差異時,
    改一個布林值就好,不必改程式。
    """
    outcome = TargetingOutcome()

    if not cfg.enabled or not jobs:
        outcome.keep = list(jobs)
        return outcome

    total = len(jobs)

    # ── fail-open 檢查:訊號還在嗎 ──────────────────────────────────
    check_industry = bool(cfg.industry_prefixes)
    if check_industry:
        missing = sum(1 for j in jobs if not j.industry_code)
        if missing / total > MISSING_RATIO_LIMIT:
            check_industry = False
            outcome.warnings.append(
                f"⚠️ {missing}/{total} 筆沒有產業代碼(coIndustry),"
                " 本次停用產業過濾以免日報靜默歸零 —— 104 可能已改版,請查 normalize.py"
            )
            log.warning("產業代碼缺失 %d/%d,停用產業過濾", missing, total)

    check_size = cfg.min_employee_count > 0
    if check_size:
        missing = sum(1 for j in jobs if j.employee_count is None)
        if missing / total > MISSING_RATIO_LIMIT:
            check_size = False
            outcome.warnings.append(
                f"⚠️ {missing}/{total} 筆沒有員工數(employeeCount),"
                " 本次停用公司規模過濾以免日報靜默歸零 —— 104 可能已改版,請查 normalize.py"
            )
            log.warning("員工數缺失 %d/%d,停用規模過濾", missing, total)

    if check_industry:
        outcome.applied.append(f"產業({len(cfg.industry_prefixes)} 群)")
    if check_size:
        outcome.applied.append(f"員工數 ≥{cfg.min_employee_count}")

    # ── 逐筆判定 ────────────────────────────────────────────────────
    for job in jobs:
        # 產業先判:它是「這家公司做什麼」,比規模更接近使用者真正的意圖
        if check_industry and not _industry_matches(job.industry_code, cfg.industry_prefixes):
            outcome.dropped.append(job)
            outcome.dropped_by_industry += 1
            continue
        # 員工數缺值視為不符。整批都缺時上面已經 fail-open 停用這條,
        # 走到這裡代表只有零星幾筆缺 —— 那是個別公司沒填,不是訊號消失。
        if check_size and (job.employee_count or 0) < cfg.min_employee_count:
            outcome.dropped.append(job)
            outcome.dropped_by_size += 1
            continue
        outcome.keep.append(job)

    log.info(
        "產業／規模過濾:%d → %d(產業不符 %d、規模不足 %d)",
        total,
        len(outcome.keep),
        outcome.dropped_by_industry,
        outcome.dropped_by_size,
    )
    return outcome
