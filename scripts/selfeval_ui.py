"""啟動自評 app(方案 B 卡片)。跟 Job Finder UI 畫面分開、資料共用 decisions.db。

    python scripts/selfeval_ui.py              # http://127.0.0.1:8502
    python scripts/selfeval_ui.py --port 8510

**永遠只綁 127.0.0.1** —— 讀的是實際的求職資料。不連 104。
不放在 ``jobfinder`` 子指令裡:cli.py 另一條分支正在改,獨立腳本不會互相衝突。
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "jobfinder" / "webui" / "selfeval_app.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="啟動自評 app(只綁 127.0.0.1、不連 104)")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--data-dir", default=str(ROOT / "local_data"))
    parser.add_argument("--resume", default=str(ROOT / "profile-de.md"), help="技能自評寫回的履歷")
    parser.add_argument(
        "--idle-minutes",
        type=float,
        default=0,
        help="連續這麼多分鐘沒有分頁連著就自動結束(0 = 不自動結束;桌面捷徑帶 10)",
    )
    args = parser.parse_args(argv)

    if importlib.util.find_spec("streamlit") is None:
        print("需要先安裝 UI 依賴:uv sync --all-extras", file=sys.stderr)
        return 2
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.address", "127.0.0.1",
        "--server.port", str(args.port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--", "--data-dir", args.data_dir, "--resume", args.resume,
    ]  # fmt: skip
    print(f"自評 app:http://127.0.0.1:{args.port}(Ctrl+C 結束)")
    if args.idle_minutes <= 0:
        return subprocess.call(cmd)
    return _run_until_idle(cmd, args.port, args.idle_minutes)


def _run_until_idle(cmd: list[str], port: int, idle_minutes: float) -> int:
    """同 ``jobfinder ui --idle-minutes``:關分頁不會停 Streamlit,所以要自己看連線數。"""
    from jobfinder.ui_idle import IdleWatcher, active_connections

    print(f"關掉所有分頁 {idle_minutes:g} 分鐘後自動結束")
    watcher = IdleWatcher(idle_minutes * 60)
    proc = subprocess.Popen(cmd)
    try:
        while True:
            try:
                return proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            if watcher.observe(active_connections(port)):
                print(f"已經 {idle_minutes:g} 分鐘沒有分頁連著,自動結束自評 app")
                return 0
    finally:
        # 閒置、Ctrl+C、任何例外都走這裡 —— 不能把 Streamlit 孤兒留在背景
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
