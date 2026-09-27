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
#: 第 3 道門檻(規格:必備命中 ≥70%)。`jobfinder hitrate` 與 UI 共用這一份
HIT_RATE_THRESHOLD = 0.70
# 2026-09-24 使用者定案:2 年標黃、3 年以上排除。實測深評 ≥70 分的比例 2 年 45/104、3 年 12/78 ——
# 2 年常是可談的彈性標準,3 年才是真的不同級距。年數已由 normalize.period_to_years 換算
# (104 的 period = 年資 + 1;2026-09-24 前誤當年數,畫面上的「2 年以上」其實是 1 年以上)。
WARN_PERIOD_YEARS = 2
EXCLUDE_PERIOD_YEARS = 3

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
#: 第 2 道的 SI 訊號:列表 `coIndustry` 代碼(比對前綴,不比中文名)。1001001001 = 電腦系統整合服務業。
#: 它分不出「SI 公司但做自有產品」—— 那種看到了自己標「投」即可,紅燈不會自動排除任何東西。
SI_INDUSTRY_PREFIXES = ("1001001001",)


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
    #: 第 2 道「職缺訊號」的紅燈(有任一條就卡在第 2 道)
    signals: list[str] = field(default_factory=list)

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

    if row.period is not None and row.period >= EXCLUDE_PERIOD_YEARS:
        fails.append(f"經歷 {row.period} 年以上")
    elif row.period is not None and row.period >= WARN_PERIOD_YEARS:
        flags.append(f"⚠️經歷 {row.period} 年以上,可談")

    if PLATFORM_RX.search(row.jd):
        flags.append("🔴Platform/DataOps")

    result.gate1 = Light.FAIL if fails else (Light.WARN if flags else Light.PASS)

    # ── 第 2 道:職缺訊號。全部用已抓回來的資料、由程式判斷,不花 LLM、不多打 104 ──
    # 沒有紅燈就過;沒掃到培訓 ≠ 沒有培訓,所以它不是紅燈(只顯示「未提」)
    if row.industry_code.startswith(SI_INDUSTRY_PREFIXES):
        result.signals.append("SI產業")
    if CHORE_CATEGORY_RX.search(row.job_category):
        result.signals.append("職類含行銷/行政/業務")
    # 培訓常寫在福利制度而不是 JD:只掃 JD 命中 14/382,加上福利制度 154/382(2026-09-28 實測)
    if TRAINING_RX.search(row.jd) or TRAINING_RX.search(row.welfare):
        result.training = Training.FOUND
    elif row.has_detail:
        result.training = Training.NOT_FOUND
    if result.signals:
        result.gate2 = Light.FAIL
    elif row.has_detail:
        result.gate2 = Light.PASS
    else:
        result.gate2 = Light.UNKNOWN  # 職務類別只有全文才有,判斷不完整 —— 未判定不算卡住

    # ── 第 3 道:必備命中率(`jobfinder hitrate` 算好,存在 hitrate.db)──
    if row.hit_rate is None:
        result.gate3 = Light.UNKNOWN  # 未計算 / 無全文 / 無明列必備 —— 都不算卡住
    elif row.hit_rate < HIT_RATE_THRESHOLD or row.hit_core_missed:
        # 核心條件不符 → 不論百分比多高都不過(平均會把核心缺口稀釋掉)
        result.gate3 = Light.FAIL
    else:
        result.gate3 = Light.PASS
    return result


GATE3_LEGEND = (
    f"③命中率:✅ ≥{HIT_RATE_THRESHOLD:.0%} 且核心條件都符合 · "
    f"❌ 未達 {HIT_RATE_THRESHOLD:.0%} · "
    "⛔ 百分比有過,但核心條件不符(部分符合也算不符)· "
    "未計算/無全文/無明列必備 不算卡住"
)


def gate3_label(row: JobRow) -> str:
    """第 3 道的顯示文字。三種不過的原因要一眼分得出來 ——
    同樣是 ❌,「55% 未達標」跟「88% 但缺 AWS」是完全不同的處置。"""
    if row.hit_rate is None:
        return row.hit_rate_status
    pct = f"{row.hit_rate:.0%}"
    if row.hit_core_missed:
        return f"⛔ {pct}|核心缺:{'、'.join(row.hit_core_missed)}"
    if row.hit_rate < HIT_RATE_THRESHOLD:
        return f"❌ {pct}|未達 {HIT_RATE_THRESHOLD:.0%}"
    return f"✅ {pct}"


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
| 經歷 | {EXCLUDE_PERIOD_YEARS} 年以上 | ❌ 排除 |
| 經歷 | {WARN_PERIOD_YEARS} 年以上 | ⚠️ 標黃不排除:常是可談的彈性標準 |
| 薪資 | 月薪換算後上限 < 5 萬 | ❌ 排除 |
| 薪資 | 「以上」型且下限 < 5 萬 | ⚠️ 標黃:談薪 5 萬不可退 |
| 薪資 | 面議 | 不排除,視為未知 |
| AI 職稱 | 職稱含 AI工程師／人工智慧工程師／演算法工程師 | 🔴 標紅**不排除**,自己判斷 |
| Platform 缺 | JD 含 Helm／Terraform／GitOps／Iceberg／Trino／K8s | 🔴 標紅:名為資料工程、實為 Platform/DataOps |
| 學歷 | — | 不篩,只顯示 |

#### ② 職缺訊號(**沒有 🔴 就通過**;全部由程式判斷,不花 LLM、不多打 104)
| 訊號 | 判定 | 結果 |
|---|---|---|
| SI／接案 | 產業代碼是 電腦系統整合服務業(`{"／".join(SI_INDUSTRY_PREFIXES)}`) | 🔴 卡在第 2 道。做自有產品的 SI 請自己標「投」 |
| 打雜 | 職務類別含 數位行銷／行政／業務(只有全文才有職務類別) | 🔴 卡在第 2 道 |
| 新人培訓 | JD **與福利制度**含 新人培訓／教育訓練／導師／mentor／培訓計畫／學習資源／內部學習平台 | 有提 ✅;沒提只顯示「培訓未提」,**不是紅燈**(沒寫 ≠ 沒有) |

沒有全文的職缺缺了職務類別,只要沒有 SI 紅燈就顯示 ❔ 未判定(不算卡住)。
**主觀的「想不想去」不在閘門裡** —— 點開職缺後用「想去程度」1–5 自己評,之後拿來分析偏好。

#### ③ 必備命中率 ≥ {HIT_RATE_THRESHOLD:.0%}
`jobfinder hitrate`(每日排程在 pipeline 之後自動跑)由 LLM 把 JD 拆成條件清單,逐條判斷履歷是否滿足,
**命中率由程式算**:(符合 + 0.5 × 部分符合)÷ 必備條數,加分條件不進分母。
工作內容明確要求的核心技術即使條件欄寫「者佳」也算必備。點開職缺可看逐條判定與證據。

**核心條件:** 必備裡最多 2 條標為「核心」(這份工作的主要平台/工具)。任一條核心不是「符合」
(部分符合也算),**第 3 道就不過,不論百分比多高** —— 平均分數會把「卡在一個核心技術」稀釋掉。

| 顯示 | 意思 |
|---|---|
| ✅ 82% | 達標:≥{HIT_RATE_THRESHOLD:.0%} 且核心條件都符合 |
| ❌ 55%|未達 {HIT_RATE_THRESHOLD:.0%} | 百分比不夠 |
| ⛔ 88%|核心缺:X | 百分比有過,但核心條件 X 不是「符合」(部分符合也算) |
| 未計算 | 有全文但還沒輪到(每次最多算 config 的 `hitrate.max_jobs_per_run` 筆,由新到舊) |
| 無全文 | 只有粗篩通過的職缺才有 JD 全文,其餘算不出來 |
| 無明列必備 | JD 沒有可辨識的必備條件 |
| 履歷改過 | 命中率綁定履歷版本,`profile-de.md` 一改,舊結果全部視為未計算 |
"""  # noqa: E501 —— Markdown 表格列不能斷行
