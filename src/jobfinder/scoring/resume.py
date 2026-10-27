"""履歷載入。

粗篩是批次的,履歷 context 是每個 batch 的固定成本,所以粗篩用**壓縮版**;
深評逐筆呼叫,用完整版才給得出具體的匹配理由。

壓縮版是從 `profile.md` 自動摘出來的,不是另外手寫一份 —— 手寫的那份遲早會跟履歷不同步。

**參考資料(``paths.references``,例如成績單)只進深評。** 不併進 ``full``:命中率以
``full`` 的雜湊為鍵,併進去會讓全部命中率失效重算;粗篩的壓縮版也不需要修課細節。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resume:
    full: str
    brief: str
    references: str = ""

    @property
    def deep(self) -> str:
        """深評用:履歷全文 + 參考資料。"""
        return f"{self.full}\n\n{self.references}" if self.references else self.full


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


def load_resume(path: str | Path, references: Iterable[str | Path] = ()) -> Resume:
    """``references`` 的相對路徑以履歷檔所在目錄為準。"""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"找不到履歷檔 {p}。評分完全依賴它,沒有它整個日報沒有意義。")
    full = p.read_text(encoding="utf-8").strip()
    if not full:
        raise ConfigError(f"履歷檔 {p} 是空的")
    extras: list[str] = []
    for ref in references:
        r = Path(ref) if Path(ref).is_absolute() else p.parent / ref
        if not r.is_file():
            # 不擋啟動:參考資料是加分項,少了它日報照樣有意義(而且多半沒進版控)
            log.warning("找不到參考資料 %s,本次深評不附", r)
            continue
        extras.append(r.read_text(encoding="utf-8").strip())
    return Resume(full=full, brief=_digest(full), references="\n\n".join(x for x in extras if x))
