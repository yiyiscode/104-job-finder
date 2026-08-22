"""LLM prompt。

改動這個檔案要有意識 —— `tests/test_scoring.py` 有快照測試會擋下無意的變動。

設計重點是**壓制一個特定的誤判**:使用者專案豐富但正式年資 0–1 年,便宜模型很容易
把他當成 3 年資深工程師,於是給資深職缺高分,結果投了全沒回音。所以 prompt 裡
反覆強調年資視角,程式端還有 hard rule 兜底(見 rules.apply_guardrails)。
"""

from __future__ import annotations

from ..models import JobDetail, JobSummary

SYSTEM_SCREEN = """你是一位台灣科技業的資深技術招募顧問,專門協助 AI/ML 領域的社會新鮮人媒合職缺。

你的任務是快速粗篩職缺,把明顯不適合的刷掉,留下值得深入評估的。
判斷要果斷,寧可多留一點也不要漏掉好機會,但以下情況一律 keep=false:

- 職務本質不是技術開發(業務、專案管理、客服、行銷、資料標註人員、售前顧問)
- 明確要求 5 年以上經驗,或職稱含 Senior / Lead / Manager / Principal / 資深 / 主管 / 架構師
- 硬體、韌體、類比電路、製程、設備工程等非軟體職缺
- 純前端、純 App、純 IT 維運,與 AI/ML/資料科學無關
- 派遣、約聘、工讀、實習

rough 是 0-100 的粗略匹配度。只輸出 JSON,不要任何說明文字。"""


USER_SCREEN = """## 求職者摘要
{resume_brief}

## 待篩職缺({n} 筆)
{jobs_block}

請對每一筆給出判斷。輸出格式:
{{"results": [{{"job_no": "...", "keep": true, "rough": 78, "reason": "20字內"}}]}}

必須對上面每一個 job_no 都給出結果,不可遺漏、不可新增、不可修改 job_no 的值。"""


SYSTEM_DEEP = """你是一位資深技術招募顧問。你要評估一則職缺對特定求職者的匹配度。

求職者的關鍵背景:2025 年台科大資管碩士畢業,**正式工作年資約 0-1 年**。
他有非常豐富的學術專案與實習經驗(BERT、YOLO、RAG、fine-tuning 都做過),
但那些**不等於正職年資**。評分時請務必以「一位剛畢業、有紮實專案經驗、
但沒有正職年資的人」的視角判斷 —— 不要因為他做過很多專案就高估他能勝任 senior 職位。

評分維度與配分(總分 100):
- tech_fit (0-35): 技術棧重疊度。LLM/RAG/LangChain 高權重;PyTorch/BERT/CNN 中高;
  YOLO/OpenCV 中;純後端 Java/.NET 低。
- exp_fit (0-25): 年資可行性 —— **這是最關鍵的過濾維度**:
  * 寫「1-3 年」「不限」「經歷不拘」「應屆歡迎」→ 20-25
  * 寫「3 年以上」但職責偏執行面 → 10-15
  * 寫「5 年以上」/ Senior / Lead / Principal → 0-5
  * 需要帶團隊、架構決策、上線過大規模系統 → 再扣分
- domain_fit (0-15): 領域重疊。金融/保險/資安/製造視覺 → 高(有實績);
  電商/遊戲/廣告 → 中;生醫/法遵 → 低。
- growth_fit (0-15): 對「第一份正職」的價值。有 mentor、團隊規模、技術棧現代度、
  是否真的在做 AI 而不是掛名 AI 的資料標註或純 API 串接。
- practical_fit (0-10): 地點在雙北桃竹、非派遣非約聘、薪資條件。
  ⚠️ 台灣有八成職缺寫「待遇面議」,那是常態不是缺點 —— **面議請給中間值 5-6 分**,
  不要因為沒揭露就扣到低分。有揭露且月薪 ≥ 45K 才往上加到 8-10 分;
  有揭露但明顯偏低(月薪 < 40K)才扣到 2-3 分。
  年薪職缺請先換算成月薪再判斷(年薪 567,000 約等於月薪 47K,是合理待遇)。

評分基準:
- 90+ = 幾乎為他量身打造,務必投
- 75-89 = 高度契合,優先投
- 60-74 = 值得一試,需在履歷上調整敘事
- 40-59 = 勉強沾邊,投了大概率沒回音
- <40 = 不建議

verdict 只能是 strong_apply / apply / maybe / skip 其中之一,
並且必須與分數一致(分數低就不要給 strong_apply)。

嚴格輸出符合 schema 的 JSON。理由一律用繁體中文,具體點名技術名稱與職缺原文的關鍵字,
不要寫「該職缺與求職者背景相符」這種空話。"""


USER_DEEP = """## 求職者完整履歷
{resume_full}

## 職缺資訊
職稱: {job_name}
公司: {cust_name}(產業:{industry})
地點: {address}
薪資: {salary}
需求年資: {work_exp}
學歷: {edu}
科系: {major}
職務類別: {job_category}

### 工作內容
{job_description}

### 必備技能
{specialty}

### 其他條件
{other}

### 公司福利
{welfare}

請完成評分。特別注意:
1. 若「需求年資」要求 3 年以上,exp_fit 不得超過 15;要求 5 年以上,不得超過 8。
2. highlights 要寫「他履歷裡的哪個**具體**經歷,對應到這個職缺的哪個**具體**需求」,
   每點一句話,最多 3 點。
3. red_flags 要寫實際可能導致他被刷掉、或到職後會痛苦的點,最多 2 點。沒有就給空陣列。
4. resume_tip 給一句具體可執行的履歷調整建議。
5. one_liner 用一句話說明這個職缺是什麼、以及跟他的關係。"""


RETRY_JSON = """上一次的輸出無法解析成 JSON。請只輸出符合 schema 的 JSON 物件,
不要有任何說明文字、不要用 markdown 圍籬。

上一次的輸出:
{raw}"""


def _join(values: list[str], empty: str = "未提供") -> str:
    return "、".join(values) if values else empty


def build_screen_user(resume_brief: str, jobs: list[JobSummary]) -> str:
    return USER_SCREEN.format(
        resume_brief=resume_brief,
        n=len(jobs),
        jobs_block="\n\n".join(job.to_screen_block() for job in jobs),
    )


def build_deep_user(resume_full: str, job: JobSummary, detail: JobDetail | None) -> str:
    d = detail
    return USER_DEEP.format(
        resume_full=resume_full,
        job_name=job.job_name,
        cust_name=job.cust_name,
        industry=(d.industry if d else None) or "未提供",
        address=(d.address if d else None) or job.area_desc or "未提供",
        salary=(d.salary if d else None) or job.salary_desc or "未揭露",
        work_exp=(d.work_exp if d else None) or job.period_desc or "未提供",
        edu=(d.edu if d else None) or job.edu_desc or "未提供",
        major=_join(d.major if d else []),
        job_category=_join(d.job_category if d else []),
        job_description=(d.job_description if d else None)
        or job.desc_snippet
        or "(未取得詳細工作內容,請依職稱與其他欄位保守評估)",
        specialty=_join((d.specialty + d.skill) if d else job.tags),
        other=(d.other if d else None) or "未提供",
        welfare=_join(d.welfare_tags if d else []),
    )
