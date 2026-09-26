"""技能趨勢頁的統計。純函式,回傳 list/dict,由 app.py 轉 DataFrame 畫圖。

⚠️ 取樣偏誤(規格 §四):全文(raw_detail)只有粗篩通過的職缺才有 —— 那是
「跟履歷相符」的子集,拿它算技能頻率會循環論證。所以**摘要層做母體估計、全文層
做細節,兩層分開報**,每張圖都要標 N 與偏誤。偏誤說明集中在這裡,不散落在畫面程式。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .rows import JobRow
from .skills import match_skills

SUMMARY_LAYER = "摘要層"
DETAIL_LAYER = "全文層"
LAYERS = (SUMMARY_LAYER, DETAIL_LAYER)
SMALL_SAMPLE = 30  # 低於這個 N,每 1 筆就差 3% 以上,只看方向

BIAS_NOTES = {
    SUMMARY_LAYER: "來源:職稱＋列表摘要(中位數約 123 字)＋電腦技能欄。"
    "**偏誤:文字短,出現率普遍低估**,只適合比較相對排名。",
    DETAIL_LAYER: "來源:JD 全文。**偏誤:只有粗篩通過的職缺才有全文,是跟履歷相符的子集** —— "
    "履歷上有的技能出現率會被高估,不能當作市場分布。",
    "weekly": "**偏誤:第一週與最後一週通常不完整;09-23 關鍵字從 AI 換成資料工程,"
    "之後的組成跟著變,不全是市場在變。**",
    "salary": "年薪 ÷12、日薪 ×22、時薪 ×176。**偏誤:願意揭露薪資的多半是中低薪職缺。**",
    "size": "以職缺計,非以公司計。**偏誤:大公司同時開多個缺,會被重複計算。**",
}


def layer_rows(rows: Sequence[JobRow], layer: str) -> list[JobRow]:
    return list(rows) if layer == SUMMARY_LAYER else [r for r in rows if r.detail_text]


def layer_text(row: JobRow, layer: str) -> str | None:
    return row.summary_text if layer == SUMMARY_LAYER else row.detail_text


@dataclass(frozen=True)
class SkillRate:
    skill: str
    count: int
    rate: float


def skill_rates(rows: Sequence[JobRow], layer: str, top: int = 20) -> list[SkillRate]:
    """per-job 去重的出現率。分母是該層的 N。"""
    population = layer_rows(rows, layer)
    if not population:
        return []
    counts: Counter[str] = Counter()
    for r in population:
        counts.update(match_skills(layer_text(r, layer)))
    n = len(population)
    return [SkillRate(k, v, v / n) for k, v in counts.most_common(top)]


def iso_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_rates(
    rows: Sequence[JobRow], skills: Sequence[str], layer: str = SUMMARY_LAYER
) -> list[dict]:
    """每週(週一起算)每個技能的出現率,附該週 N。"""
    by_week: dict[date, list[JobRow]] = {}
    for r in layer_rows(rows, layer):
        by_week.setdefault(iso_week_start(r.first_seen), []).append(r)
    out = []
    for week in sorted(by_week):
        group = by_week[week]
        matched = [match_skills(layer_text(r, layer)) for r in group]
        for skill in skills:
            hits = sum(skill in m for m in matched)
            out.append({"week": week, "skill": skill, "rate": hits / len(group), "n": len(group)})
    return out


def group_matrix(
    rows: Sequence[JobRow],
    key: Callable[[JobRow], str],
    layer: str,
    top: int = 15,
) -> tuple[list[str], list[dict]]:
    """技能 × 群 的出現率矩陣。技能取整體前 ``top`` 名,每格附該群 N。"""
    skills = [s.skill for s in skill_rates(rows, layer, top)]
    groups: dict[str, list[JobRow]] = {}
    for r in layer_rows(rows, layer):
        groups.setdefault(key(r), []).append(r)
    cells = []
    for name in sorted(groups):
        members = groups[name]
        matched = [match_skills(layer_text(r, layer)) for r in members]
        for skill in skills:
            hits = sum(skill in m for m in matched)
            cells.append(
                {"group": name, "skill": skill, "rate": hits / len(members), "n": len(members)}
            )
    return skills, cells


@dataclass(frozen=True)
class SalaryStats:
    n: int
    lows: list[int]  # 有揭露者的月薪下限
    highs: list[int]  # 只含有明確上限者(「以上」型不列入)
    open_ended: int

    @property
    def disclosure_rate(self) -> float:
        return len(self.lows) / self.n if self.n else 0.0


def salary_stats(rows: Sequence[JobRow]) -> SalaryStats:
    return SalaryStats(
        n=len(rows),
        lows=[r.salary_low_monthly for r in rows if r.salary_low_monthly is not None],
        highs=[r.salary_high_monthly for r in rows if r.salary_high_monthly is not None],
        open_ended=sum(r.salary_open_ended for r in rows),
    )


SIZE_BUCKETS: list[tuple[str, int, float]] = [
    ("<30", 0, 30),
    ("30–99", 30, 100),
    ("100–499", 100, 500),
    ("500–999", 500, 1000),
    ("1000–4999", 1000, 5000),
    ("5000+", 5000, float("inf")),
]
SIZE_UNKNOWN = "未提供"


def size_distribution(rows: Iterable[JobRow]) -> list[tuple[str, int]]:
    counts = Counter()
    for r in rows:
        if r.employees is None:
            counts[SIZE_UNKNOWN] += 1
            continue
        for label, lo, hi in SIZE_BUCKETS:
            if lo <= r.employees < hi:
                counts[label] += 1
                break
    return [(label, counts[label]) for label, _, _ in SIZE_BUCKETS] + [
        (SIZE_UNKNOWN, counts[SIZE_UNKNOWN])
    ]
