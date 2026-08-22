"""LLM 結構化輸出的 schema。

⚠️ Pydantic 的 ``model_json_schema()`` 直接餵給 OpenRouter 的 strict mode 常常失敗:
產出的 schema 有 ``$defs``/``$ref``、沒把所有欄位放進 ``required``、缺
``additionalProperties: false``。:func:`to_strict_schema` 做這三項後處理。

這是最容易踩的雷,所以 `tests/test_scoring.py` 有對應的 assert。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VERDICTS = ("strong_apply", "apply", "maybe", "skip")


class ScreenItem(BaseModel):
    job_no: str
    keep: bool
    rough: int = Field(ge=0, le=100)
    reason: str


class ScreenResult(BaseModel):
    results: list[ScreenItem]


class DeepScore(BaseModel):
    tech_fit: int = Field(ge=0, le=35)
    exp_fit: int = Field(ge=0, le=25)
    domain_fit: int = Field(ge=0, le=15)
    growth_fit: int = Field(ge=0, le=15)
    practical_fit: int = Field(ge=0, le=10)
    total_score: int = Field(ge=0, le=100)
    # 用 Literal 讓 JSON schema 帶上 enum 約束。
    #
    # 沒有 enum 時,便宜模型會自己發明值 —— 實測 22 筆裡有 9 筆吐出 `worth_a_shot`、
    # `worth_a_try` 這種 prompt 裡從沒出現過的字。而 verdict 是交叉驗證機制的一半,
    # 它失效等於少了一道防幻覺護欄。
    #
    # (原本擔心某些 provider 的 strict mode 不吃 enum 而用 str;但 `require_parameters`
    #  本來就只會路由到支援 structured output 的 provider,而且解析還有三層保險。)
    verdict: Literal["strong_apply", "apply", "maybe", "skip"]
    one_liner: str
    highlights: list[str]
    red_flags: list[str]
    resume_tip: str


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """把 ``$ref`` 展開成實際內容 —— 部分 provider 的 strict mode 不支援 ``$defs``。"""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            target = _inline_refs(defs.get(name, {}), defs)
            extra = {k: _inline_refs(v, defs) for k, v in node.items() if k != "$ref"}
            return {**target, **extra}
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _strictify(node: Any) -> Any:
    """每一層 object 都要 ``additionalProperties: false`` 且所有欄位都在 ``required``。"""
    if isinstance(node, dict):
        out = {k: _strictify(v) for k, v in node.items()}
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out["additionalProperties"] = False
            out["required"] = list(out["properties"].keys())
        return out
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    return node


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _strictify(_inline_refs(schema, defs))
