"""三道閘門(規格 docs/webui-spec.md §二)。純函式,不碰 IO。

依序由便宜到貴。「卡在哪一道」= 第一個出現 ❌ 的閘門;**未判定不算卡住** ——
LLM 還沒跑不代表職缺不好,把它當不通過會讓大部分職缺憑空卡住。

說明文字 ``GATE_HELP_MD`` 放在這裡、緊鄰規則常數:改規則的人一定會看到它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rows import JobRow

MIN_EMPLOYEES = 30
SALARY_FLOOR_MONTHLY = 50_000
MAX_PERIOD_YEARS = 1  # 不拘與 1 年以上可投;2 年以上排除

EXCLUDED_AREAS = ("高雄", "台南", "臺南", "嘉義", "雲林", "彰化")
SHIFT_RX = re.compile(r"輪班|on[\s-]?call|夜班|中班", re.I)
DISPATCH_RX = re.compile(r"派遣|人力仲介")
INTERN_RX = re.compile(r"實習|intern|工讀|時薪", re.I)
CAMPUS_RX = re.compile(r"校園徵才|預聘|研發替代役")
CAMPUS_KEEP = "秋季"
# 字面比對。寬鬆的 AI.*工程師 會誤傷「AI 數據開發工程師」(規格評 ~75% 命中的好缺)
AI_TITLE_RX = re.compile(r"AI工程師|人工智慧工程師|演算法工程師", re.I)
PLATFORM_RX = re.compile(r"\bhelm\b|terraform|gitops|iceberg|trino|\bk8s\b|kubernetes", re.I)
TRAINING_RX = re.compile(r"新人培訓|教育訓練|導師|mentor|培訓計畫|學習資源|內部學習平台", re.I)
CHORE_CATEGORY_RX = re.compile(r"數位行銷|行政|業務")


class Light(StrEnum):
    PASS = "pass"  # 全過
    WARN = "warn"  # 有黃/紅標但不排除
    FAIL = "fail"  # 排除
    UNKNOWN = "unknown"  # 未判定 / 未計算


class Training(StrEnum):
    FOUND = "found"  # 正則命中
    NOT_FOUND = "not_found"  # 有全文但沒掃到 —— 仍可能有,要 LLM 才能確定
    UNKNOWN = "unknown"  # 沒有全文,摘要太短不足以判斷


@dataclass
class GateResult:
    gate1: Light = Light.UNKNOWN
    gate2: Light = Light.UNKNOWN
    gate3: Light = Light.UNKNOWN
    training: Training = Training.UNKNOWN
    fails: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def stuck_at(self) -> int | None:
        for i, light in enumerate((self.gate1, self.gate2, self.gate3), start=1):
            if light is Light.FAIL:
                return i
        return None

    def passes_through(self, n: int) -> bool:
        """第 1 到第 n 道都沒有 ❌。"""
        stuck = self.stuck_at
        return stuck is None or stuck > n


def evaluate(row: JobRow) -> GateResult:
    result = GateResult()
    fails, flags = result.fails, result.flags
    title = row.title.replace(" ", "")

    # ── 第 1 道:硬門檻 ──
    if row.employees is None:
        flags.append("⚠️員工數未提供")
    elif row.employees < MIN_EMPLOYEES:
        fails.append(f"員工 {row.employees} 人 <{MIN_EMPLOYEES}")

    if any(area in row.area for area in EXCLUDED_AREAS):
        fails.append("地點在台中以南")
    if SHIFT_RX.search(row.jd):
        fails.append("輪班/on-call")
    if DISPATCH_RX.search(row.company + row.industry_desc):
        fails.append("派遣/人力仲介")
    if INTERN_RX.search(title):
        fails.append("實習/工讀")
    if CAMPUS_RX.search(title) and CAMPUS_KEEP not in title:
        fails.append("校園徵才/預聘")
    if AI_TITLE_RX.search(title):
        flags.append("🔴AI職稱")

    high, low = row.salary_high_monthly, row.salary_low_monthly
    if high is not None and high < SALARY_FLOOR_MONTHLY:
        fails.append(f"薪資上限 {high:,} <5萬")
    elif low is not None and low < SALARY_FLOOR_MONTHLY and row.salary_open_ended:
        flags.append("⚠️以上型 <5萬,談薪不可退")

    if row.period is not None and row.period > MAX_PERIOD_YEARS:
        fails.append(f"經歷 {row.period} 年以上")

    if PLATFORM_RX.search(row.jd):
        flags.append("🔴Platform/DataOps")

    result.gate1 = Light.FAIL if fails else (Light.WARN if flags else Light.PASS)

    # ── 第 2 道:想不想去。① 自有產品要 LLM,UI 不判 → 整道最多只能是「未判定」──
    if TRAINING_RX.search(row.jd):
        result.training = Training.FOUND
    elif row.has_detail:
        result.training = Training.NOT_FOUND
    if CHORE_CATEGORY_RX.search(row.job_category):
        flags.append("🔴職類含行銷/行政/業務")
    result.gate2 = Light.UNKNOWN

    # ── 第 3 道:必備命中率,P2 才實作 ──
    result.gate3 = Light.UNKNOWN
    return result


GATE_HELP_MD = f"""
**三道閘門依序由便宜到貴。「卡在哪一道」= 第一個出現 ❌ 的閘門;未判定不算卡住。**

#### ① 硬門檻(結構化欄位,程式全自動)
| 條件 | 規則 | 結果 |
|---|---|---|
| 公司人數 | < {MIN_EMPLOYEES} 人 | ❌ 排除;**未提供 → ⚠️ 標黃不排除** |
| 地點 | {"／".join(EXCLUDED_AREAS)} | ❌ 排除(台中以北都可) |
| 輪班 | JD 含 輪班／on-call／夜班／中班(有全文用全文,沒有用摘要) | ❌ 排除 |
| 派遣 | 公司名或產業含 派遣／人力仲介 | ❌ 排除 |
| 實習／工讀 | 職稱含 實習／intern／工讀／時薪 | ❌ 排除 |
| 校園徵才 | 職稱含 校園徵才／預聘／研發替代役(**含「{CAMPUS_KEEP}」保留**) | ❌ 排除 |
| 經歷 | {MAX_PERIOD_YEARS + 1} 年以上 | ❌ 排除(不拘、1 年以上可投) |
| 薪資 | 月薪換算後上限 < 5 萬 | ❌ 排除 |
| 薪資 | 「以上」型且下限 < 5 萬 | ⚠️ 標黃:談薪 5 萬不可退 |
| 薪資 | 面議 | 不排除,視為未知 |
| AI 職稱 | 職稱含 AI工程師／人工智慧工程師／演算法工程師 | 🔴 標紅**不排除**,自己判斷 |
| Platform 缺 | JD 含 Helm／Terraform／GitOps／Iceberg／Trino／K8s | 🔴 標紅:名為資料工程、實為 Platform/DataOps |
| 學歷 | — | 不篩,只顯示 |

#### ② 想不想去(三條**全部**滿足才通過)
| # | 條件 | 判定 | 目前狀態 |
|---|---|---|---|
| ① | 有自己的產品,非 SI／接案 | LLM(分界線是「產品給誰用」,不是產業) | ❔ 未判定(P2) |
| ② | JD 有寫新人培訓／導師制度 | 正則:新人培訓／教育訓練／導師／mentor／培訓計畫／學習資源／內部學習平台 | 命中 ✅;有全文沒命中「未掃到」;無全文「未判定」 |
| ③ | 職務明確是 Data／工程,不是打雜 | 職務類別含 數位行銷／行政／業務 → 🔴 | 只有全文才有職務類別 |

#### ③ 必備命中率 ≥ 70%
`profile-de.md` 的技能逐條比對 JD 必備條件。**尚未實作(P2),顯示「未計算」。**
"""  # noqa: E501 —— Markdown 表格列不能斷行
