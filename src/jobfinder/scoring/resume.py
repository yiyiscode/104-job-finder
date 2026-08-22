"""履歷載入。

粗篩是批次的,履歷 context 是每個 batch 的固定成本,所以粗篩用**壓縮版**;
深評逐筆呼叫,用完整版才給得出具體的匹配理由。

壓縮版是從 `profile.md` 自動摘出來的,不是另外手寫一份 —— 手寫的那份遲早會跟履歷不同步。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError


@dataclass(frozen=True)
class Resume:
    full: str
    brief: str


def _digest(markdown: str, per_section: int = 260) -> str:
    """每個 ``##`` 區塊各留開頭一小段,壓成粗篩用的摘要。"""
    sections = re.split(r"\n(?=##\s)", markdown.strip())
    parts: list[str] = []
    for section in sections:
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        if not lines:
            continue
        heading = lines[0].lstrip("#").strip()
        body = " ".join(lines[1:])
        # 表格與分隔線在摘要裡只是雜訊
        body = re.sub(r"\|[\s\-:|]+\|", " ", body)
        body = re.sub(r"\s{2,}", " ", body).strip()
        if len(body) > per_section:
            body = body[:per_section].rstrip() + "…"
        parts.append(f"[{heading}] {body}" if body else f"[{heading}]")
    return "\n".join(parts)


def load_resume(path: str | Path) -> Resume:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"找不到履歷檔 {p}。評分完全依賴它,沒有它整個日報沒有意義。")
    full = p.read_text(encoding="utf-8").strip()
    if not full:
        raise ConfigError(f"履歷檔 {p} 是空的")
    return Resume(full=full, brief=_digest(full))
