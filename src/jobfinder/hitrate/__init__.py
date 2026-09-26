"""第 3 道閘門:必備條件命中率(docs/webui-spec.md §二)。

流程:jobs.db 快照 → 挑出「有全文且沒卡在第 1 道」且還沒用目前履歷算過的職缺 →
LLM 把 JD 拆成條件清單(必備/加分 × 符合/部分/不符 + 證據)→ **程式**算命中率 →
寫進獨立的 hitrate.db。

* 只打 OpenRouter,不打 104 —— 不 import scrape / http_source / pipeline
* 不寫 jobs.db —— pipeline 會整檔 os.replace 它(docs/adr/0001)
* 不信任模型的數字:模型只給逐條判定,百分比由 :func:`compute.hit_rate` 算
"""
