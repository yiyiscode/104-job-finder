"""Web UI 的閒置自動關閉。

關掉瀏覽器分頁**不會**讓 Streamlit 結束 —— 它會一直在背景聽 8501。桌面捷徑是
「隨用隨開」,所以由 ``jobfinder ui`` 在外面看著:連續 N 分鐘沒有任何分頁連著,
就把 Streamlit 關掉。

「有沒有分頁連著」看的是 ``127.0.0.1:{port}`` 上 ESTABLISHED 的 TCP 連線:
每個開著的分頁都掛著一條 WebSocket。刻意不用 Streamlit 的內部 API
(``runtime._session_mgr``)—— 那不是公開介面,升版會默默壞掉。

判斷不出來(netstat 失敗)時**當成有人在用**:誤關會讓正在看的人畫面斷掉,
晚關只是多佔一點記憶體。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable


def count_connections(netstat_output: str, port: int) -> int:
    """數出本機端是 ``{port}`` 且狀態為 ESTABLISHED 的連線。

    只看「本機位址」那一欄 —— 遠端位址是 ``:{port}`` 的是瀏覽器那一側,
    同一條連線會在 netstat 出現兩次,兩邊都數會算成兩倍。
    """
    suffix = f":{port}"
    n = 0
    for line in netstat_output.splitlines():
        # TCP  本機位址  遠端位址  狀態  PID
        parts = line.split()
        if (
            len(parts) >= 4
            and parts[0] == "TCP"
            and parts[3] == "ESTABLISHED"
            and parts[1].endswith(suffix)
        ):
            n += 1
    return n


def active_connections(port: int) -> int | None:
    """目前連著的分頁數;查不到回 ``None``(呼叫端要當成「有人在用」)。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=15,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # 標題列是系統語系(中文 Windows 是 cp950),連線列本身是 ASCII
    return count_connections(out.decode("ascii", errors="replace"), port)


class IdleWatcher:
    """連續 ``idle_seconds`` 秒都沒有連線才判定閒置。

    從啟動那一刻就開始計時 —— 瀏覽器一直沒連上來(例如開瀏覽器失敗)
    也會在 N 分鐘後收掉,不會留一個沒人知道的服務。
    """

    def __init__(self, idle_seconds: float, now: Callable[[], float] = time.monotonic) -> None:
        self._idle_seconds = idle_seconds
        self._now = now
        self._last_seen = now()

    def observe(self, connections: int | None) -> bool:
        """回報一次觀測;回傳 True 代表該關了。"""
        t = self._now()
        if connections is None or connections > 0:
            self._last_seen = t
            return False
        return t - self._last_seen >= self._idle_seconds
