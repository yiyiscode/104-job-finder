"""驗證 config.yaml 裡的 OpenRouter model id 真的存在且支援 structured output。

**不要憑記憶寫 model id。** 模型汰換很快,寫死一個已經下架的 id,症狀會是每天早上
收到一則「LLM 失敗」告警,而不是明顯的啟動錯誤。

    python scripts/check_models.py
    python scripts/check_models.py --search gemini    # 找可用的便宜模型
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from jobfinder.config import Secrets, load_config  # noqa: E402
from jobfinder.logging_conf import use_utf8_console  # noqa: E402

MODELS_URL = "https://openrouter.ai/api/v1/models"


def fetch_models() -> dict[str, dict]:
    secrets = Secrets()
    headers = {}
    if secrets.openrouter_api_key:
        headers["Authorization"] = f"Bearer {secrets.openrouter_api_key}"
    response = httpx.get(MODELS_URL, headers=headers, timeout=30)
    response.raise_for_status()
    return {m["id"]: m for m in response.json().get("data", [])}


def _price(model: dict) -> str:
    pricing = model.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt", 0)) * 1_000_000
        completion = float(pricing.get("completion", 0)) * 1_000_000
    except (TypeError, ValueError):
        return "價格未知"
    return f"${prompt:.3f}/M in, ${completion:.3f}/M out"


def check_one(models: dict[str, dict], label: str, model_id: str) -> bool:
    model = models.get(model_id)
    if model is None:
        print(f"  ❌ {label}: {model_id!r} 不存在於 OpenRouter")
        near = [m for m in models if model_id.split("/")[-1][:8] in m]
        if near:
            print(f"       名稱相近的:{', '.join(sorted(near)[:5])}")
        return False

    supported = model.get("supported_parameters") or []
    structured = "structured_outputs" in supported or "response_format" in supported
    icon = "✅" if structured else "⚠️ "
    print(f"  {icon} {label}: {model_id}")
    print(f"       {_price(model)} · context {model.get('context_length', '?')}")
    if not structured:
        print(
            "       這個模型沒宣告支援 structured_outputs,會一路靠容錯解析與修復輪撐著,建議換一個。"
        )
    return structured


def main() -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--search", help="列出名稱含此字串且支援 structured output 的模型")
    args = parser.parse_args()

    try:
        models = fetch_models()
    except httpx.HTTPError as exc:
        print(f"❌ 無法取得 OpenRouter 模型清單:{exc}")
        return 1

    print(f"OpenRouter 目前有 {len(models)} 個模型\n")

    if args.search:
        print(f"── 名稱含 {args.search!r} 且支援 structured output ──")
        for model_id, model in sorted(models.items()):
            if args.search.lower() not in model_id.lower():
                continue
            supported = model.get("supported_parameters") or []
            if "structured_outputs" not in supported:
                continue
            print(f"  {model_id:<55} {_price(model)}")
        return 0

    cfg = load_config(args.config)
    print("── config.yaml 使用的模型 ──")
    ok = all(
        [
            check_one(models, "粗篩 llm.screen.model", cfg.llm.screen.model),
            check_one(models, "深評 llm.deep.model", cfg.llm.deep.model),
        ]
    )
    if cfg.llm.fallback_model:
        ok = check_one(models, "備援 llm.fallback_model", cfg.llm.fallback_model) and ok

    if not ok:
        print(
            "\n有模型不可用或不支援 structured output。"
            "\n用 `python scripts/check_models.py --search gemini`(或 mini / flash / haiku)"
            "\n找一個便宜且支援的,改進 config.yaml 的 llm.screen.model / llm.deep.model。"
        )
        return 1

    print("\n✅ 全部可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
