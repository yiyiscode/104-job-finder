"""LLM 回的中文統一成台灣繁體。

模型偶爾會整句或夾幾個字吐簡體(實測 2026-10-15:5,225 個欄位裡「资/与/经/应/职…」
都有出現),prompt 寫了「一律繁體中文」也擋不住。改 prompt 會讓分數位移,
所以在**解析結果的那一刻**轉換,兩個 provider、所有 schema 都走同一個入口。

⚠️ **不能整段無條件丟進 OpenCC。** ``s2tw`` 套在已經是繁體的文字上會改壞字:
「台積電」→「臺積電」、「了解」→「瞭解」。所以先判斷欄位裡有沒有「只有簡體才會用」
的字,有才轉;轉完再把台灣慣用寫法換回來。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AfterValidator

#: 簡繁兩邊都是正常用字。OpenCC 會把它們轉掉,但出現在繁體文章裡不代表是簡體
_SHARED = set("台里后云干么准冲范丑征占几斗伙余涂咸朴杰采雇")

#: s2tw 轉完要換回的台灣慣用寫法
_TW_FIXUPS = (("臺", "台"), ("瞭解", "了解"))


@lru_cache(maxsize=1)
def _converters():
    import opencc

    return opencc.OpenCC("s2tw"), opencc.OpenCC("t2s")


@lru_cache(maxsize=4096)
def _is_simplified_only(ch: str) -> bool:
    if ch in _SHARED or not ("一" <= ch <= "鿿"):
        return False
    s2tw, t2s = _converters()
    # t2s 不動 = 它不是有簡體對應的繁體字;s2tw 會動 = 它有繁體寫法
    return t2s.convert(ch) == ch and s2tw.convert(ch) != ch


def has_simplified(text: str) -> bool:
    return any(_is_simplified_only(ch) for ch in text)


def to_traditional(text: str) -> str:
    """含簡體字才轉成台灣繁體;已經是繁體的原樣回傳。"""
    if not text or not has_simplified(text):
        return text
    out = _converters()[0].convert(text)
    for src, dst in _TW_FIXUPS:
        out = out.replace(src, dst)
    return out


#: 給 LLM 回應 schema 用的字串型別。JSON schema 仍是普通 string,不影響 strict mode
ZhText = Annotated[str, AfterValidator(to_traditional)]
