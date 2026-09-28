"""Web UI 的邊界護欄。護欄沒有測試等於沒有護欄。

* UI 不能 import 任何會碰 104 的東西 —— 抓取的請求預算、節流、熔斷只在 pipeline 裡,
  一個 UI 按鈕就能繞過三道護欄
* UI 不能 import ``storage`` —— ``Database`` 結束時會 checkpoint,把整個 jobs.db 寫回
* 邏輯模組不能 import streamlit / pandas —— 測試環境沒裝 ``[ui]`` 也要能跑
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[1] / "src" / "jobfinder" / "webui"
MODULES = sorted(WEBUI.glob("*.py"))

FORBIDDEN_EVERYWHERE = ("scrape", "http_source", "pipeline", "storage", "httpx", "patchright")
UI_ONLY = ("streamlit", "pandas", "altair")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_modules_found():
    assert {"app.py", "gates.py", "decisions.py", "snapshot.py"} <= {p.name for p in MODULES}


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_scraping_or_jobs_db_writer(path):
    for name in _imports(path):
        parts = name.lstrip(".").split(".")
        bad = [p for p in parts if p in FORBIDDEN_EVERYWHERE]
        assert not bad, f"{path.name} import 了 {name} —— UI 不得碰 104 或 jobs.db 的寫入端"


@pytest.mark.parametrize("path", [p for p in MODULES if p.name != "app.py"], ids=lambda p: p.name)
def test_logic_modules_do_not_need_ui_extras(path):
    for name in _imports(path):
        assert name.split(".")[0] not in UI_ONLY, f"{path.name} import 了 {name}"


HITRATE = WEBUI.parent / "hitrate"


@pytest.mark.parametrize("path", sorted(HITRATE.glob("*.py")), ids=lambda p: p.name)
def test_hitrate_never_touches_104_or_jobs_db_writer(path):
    """命中率只打 OpenRouter。它跟 pipeline 同一個排程裡跑,更不能順手碰 104。"""
    for name in _imports(path):
        parts = name.lstrip(".").split(".")
        bad = [p for p in parts if p in FORBIDDEN_EVERYWHERE]
        assert not bad, f"hitrate/{path.name} import 了 {name}"


SHORTLIST = WEBUI.parent / "shortlist"
#: 投遞清單裡只有 fetch.py 會連 104;UI 與命中率讀 store.py 的資料
SHORTLIST_NETWORK = "fetch"


@pytest.mark.parametrize(
    "path", [*MODULES, *sorted(HITRATE.glob("*.py"))], ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_ui_and_hitrate_never_import_shortlist_fetch(path):
    for name in _imports(path):
        parts = name.lstrip(".").split(".")
        assert not ("shortlist" in parts and SHORTLIST_NETWORK in parts), (
            f"{path.parent.name}/{path.name} import 了 {name} —— 那是會連 104 的補抓"
        )


@pytest.mark.parametrize(
    "path",
    [p for p in sorted(SHORTLIST.glob("*.py")) if p.stem != SHORTLIST_NETWORK],
    ids=lambda p: p.name,
)
def test_shortlist_store_side_never_touches_104_or_jobs_db_writer(path):
    for name in _imports(path):
        parts = name.lstrip(".").split(".")
        bad = [p for p in parts if p in FORBIDDEN_EVERYWHERE]
        assert not bad, f"shortlist/{path.name} import 了 {name}"


def test_app_binds_localhost_only():
    """jobs.db 含實際求職資料,不能對區網公開。"""
    cli = (WEBUI.parent / "cli.py").read_text(encoding="utf-8")
    assert '"--server.address", "127.0.0.1"' in cli
