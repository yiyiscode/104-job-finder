"""`scripts/` 的煙霧測試。

存在的理由很具體:改領域模型時漏改腳本,測試會全綠而腳本在使用者手上壞掉。
這實際發生過 —— `JobSummary` 加了必填的 `detail_id`,`send_test_message.py` 的
範例職缺沒跟著改;`urls.build_search_url` 改名,`probe_api.py` 的 import 沒跟著改。
兩個都是使用者跑下去才炸的。

這裡只驗「模組載入 + `--help` 走得完」,不真的執行任何動作。夠便宜,也夠擋這類錯誤。
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((PROJECT_ROOT / "scripts").glob("*.py"))


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """跑腳本並強制用 UTF-8 收輸出。

    腳本會印中文,而 Windows 的預設是 cp950 —— 不指定編碼的話,
    子行程的輸出會在解碼時炸掉,測試失敗的原因跟腳本本身毫無關係。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        cwd=PROJECT_ROOT,
        env=env,
    )


def test_there_are_scripts_to_check():
    assert SCRIPTS, "找不到任何腳本 —— 這個測試檔本身可能已經失效"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_imports_cleanly(script: Path):
    """模組層的 import 與常數要能跑完。

    `send_test_message.py` 在模組層就建了一個範例 `JobSummary` —— 領域模型改了
    卻沒改它,這裡就會抓到。
    """
    spec = importlib.util.spec_from_file_location(f"_smoke_{script.stem}", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):  # argparse 之類的正常退出
        spec.loader.exec_module(module)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_help_works(script: Path):
    """`--help` 必須由 argparse 處理掉。

    只檢查 exit code 是不夠的:沒有 argparse 的腳本會忽略 --help、把整個腳本跑一遍,
    然後正常結束 —— exit code 一樣是 0。所以要驗 stdout 有 usage。
    """
    result = run_script(str(script), "--help")
    assert result.returncode == 0, f"{script.name} --help 失敗:\n{result.stderr[-1500:]}"
    assert "usage:" in result.stdout, (
        f"{script.name} 的 --help 沒有印出 usage —— 代表它沒有 argparse,"
        f"而是把整個腳本跑了一遍。這類腳本會連 104 或送 Telegram,不能這樣。"
        f" 實際輸出:{result.stdout[:400]}"
    )


def test_probe_api_refuses_to_run_when_fixtures_exist():
    """SPEC.md 規則 6:錄過就別再錄。這個防呆壞掉會讓人不小心多打 104 一輪。"""
    result = run_script("scripts/probe_api.py")
    assert result.returncode == 0
    assert "已經有錄好的 fixture" in result.stdout
    assert "--force" in result.stdout
