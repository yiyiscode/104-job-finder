"""已標投遞頁的分區、排序與週次篩選。純邏輯,不 import streamlit(測試要能在沒裝 [ui] 的環境跑)。

分區只看兩份使用者自己記的資料 —— 排定週次(``plans``)與送出日期(``sent``):

* 本週待投:排在本週或更早、還沒送出(過了那週沒送的留在這裡,不會消失)
* 之後幾週 / 未排週次 / 已送出
* 清單候補:投遞清單裡標「待確認」的 —— 還沒決定要投,所以不在上面四區
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from .decisions import Decision
from .rows import JobRow

#: 週次篩選的特殊值;其餘選項是某一週的週一(date)
WEEK_ALL = "all"
WEEK_DUE = "due"
WEEK_UNPLANNED = "unplanned"
WEEK_SENT = "sent"


def week_label(monday: date | None, this_week: date) -> str:
    """「本週(09/28)」「下週(10/05)」「+2 週(10/12)」「上週(09/21)」;``None`` →「未排」。"""
    if monday is None:
        return "未排"
    delta = (monday - this_week).days // 7
    name = {0: "本週", 1: "下週", -1: "上週"}.get(delta, f"{delta:+d} 週")
    return f"{name}({monday:%m/%d})"


@dataclass
class Board:
    due: list[JobRow] = field(default_factory=list)
    later: list[JobRow] = field(default_factory=list)
    unplanned: list[JobRow] = field(default_factory=list)
    done: list[JobRow] = field(default_factory=list)
    backups: list[JobRow] = field(default_factory=list)
    #: 標了投、沒送出,職缺卻已關閉。不進履歷修改的母體(投不成了,不必改履歷)
    closed: list[JobRow] = field(default_factory=list)

    @property
    def pool(self) -> list[JobRow]:
        """履歷修改 tab 的母體:所有標「投」的,深評高到低(同分依 本週 → 之後 → 未排 → 已送出)。"""
        return by_score(self.due + self.later + self.unplanned + self.done)


def by_score(rs: Iterable[JobRow]) -> list[JobRow]:
    """已標投遞頁的唯一排序:深評高到低,沒分數的排最後。穩定排序,同分保留原順序。"""
    return sorted(rs, key=lambda r: (r.deep_score is None, -(r.deep_score or 0)))


def build_board(
    rows: Iterable[JobRow],
    latest: Mapping[str, Decision],
    plans: Mapping[str, date],
    sent: Mapping[str, date],
    desires: Mapping[str, int],
    this_week: date,
    closed: Mapping[str, date] | None = None,
) -> Board:
    closed = closed or {}
    board = Board()
    for r in rows:
        decision = latest.get(r.job_no)
        if decision is None:
            continue
        if decision.status == "pending" and r.in_shortlist:
            board.backups.append(r)
        if decision.status != "apply":
            continue
        week = plans.get(r.job_no)
        if r.job_no in sent:
            board.done.append(r)  # 送出優先:送了之後才下架,仍然算投過
        elif r.job_no in closed:
            board.closed.append(r)
        elif week is None:
            board.unplanned.append(r)
        elif week <= this_week:
            board.due.append(r)
        else:
            board.later.append(r)

    # 2026-10-27 使用者要求:每一區都只看深評高到低(原本是 週次 → 想去 → 深評)
    board.due, board.later = by_score(board.due), by_score(board.later)
    board.unplanned, board.backups = by_score(board.unplanned), by_score(board.backups)
    board.done, board.closed = by_score(board.done), by_score(board.closed)
    return board


def week_options(board: Board, plans: Mapping[str, date]) -> list[str | date]:
    """篩選選項:全部 / 本週待投 / 之後每一週 / 未排 / 已送出。空的區不列。"""
    options: list[str | date] = [WEEK_ALL]
    if board.due:
        options.append(WEEK_DUE)
    options += sorted({plans[r.job_no] for r in board.later})
    if board.unplanned:
        options.append(WEEK_UNPLANNED)
    if board.done:
        options.append(WEEK_SENT)
    return options


def default_week_option(options: list[str | date]) -> int:
    """週次篩選的預設:本週待投;本週沒有待投的(選項裡沒有)就退回「全部」。"""
    return options.index(WEEK_DUE) if WEEK_DUE in options else 0


def option_label(
    option: str | date, board: Board, plans: Mapping[str, date], this_week: date
) -> str:
    counts = {
        WEEK_ALL: len(board.pool),
        WEEK_DUE: len(board.due),
        WEEK_UNPLANNED: len(board.unplanned),
        WEEK_SENT: len(board.done),
    }
    names = {
        WEEK_ALL: "全部",
        WEEK_DUE: "本週待投",
        WEEK_UNPLANNED: "未排週次",
        WEEK_SENT: "已送出",
    }
    if isinstance(option, date):
        n = sum(plans.get(r.job_no) == option for r in board.later)
        return f"{week_label(option, this_week)} · {n}"
    return f"{names[option]} · {counts[option]}"


#: 「排入週次」可選到幾週後(本週 + 5 週;清單排程是 4 週)
PLAN_WEEKS_AHEAD = 5


def plan_week_choices(this_week: date) -> list[date | None]:
    """批次排週次的選項:本週 → +5 週,**「取消週次」(None)放最後**。

    放第一個的話它就是預設值 —— 勾幾筆、沒改選單就按下去,會把原本的週次全部清掉。
    """
    return [this_week + timedelta(weeks=i) for i in range(PLAN_WEEKS_AHEAD + 1)] + [None]


def default_plan_week(
    selected: Iterable[JobRow], plans: Mapping[str, date], choices: list[date | None]
) -> int:
    """預設選哪一個(索引)。勾選的全都排在同一週、且那週在選項裡 → 那一週;否則本週。

    絕不預設成「取消週次」。
    """
    weeks = {plans.get(r.job_no) for r in selected}
    if len(weeks) == 1 and (week := weeks.pop()) is not None and week in choices:
        return choices.index(week)
    return 0


def filter_pool(board: Board, option: str | date, plans: Mapping[str, date]) -> list[JobRow]:
    if option == WEEK_ALL:
        return board.pool
    if option == WEEK_DUE:
        return board.due
    if option == WEEK_UNPLANNED:
        return board.unplanned
    if option == WEEK_SENT:
        return board.done
    return [r for r in board.later if plans.get(r.job_no) == option]


#: 清單候補分流的三個選項。「維持」= 這次不決定,不寫入任何東西
TRIAGE_APPLY, TRIAGE_SKIP, TRIAGE_HOLD = "apply", "skip", "hold"


def triage_writes(
    picks: Mapping[str, tuple[str | None, str | None]],
) -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """清單候補一次分流 → (要寫入的 (job_no, status, reason), 標不投卻沒選原因的 job_no)。

    沒選或選「維持」的不寫:待確認本來就是它現在的狀態,再寫一筆只會讓紀錄變雜。
    不投必須有原因 —— 原因是之後檢討閘門的唯一資料,一筆都不能漏,所以有缺就整批不存。
    """
    writes: list[tuple[str, str, str | None]] = []
    missing: list[str] = []
    for job_no, (choice, reason) in picks.items():
        if choice == TRIAGE_APPLY:
            writes.append((job_no, "apply", None))
        elif choice == TRIAGE_SKIP:
            if reason:
                writes.append((job_no, "skip", reason))
            else:
                missing.append(job_no)
    return writes, missing


PAGE_SIZE = 20


def page_bounds(total: int, page: int, size: int = PAGE_SIZE) -> tuple[int, int, int]:
    """(start, end, 修正後的頁碼)。頁碼超出範圍就夾回去 —— 標記完列數變少時常發生。"""
    pages = max(1, -(-total // size))
    page = min(max(page, 0), pages - 1)
    return page * size, min(total, (page + 1) * size), page


def page_count(total: int, size: int = PAGE_SIZE) -> int:
    return max(1, -(-total // size))
