"""標記(decisions.db)與 jobs.db 快照。

兩件事一定要有測試:
* 「不投必填原因」—— 原因是之後檢討閘門的唯一資料,Python 端與 DB 端都要擋
* 快照不能持有原檔 —— 否則 Windows 上 pipeline 的 ``os.replace`` 會失敗,
  而 checkpoint 失敗只記 log,當次資料與熔斷器狀態全部遺失
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import pytest

from jobfinder.webui.decisions import DecisionStore
from jobfinder.webui.snapshot import connect_readonly, take_snapshot

NOW = datetime(2026, 9, 22, 9, 0)


# ── decisions ──
def test_append_only_latest_wins(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    store.append("J1", "pending", None, "", NOW)
    store.append("J1", "apply", None, "  週五投  ", NOW)
    store.append("J2", "skip", "SI接案", "", NOW)

    latest = store.latest()
    assert latest["J1"].status == "apply" and latest["J1"].note == "週五投"
    assert latest["J2"].reason == "SI接案"
    assert [d.status for d in store.history("J1")] == ["pending", "apply"]


@pytest.mark.parametrize("reason", [None, "", "我不喜歡"])
def test_skip_requires_valid_reason(tmp_path, reason):
    store = DecisionStore(tmp_path / "decisions.db")
    with pytest.raises(ValueError):
        store.append("J1", "skip", reason, "", NOW)
    assert store.latest() == {}


def test_reason_only_for_skip(tmp_path):
    with pytest.raises(ValueError):
        DecisionStore(tmp_path / "decisions.db").append("J1", "apply", "SI接案", "", NOW)


def test_db_check_blocks_skip_without_reason_even_bypassing_python(tmp_path):
    """有人繞過 ``append`` 直接寫 SQL,DB 的 CHECK 仍然要擋。"""
    path = tmp_path / "decisions.db"
    DecisionStore(path)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decisions (job_no, status, reason, decided_at)"
            " VALUES ('J1', 'skip', NULL, 'x')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decisions (job_no, status, reason, decided_at)"
            " VALUES ('J1', 'apply', 'SI接案', 'x')"
        )
    conn.close()


def test_desire_is_independent_of_decision_and_latest_wins(tmp_path):
    """想去程度選填、跟投/不投分開:可以只評分不標記,之後才分析得了「很想去卻不投」。"""
    store = DecisionStore(tmp_path / "decisions.db")
    store.rate("J1", 2, NOW)
    store.rate("J1", 5, NOW)
    store.append("J2", "skip", "SI接案", "", NOW)
    assert store.latest_desires() == {"J1": 5}
    assert "J1" not in store.latest()


@pytest.mark.parametrize("bad", [0, 6])
def test_desire_out_of_range_is_rejected_in_python_and_db(tmp_path, bad):
    path = tmp_path / "decisions.db"
    with pytest.raises(ValueError):
        DecisionStore(path).rate("J1", bad, NOW)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO desires (job_no, desire, rated_at) VALUES ('J1', ?, 'x')", (bad,))
    conn.close()


def test_existing_decisions_db_gets_desires_table(tmp_path):
    """舊的 decisions.db 沒有 desires 表 —— 開 store 時要自動補上,不能炸。"""
    path = tmp_path / "decisions.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, job_no TEXT)")
    conn.commit()
    conn.close()
    DecisionStore(path).rate("J1", 4, NOW)
    assert DecisionStore(path).latest_desires() == {"J1": 4}


def test_closed_is_append_only_and_can_be_undone(tmp_path):
    from datetime import date

    store = DecisionStore(tmp_path / "decisions.db")
    store.mark_closed("J1", date(2026, 9, 22), NOW)
    store.mark_closed("J2", date(2026, 9, 22), NOW)
    store.mark_closed("J2", None, NOW)  # 撤銷
    assert store.latest_closed() == {"J1": date(2026, 9, 22)}
    with pytest.raises(ValueError):
        store.mark_closed("J3", date(2026, 9, 23), NOW)  # 未來日期


def test_store_does_not_hold_file_open(tmp_path):
    """短交易:每個操作後連線都關掉,檔案可以被搬走。"""
    path = tmp_path / "decisions.db"
    store = DecisionStore(path)
    store.append("J1", "pending", None, "", NOW)
    store.latest()
    os.replace(path, tmp_path / "moved.db")


# ── snapshot ──
def _make_db(path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read(snap) -> str:
    conn = connect_readonly(snap)
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def test_snapshot_lets_pipeline_replace_original(tmp_path):
    """模擬 pipeline 的 checkpoint:UI 取完快照、甚至正開著快照時,原檔都要能被 os.replace。"""
    db = tmp_path / "jobs.db"
    _make_db(db, "v1")
    snap = take_snapshot(db, tmp_path / "cache")
    ui_conn = connect_readonly(snap)  # UI 正開著快照

    new = tmp_path / "jobs.db.new"
    _make_db(new, "v2")
    os.replace(new, db)  # 不能因 UI 而失敗
    ui_conn.close()
    assert _read(take_snapshot(db, tmp_path / "cache")) == "v2"


def test_snapshot_reused_when_unchanged_and_old_ones_cleaned(tmp_path):
    db = tmp_path / "jobs.db"
    cache = tmp_path / "cache"
    _make_db(db, "v1")
    first = take_snapshot(db, cache)
    assert take_snapshot(db, cache) == first

    _make_db(db, "v2-longer-value-to-change-size")
    os.utime(db, ns=(first.stat().st_mtime_ns + 10**9,) * 2)
    second = take_snapshot(db, cache)
    assert second != first and not first.exists()
    assert list(cache.glob("*.db")) == [second]


def test_snapshot_skips_original_during_checkpoint(tmp_path):
    """jobs.db.tmp 存在 = pipeline 正在寫回,這時不碰原檔,沿用上一份。"""
    db = tmp_path / "jobs.db"
    cache = tmp_path / "cache"
    _make_db(db, "v1")
    first = take_snapshot(db, cache)
    (tmp_path / "jobs.db.tmp").write_bytes(b"partial")
    _make_db(db, "v2")
    assert take_snapshot(db, cache) == first


def test_snapshot_is_read_only(tmp_path):
    db = tmp_path / "jobs.db"
    _make_db(db, "v1")
    conn = connect_readonly(take_snapshot(db, tmp_path / "cache"))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES ('x')")
    conn.close()


def test_snapshot_missing_db_is_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="pipeline"):
        take_snapshot(tmp_path / "jobs.db", tmp_path / "cache")
