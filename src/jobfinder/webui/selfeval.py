"""自評 app 的邏輯:待評佇列、低分抽樣、量測指標、技能自評寫回履歷。純函式,不碰 IO。

目的是量「LLM 深評分數貼不貼近使用者的想去程度」(見 memory:評分準確率提升路線)。
累積約 60 筆「有深評 + 想去程度」的標記後,才做程式端校正模型。

畫面在 ``selfeval_app.py``;資料跟 Job Finder UI 共用 decisions.db(想去程度兩邊互通)。
"""

from __future__ import annotations

import random
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .rows import JobRow
from .skills import match_skills

BATCH_SIZE = 30  # 使用者一次補約 30 筆
SAMPLE_SIZE = 5
SAMPLE_MIN_POOL = 10
TARGET_LABELS = 60  # 校正模型開工的門檻

SCALE = {
    1: "聽過/碰過",
    2: "照教學做得出來",
    3: "專案裡實際用過",
    4: "能獨立負責一塊",
    5: "能帶別人/當主力",
}
FEEDBACK = {"high": "⬆️ 分數偏高", "ok": "✅ 準", "low": "⬇️ 分數偏低"}
VERDICTS = {"strong_apply": "務必投", "apply": "優先投", "maybe": "值得一試", "skip": "不建議"}

#: 深評五維的配分與判準摘要 —— 原文在 scoring/prompts.py 的 SYSTEM_DEEP。
#: LLM 只回分數、不回每一維的理由,所以卡片上用判準讓使用者自己對照。
DIMENSIONS = (
    (
        "tech_fit",
        "技術",
        35,
        "技術棧重疊度。SQL/Python/ETL/排程/爬蟲高權重;AI/ML 是輔線;"
        "JD 要 Spark/Airflow/dbt 等沒實作過的工具扣分但不歸零",
    ),
    (
        "exp_fit",
        "年資",
        25,
        "最關鍵的過濾。不拘/1–3 年 → 20–25;3 年以上偏執行 → 10–15;5 年以上/Senior → 0–5",
    ),
    (
        "domain_fit",
        "領域",
        15,
        "金融/保險/資安高(國泰 CAP 真實實習);半導體中高(只有學術專案);電商/物流中",
    ),
    (
        "growth_fit",
        "成長",
        15,
        "對第一份正職的價值:mentor、團隊、技術棧現代度,是否真的在做資料工程而不是報表維護",
    ),
    (
        "practical_fit",
        "實務",
        10,
        "台中以北、非派遣、薪資。面議給中間值 5–6;月薪 ≥45K 加到 8–10;<40K 扣到 2–3",
    ),
)
SCORE_BANDS = "90+ 量身打造 · 75–89 高度契合 · 60–74 值得一試 · 40–59 勉強沾邊 · <40 不建議"

_BLOCK_START = "<!-- skill-self-rating:start"
_BLOCK_END = "<!-- skill-self-rating:end -->"
#: 連同區塊後面那一個分隔空行一起比對 —— 插入時會補一個,移除時才不會留下多的空行
_BLOCK_RX = re.compile(re.escape(_BLOCK_START) + r".*?" + re.escape(_BLOCK_END) + r"\n{0,2}", re.S)


@dataclass(frozen=True)
class Card:
    job: JobRow
    source: str  # "sample" = 低分抽樣(補反例);"notified" = 推播過;"scored" = 其他有深評的


def weekly_sample(
    rows: Iterable[JobRow], this_week: date, n: int = SAMPLE_SIZE
) -> tuple[date, float, list[JobRow]]:
    """有深評、低於中位數的隨機 n 筆 —— 補「不想去」的反例。

    只抽有深評的:校正模型的特徵是深評五維 + 命中率,被規則層/粗篩刷掉的沒有這些特徵。
    本週有深評的不足 ``SAMPLE_MIN_POOL`` 筆就往前多看幾週。以週一日期當種子,同一週抽到的固定。
    回傳 (母體起始日, 中位數, 抽到的)。
    """
    scored = [r for r in rows if r.deep_score is not None]
    since, pool_all = this_week, []
    for back in range(5):
        since = this_week - timedelta(weeks=back)
        pool_all = [r for r in scored if r.first_seen >= since]
        if len(pool_all) >= SAMPLE_MIN_POOL:
            break
    if not pool_all:
        return since, 0.0, []
    median = statistics.median(r.deep_score for r in pool_all)
    pool = sorted((r for r in pool_all if r.deep_score < median), key=lambda r: r.job_no)
    return since, median, random.Random(this_week.isoformat()).sample(pool, min(n, len(pool)))


def build_queue(
    rows: Sequence[JobRow],
    desires: Mapping[str, int],
    feedback: Mapping[str, str],
    this_week: date,
    size: int = BATCH_SIZE,
) -> list[Card]:
    """這一批要評的卡片:抽樣 → 推播過(新到舊)→ 其他有深評(新到舊)。

    想去程度與分數準不準**都評過**的才算完成、不再出現;只評了一半的還會回來。
    """

    def pending(r: JobRow) -> bool:
        return r.job_no not in desires or r.job_no not in feedback

    _, _, sample = weekly_sample(rows, this_week)
    newest = sorted(
        (r for r in rows if r.deep_score is not None and pending(r)),
        key=lambda r: (r.first_seen, r.deep_score),
        reverse=True,
    )
    cards: list[Card] = []
    seen: set[str] = set()
    for source, group in (
        ("sample", [r for r in sample if pending(r)]),
        ("notified", [r for r in newest if r.pipeline_status == "notified"]),
        ("scored", [r for r in newest if r.pipeline_status != "notified"]),
    ):
        for r in group:
            if r.job_no not in seen:
                seen.add(r.job_no)
                cards.append(Card(r, source))
    return cards[:size]


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """排序一致度。少於 3 筆或某一邊全部相同時回 None。"""
    if len(pairs) < 3:
        return None

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out, i = [0.0] * len(xs), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    a, b = zip(*pairs, strict=True)
    try:
        return statistics.correlation(ranks(list(a)), ranks(list(b)))
    except statistics.StatisticsError:
        return None


@dataclass(frozen=True)
class Metrics:
    labeled: list[tuple[int, int]]  # (深評分數, 想去程度)
    positives: int  # 想去 ≥4
    negatives: int  # 想去 ≤2
    rho: float | None
    feedback: Counter


def metrics(
    rows: Iterable[JobRow], desires: Mapping[str, int], feedback: Mapping[str, str]
) -> Metrics:
    by_no = {r.job_no: r for r in rows}
    labeled = [
        (by_no[k].deep_score, v)
        for k, v in desires.items()
        if k in by_no and by_no[k].deep_score is not None
    ]
    return Metrics(
        labeled=labeled,
        positives=sum(v >= 4 for _, v in labeled),
        negatives=sum(v <= 2 for _, v in labeled),
        rho=spearman(labeled),
        feedback=Counter(feedback.values()),
    )


def daily_precision(
    rows: Iterable[JobRow], desires: Mapping[str, int], days: int = 14
) -> list[dict]:
    """每天推播的前 5 名裡,使用者評了幾則 ≥4 星。"""
    daily: dict[date, list[JobRow]] = defaultdict(list)
    for r in rows:
        if r.pipeline_status == "notified":
            daily[r.first_seen].append(r)
    out = []
    for day in sorted(daily, reverse=True)[:days]:
        top = sorted(daily[day], key=lambda r: -(r.deep_score or 0))[:5]
        rated = [desires[r.job_no] for r in top if r.job_no in desires]
        hit = sum(v >= 4 for v in rated)
        out.append(
            {
                "日期": day,
                "推播": len(top),
                "已評": len(rated),
                "≥4 星": hit,
                "≥4 星比例": f"{hit / len(rated):.0%}" if rated else "—",
            }
        )
    return out


def skill_lists(
    rows: Iterable[JobRow], have: frozenset[str], top_missing: int = 15
) -> tuple[list[str], list[str], Counter]:
    """(履歷上有的, 職缺常要求但履歷沒寫的, 每個技能出現在幾筆有深評的職缺摘要)。"""
    demand: Counter = Counter()
    for r in rows:
        if r.deep_score is not None:
            demand.update(match_skills(r.summary_text))
    missing = [s for s, _ in demand.most_common() if s not in have][:top_missing]
    return sorted(have), missing, demand


def skills_block(rated: Mapping[str, int]) -> str:
    lines = [
        f"{_BLOCK_START}(由自評 app 產生,手改會被覆蓋)-->",
        "## 技能自評",
        "",
        "刻度:" + "、".join(f"{k} {v}" for k, v in SCALE.items()),
        "",
        "| 技能 | 自評 |",
        "|---|---|",
    ]
    lines += [
        f"| {k} | {v} {SCALE[v]} |"
        for k, v in sorted(rated.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    lines.append(_BLOCK_END)
    return "\n".join(lines) + "\n"


def apply_skills_block(resume: str, rated: Mapping[str, int]) -> str:
    """把自評區塊寫進履歷:已有就整塊替換;沒有就插在「## 學歷」之前(找不到就接在最後)。

    ``rated`` 為空時移除區塊。區塊外的內容一個字都不動。
    """
    without = _BLOCK_RX.sub("", resume)
    if not rated:
        return without
    block = skills_block(rated)
    if _BLOCK_RX.search(resume):
        return _BLOCK_RX.sub(lambda _: block + "\n", resume, count=1)
    marker = without.find("\n## 學歷")
    if marker == -1:
        return without.rstrip("\n") + "\n\n" + block
    return without[: marker + 1] + block + "\n" + without[marker + 1 :]
