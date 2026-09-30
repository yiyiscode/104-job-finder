"""已標投遞頁的分區、排序與週次篩選(webui/planning.py)。"""

from __future__ import annotations

from datetime import date

from webui_helpers import row

from jobfinder.webui import planning
from jobfinder.webui.decisions import Decision

THIS_WEEK = date(2026, 9, 28)
NEXT_WEEK = date(2026, 10, 5)
WEEK_3 = date(2026, 10, 12)


def _decision(job_no: str, status: str = "apply") -> Decision:
    return Decision(1, job_no, status, "其他" if status == "skip" else None, "", "2026-09-28")


def _board(rows, statuses, plans=None, sent=None, desires=None):
    latest = {k: _decision(k, s) for k, s in statuses.items()}
    return planning.build_board(rows, latest, plans or {}, sent or {}, desires or {}, THIS_WEEK)


def test_sections_by_week_and_sent():
    rows = [row(job_no=n) for n in "ABCDEF"]
    rows[5].in_shortlist = True
    board = _board(
        rows,
        {"A": "apply", "B": "apply", "C": "apply", "D": "apply", "E": "skip", "F": "pending"},
        plans={"A": date(2026, 9, 21), "B": NEXT_WEEK, "D": THIS_WEEK},
        sent={"D": date(2026, 9, 27)},
    )
    assert [r.job_no for r in board.due] == ["A"]  # 上週排的還沒送 → 留在本週待投
    assert [r.job_no for r in board.later] == ["B"]
    assert [r.job_no for r in board.unplanned] == ["C"]
    assert [r.job_no for r in board.done] == ["D"]  # 送出優先於週次
    assert [r.job_no for r in board.backups] == ["F"]  # 不投的不出現
    assert [r.job_no for r in board.pool] == ["A", "B", "C", "D"]


def test_pending_outside_shortlist_is_not_a_backup():
    """候選清單裡隨手標的「待確認」不算清單候補。"""
    assert _board([row(job_no="A")], {"A": "pending"}).backups == []


def test_order_is_week_then_desire_then_score():
    rows = [row(job_no="A", score=90), row(job_no="B", score=70), row(job_no="C", score=None)]
    rows.append(row(job_no="D", score=99))
    board = _board(
        rows,
        dict.fromkeys("ABCD", "apply"),
        plans={"A": NEXT_WEEK, "B": NEXT_WEEK, "C": NEXT_WEEK, "D": WEEK_3},
        desires={"B": 5},
    )
    assert [r.job_no for r in board.later] == ["B", "A", "C", "D"]


def test_done_is_newest_sent_first():
    rows = [row(job_no="A"), row(job_no="B")]
    board = _board(
        rows, dict.fromkeys("AB", "apply"), sent={"A": date(2026, 9, 1), "B": date(2026, 9, 20)}
    )
    assert [r.job_no for r in board.done] == ["B", "A"]


def test_week_filter_options_and_counts():
    rows = [row(job_no=n) for n in "ABCD"]
    plans = {"A": THIS_WEEK, "B": NEXT_WEEK, "C": WEEK_3}
    board = _board(rows, dict.fromkeys("ABCD", "apply"), plans=plans)
    options = planning.week_options(board, plans)
    assert options == [
        planning.WEEK_ALL,
        planning.WEEK_DUE,
        NEXT_WEEK,
        WEEK_3,
        planning.WEEK_UNPLANNED,
    ]
    labels = [planning.option_label(o, board, plans, THIS_WEEK) for o in options]
    assert labels == [
        "全部 · 4",
        "本週待投 · 1",
        "下週(10/05) · 1",
        "+2 週(10/12) · 1",
        "未排週次 · 1",
    ]
    assert [r.job_no for r in planning.filter_pool(board, NEXT_WEEK, plans)] == ["B"]
    assert [r.job_no for r in planning.filter_pool(board, planning.WEEK_ALL, plans)] == list("ABCD")


def test_cancel_week_is_last_so_it_is_never_the_default():
    """「取消週次」若排第一就是預設值:勾幾筆沒改選單直接按,原本的週次會全被清掉。"""
    choices = planning.plan_week_choices(THIS_WEEK)
    assert choices[0] == THIS_WEEK and choices[-1] is None
    assert len(choices) == planning.PLAN_WEEKS_AHEAD + 2


def test_default_plan_week_keeps_a_shared_week_else_this_week():
    choices = planning.plan_week_choices(THIS_WEEK)
    a, b, c = row(job_no="A"), row(job_no="B"), row(job_no="C")
    plans = {"A": NEXT_WEEK, "B": NEXT_WEEK, "C": WEEK_3}
    assert choices[planning.default_plan_week([a, b], plans, choices)] == NEXT_WEEK
    assert choices[planning.default_plan_week([a, c], plans, choices)] == THIS_WEEK  # 不同週
    unplanned = row(job_no="D")
    assert choices[planning.default_plan_week([unplanned], plans, choices)] == THIS_WEEK
    far = {"A": date(2027, 1, 4)}  # 不在選項裡的週
    assert choices[planning.default_plan_week([a], far, choices)] == THIS_WEEK


def test_week_label():
    assert planning.week_label(None, THIS_WEEK) == "未排"
    assert planning.week_label(date(2026, 9, 21), THIS_WEEK) == "上週(09/21)"
    assert planning.week_label(THIS_WEEK, THIS_WEEK) == "本週(09/28)"
