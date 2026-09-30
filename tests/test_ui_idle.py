"""Web UI 閒置自動關閉。

會踩到的錯法都很安靜:數錯連線(把瀏覽器那一側也算進去)只會讓 UI 永遠不關;
把「查不到」當成 0 條則會把正在看的人畫面切斷。
"""

from __future__ import annotations

from jobfinder.ui_idle import IdleWatcher, count_connections

# 中文 Windows 的 netstat -ano -p TCP 實際長相(標題列是 cp950,這裡只留結構)
NETSTAT = """
使用中連線

  協定   本機位址               外部位址               狀態           PID
  TCP    127.0.0.1:8501         0.0.0.0:0              LISTENING       40364
  TCP    127.0.0.1:8501         127.0.0.1:53422        ESTABLISHED     40364
  TCP    127.0.0.1:53422        127.0.0.1:8501         ESTABLISHED     9120
  TCP    127.0.0.1:8501         127.0.0.1:53430        TIME_WAIT       0
  TCP    127.0.0.1:18501        127.0.0.1:60000        ESTABLISHED     777
"""


def test_counts_only_the_server_side_of_each_connection():
    # 同一條連線出現兩次(伺服器側 + 瀏覽器側),只能算一條
    assert count_connections(NETSTAT, 8501) == 1


def test_listening_and_time_wait_are_not_open_tabs():
    only_listen = "  TCP    127.0.0.1:8501    0.0.0.0:0    LISTENING    1\n"
    assert count_connections(only_listen, 8501) == 0


def test_port_suffix_does_not_match_a_longer_port():
    # :18501 結尾也是 "8501",不能被當成 8501
    assert count_connections(NETSTAT, 18501) == 1
    assert count_connections("  TCP  127.0.0.1:18501  1.1.1.1:1  ESTABLISHED  1", 8501) == 0


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_shuts_down_only_after_continuous_idle():
    clock = Clock()
    w = IdleWatcher(600, now=clock)
    clock.t = 300
    assert w.observe(0) is False
    clock.t = 599
    assert w.observe(0) is False
    clock.t = 600
    assert w.observe(0) is True


def test_a_tab_resets_the_idle_timer():
    clock = Clock()
    w = IdleWatcher(600, now=clock)
    clock.t = 500
    assert w.observe(1) is False
    clock.t = 1000
    assert w.observe(0) is False  # 距離最後一次有分頁才 500 秒
    clock.t = 1100
    assert w.observe(0) is True


def test_unknown_count_is_treated_as_in_use():
    # netstat 失敗不能當成 0 —— 誤關會切斷正在看的人
    clock = Clock()
    w = IdleWatcher(600, now=clock)
    clock.t = 10_000
    assert w.observe(None) is False
    clock.t = 10_599
    assert w.observe(0) is False
