from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def raw_config() -> dict[str, Any]:
    """專案真正在用的 config.yaml。測試直接吃它,設定壞掉時測試會先叫。"""
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def config_dict(raw_config: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(raw_config)


@pytest.fixture
def write_config(tmp_path: Path):
    """把(可能被動過手腳的)設定 dict 寫成暫存檔,回傳路徑。"""

    def _write(data: dict[str, Any]) -> Path:
        p = tmp_path / "config.yaml"
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
        return p

    return _write
