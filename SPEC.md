# SPEC — 104 每日職缺日報自動化

版本 2.0 · 建立於 2026-08-21 · **2026-08-22 大幅修訂**(發現 104 沒有 Cloudflare,抓取架構由瀏覽器改為純 HTTP)

---

## 1. 背景與目標

使用者(林芳義,2025 台科大資管碩士畢,約 0–1 年正式年資)每天手動翻 104 找職缺,費時且容易漏掉新上架的缺。

**目標**:每天 08:00(台北時間)自動抓取符合條件的職缺,用 LLM 對照其履歷(`profile-de.md`)評分,將「今天新出現、在目標產業、且分數排得上前面」者整理成日報推送到 Telegram。

**求職方向(2026-09-23 收斂)**:主敘事是**資料工程師／資料科學家**,產業收斂在**半導體與金融的大公司**。
刻意避開「AI 工程師」那一格 —— 競爭最激烈,改由 104 的公開履歷承接 inbound。
`profile.md`(AI 主敘事)保留給 inbound,本工具的評分基準是 `profile-de.md`。

**非目標**(明確不做):
- 不自動投遞履歷,不代替使用者與雇主互動
- 不對外提供服務、不公開發布抓取到的資料
- 不建立網頁前台(第一版純 Telegram)
- 不做跨平台(1111、CakeResume 等)彙整

---

## 2. 使用者輪廓(評分依據)

摘自 `profile-de.md`(主動投遞用履歷,主敘事是流程自動化／資料工程),LLM 評分時的關鍵背景:

| 項目 | 內容 |
|---|---|
| 學歷 | 台科大資管所 M.S.(2025 畢)、台北大學統計系 B.S. |
| 正式年資 | **約 0–1 年**(僅實習與產學經歷) |
| 實習 | 國泰人壽 CAP 資安大數據(BERT+CNN 惡意網址分類,pipeline 效率 6 倍) |
| 產學 | Solerie AI 金幣瑕疵檢測(YOLO / SIFT+FLANN / Arduino) |
| 代表專案 | 半導體晶圓缺陷分類(不平衡資料、SHAP、AutoML)、RAG 資安文件問答(FastAPI/LangChain/ChromaDB)、資安 LLM fine-tuning、i 郵箱 BI 平台(郵政大數據競賽 Top 15) |
| 技術棧(主線) | Python、SQL、爬蟲、排程、ETL 流程設計、API 串接、Pandas/NumPy、FastAPI、AWS/GCP |
| 技術棧(輔線) | LangChain/RAG/Fine-tuning、PyTorch/CNN/BERT、XGBoost/SHAP、OpenCV/YOLO、Power BI/Tableau/Streamlit |
| 目標產業的實績 | **金融**:國泰人壽 CAP 資安大數據實習;**半導體**:晶圓缺陷分類(FNR 100%→0%) |

⚠️ **評分時最關鍵的一點**:此人「專案豐富但正式年資 0–1 年」,是 LLM 極容易誤判的組合 —— 模型看到滿滿的 BERT/YOLO/RAG 專案,傾向把他當成 3 年資深工程師,於是給「要求 5 年經驗」的職缺高分,結果投了全無回音。**規格上必須用程式規則壓制這個誤判**(見 §6.4)。

---

## 3. 功能需求

| ID | 需求 | 驗收 |
|---|---|---|
| F-1 | 每天 08:00(Asia/Taipei)自動執行一次 | Modal Dashboard 顯示 cron 已註冊且下次執行時間正確 |
| F-2 | 依多組關鍵字搜尋,結果以 `jobNo` 合併去重 | 兩組關鍵字命中同一職缺時只存一筆,`matched_keywords` 記錄兩者 |
| F-3 | 只推送「今天新出現」的職缺 | 昨天推過的今天不再出現(見 §5 去重規則) |
| F-4 | 對每則職缺產生 AI 匹配評分與人話理由 | 卡片含分數、一句話總評、3 點匹配理由、注意事項、履歷建議 |
| F-5 | 依相對排序取分數最高的前 N 則推送(`scoring.mode: top_n`) | 低於 `top_n_floor` 者不推;理由見 §6.3 |
| F-11 | 只評估目標產業與規模的職缺 | 不符者在粗篩**之前**就濾掉,不花 LLM token、不佔詳細頁名額;筆數記入 `runs.jobs_filtered_out` |
| F-6 | 未達標職缺在摘要中一行列出(標題+分數+原因) | 摘要含「未達標(供參考)」區塊 |
| F-7 | 當日無新職缺時仍發一則摘要 | 收到「今日無新職缺」訊息,不是靜默 |
| F-8 | 執行失敗時發 Telegram 告警 | 告警含頁面標題、URL、熔斷狀態、下次可執行時間、截圖檔名 |
| F-9 | 保留原始 JSON 供重跑評分 | `cli.py --replay <RUN_ID>` 可不重爬重新評分 |
| F-10 | 使用者可隨時緊急停用抓取 | 設 `JOBFINDER_SCRAPE_DISABLED=1` 後 pipeline 短路,一個請求都不發 |

---

## 4. 資料來源

### 4.1 端點與存取方式

| 用途 | 端點 | Referer |
|---|---|---|
| 搜尋列表 | `GET /jobs/search/api/jobs` | `https://www.104.com.tw/jobs/search/` |
| 職缺詳細 | `GET /job/ajax/content/{detail_id}` | 該職缺自己的頁面 `/job/{detail_id}` |

**這兩個端點上沒有 Cloudflare。** 擋人的是 origin 層一條 `Referer` 白名單規則。
實測(2026-08-21):

| 請求 | 結果 |
|---|---|
| 不帶 `Referer` | `403` · **0 bytes** · body 不含任何 Cloudflare 標記 |
| 只加 `Referer` | **`200` · 117 KB 真實 JSON · 1.2 秒** |

連 User-Agent 都不用偽裝(`curl/8.x` 也照樣 200)。Cloudflare 在 104 確實存在
(`/sitemap.xml` 會回真正的 Turnstile 挑戰頁),但沒有掛在 `/jobs/search/api/` 上。

> **早期研究的誤判紀錄(留著免得有人繞回去):** 先前判定「必須用真實瀏覽器過
> Cloudflare」,是因為那份研究用 WebFetch 打 API —— 那個管道設不了 `Referer`,
> 拿到 403 就誤判成 Cloudflare 挑戰。整個 Patchright 架構因此建立在錯誤前提上。

所以預設 `scrape.mode: http`,用 `httpx` 直接請求。Patchright 那條路完整保留為備援
(`mode: browser`,程式碼與測試都在),只有在 104 真的把 Referer 檢查換成
bot detection 時才切過去 —— 告警會明確告訴你是哪一種。

⚠️ `/jobs/search/list`(2019–2022 年所有中文教學使用的端點)**已 404 失效**。

⚠️ 端點路徑做成 config 值(`search_api_pattern` / `detail_api_pattern`)。

#### ⚠️ `jobNo` 與 `detail_id` 是不同的 ID

實測:同一則職缺 `jobNo=15305872`,但 `link.job` 是 `/job/94234`。
**詳細頁 API 只吃連結尾段**,拿 `jobNo` 去打會回 `{"error":{"code":11201,"message":"職務不存在"}}`。

領域模型因此分成兩個欄位:`job_no`(穩定,當去重主鍵)與 `detail_id`(打 API 與組連結用)。

#### 已實測的欄位語意

| 欄位 | 語意 |
|---|---|
| `data` | **本身就是陣列**,沒有 `data.list`;總數在 `metadata.pagination.total / .lastPage` |
| `s10` | 薪資類型(等同詳細頁的 `salaryType`):`10`=面議 `50`=月薪 `60`=年薪。**不看它就會把年薪 567,000 寫成「月薪 56 萬」** |
| `period` | **實際要求年資的數字**(0 = 不拘),不是級距代碼。送 `jobexp=1,3` 回傳 0/2/3,送 `jobexp=10` 回傳 6~9 |
| `optionEdu` | int 陣列:`3`=專科 `4`=大學 `5`=碩士 `6`=博士;多碼取最低者加「以上」 |
| `tags` | dict,以參數名為 key。`desc` 為空時不可退回 `param`(那是 `wf1` 之類的內部代碼) |
| `description` | **不是完整 JD**(中位數 123 字),詳細頁仍須抓。且含換行,進批次 prompt 前要壓成單行 |
| `salaryDesc` | **不存在**。薪資字串要由 `salaryLow`/`salaryHigh`/`s10` 自行組出 |

詳細頁的 `condition.edu` 與 `condition.workExp` 已經是**文字**,不需查代碼表。

### 4.2 搜尋參數 —— 原始需求中的三個錯誤(已查證修正)

| 參數 | 原始需求假設 | **實際** |
|---|---|---|
| `jobexp=3` | 「3 年以下」 | ❌ 這是**級距值**:`1`=1年以下、`3`=1~3年、`5`=3~5年、`10`=5~10年、`99`=10年以上。原值會漏掉所有「1年以下/經歷不拘」的缺 → **改用 `jobexp=1,3`** |
| `order=15` | 「最新更新」 | ❌ 是**符合度排序**,且 104 改版後數值會漂移 → **不使用 `order`**,新鮮度改用 `isnew` + `appearDate` 自行去重 |
| `area=6001006000` | 「新竹市」 | ⚠️ 實際是**新竹縣市合併**,範圍較大(已確認接受) |

已驗證的地區代碼:`6001001000` 台北市、`6001002000` 新北市、`6001005000` 桃園市、`6001006000` 新竹縣市。

### 4.3 搜尋條件

- **關鍵字**(多組合併去重,10 組):資料工程師、數據工程師、ETL工程師、資料平台工程師、資料倉儲工程師、BI工程師、爬蟲工程師、數據分析師、資料科學家、機器學習工程師
  理由:職缺名稱在台灣沒有統一寫法,單一關鍵字會漏掉內容相符但名稱不同的職缺。
  ⚠️ **順序有意義** —— 預算用盡時 `search.py` 會停在當下的關鍵字,後面的整個不跑。
  ⚠️ 刻意不放「系統整合工程師」「流程自動化」:它們命中的主要是 SI／接案公司,與「聚焦大廠」反向拉扯。
- **地區**:台北市、新北市、桃園市、新竹縣市、台中市(硬條件是「台中以北」)
- **經歷**:`jobexp=1,3`(3 年以下)
- **新鮮度**:`isnew=3`(三日內),再由 DB 去重確保只推真正沒看過的

---

## 5. 去重規則

以 `jobNo` 為主鍵。判定「是否為今天要推的新職缺」:

| 情境 | 判定 |
|---|---|
| 全新 `job_no` | ✅ 新 |
| 看過,`appear_date` 未變 | ❌ 不新 |
| 看過,`appear_date` 變了,但距上次 < `repost_cooldown_days`(預設 30 天) | ❌ 不新(避免雇主頻繁刷新造成重複推播) |
| 看過,`appear_date` 變了,距上次 ≥ 冷卻期 | ✅ 新(視為重新開缺) |
| 曾被粗篩刷掉(`status='screened_out'`) | ❌ 不新 |
| 兩組關鍵字命中同一 `job_no` | 只存一筆,`matched_keywords` 記錄兩者 |

---

## 5.5 規則層過濾(產業與公司規模)

去重之後、進 LLM **之前**的一道純規則過濾。實作在 `src/jobfinder/targeting.py`。

| 條件 | 依據欄位 | 設定 |
|---|---|---|
| 產業 | `coIndustry`(階層式代碼,比對**前綴**) | `1001006` 半導體業 / `1001005` 電子零組件 / `1001004` 光電及光學 / `1001003` 電腦及消費性電子製造 / `1004` 金融投顧及保險 |
| 公司規模 | `employeeCount` | `min_employee_count: 500` |

**兩個欄位在搜尋列表回應裡就有**,不必抓詳細頁(1,638 筆真實資料驗證,缺失率 0)。

### 為什麼不在 104 的搜尋參數做

搜尋請求數 = 關鍵字 × 頁數,**跟有沒有帶產業參數完全無關** —— 在對方那端過濾
省不到任何請求預算(見 §9 規則 3),卻要為一個沒實測過的參數多打一次 104。
程式端過濾是零額外請求、零封鎖風險、可完全離線測試。

真正稀缺的是每次 18 個詳細頁名額,而這一層正好把它們全部留給目標職缺。

### 為什麼不用 `jobCat`(職務類別)

104 有正規的職務類別代碼(`2007001022` 資料工程師 vs `2007001020` AI工程師),
看起來更精準。**實測失敗**:雇主亂掛 —— 「數據工程師(學士/碩士)」掛的是
統計精算人員/軟體工程師,整批最好的玉山「Data & AI Platform」掛的是
其他資訊專業人員/雲端工程師,反而「AI研發工程師」**有**資料工程師。
硬篩會殺掉最好的缺、留下 AI 缺。**公司屬於什麼產業,雇主沒有動機亂填;
職務類別有。**

### ⚠️ 訊號消失時 fail-open,不是 fail-closed

104 哪天不給 `employeeCount` 了,fail-closed 會讓日報靜靜變成 0 則 ——
**跟「今天沒新職缺」長得一模一樣**,可能好幾週才發現。

所以某欄位在整批裡缺超過 50%(`MISSING_RATIO_LIMIT`)時,**停用該條件並大聲告警**。

**驗收**:`tests/test_targeting.py`(18 個測試)+ `tests/test_pipeline.py` 的 4 個整合測試。
被濾掉的筆數記入 `runs.jobs_filtered_out`;**這個數字等於 `jobs_new` 代表條件太窄或 104 改了欄位**。

---

## 6. AI 評分規格

### 6.1 兩階段

| | Stage 1 粗篩 | Stage 2 深評 |
|---|---|---|
| 輸入 | 搜尋列表欄位 | 完整 detail(JD、技能、條件、福利) |
| 批次 | **15 筆/次** | 逐筆 |
| 模型 | 最便宜級 | 便宜但稍強 |
| 輸出 | `{job_no, keep, rough, reason}` | 完整多維度評分 |

**粗篩必須批次**:履歷 context 約 800 token 是固定成本,批次攤提後每筆邊際成本約 60 token;逐筆呼叫等於把履歷付 15 次。
批次後**必須驗證**「回傳的 `job_no` 集合 == 送出的集合」,缺漏自動補一輪(便宜模型會漏)。批次上限 15,再多會出現漏回與前段錨定。

### 6.2 評分維度(總分 100)

```
tech_fit       0-35   技術棧重疊度
exp_fit        0-25   年資可行性 ← 對 0-1 年年資者,這是最關鍵的過濾維度
domain_fit     0-15   領域重疊(金融/保險/資安、半導體/製造 有實績 → 高)
growth_fit     0-15   對「第一份正職」的成長價值(是否真在做資料工程,而非掛「資料」之名的報表維護)
practical_fit  0-10   薪資揭露、地點、非派遣約聘
```

`exp_fit` 刻意佔 25 分:對此使用者而言,「技術再合但要求 5 年」的職缺投了也是浪費,分數必須被壓下去。

**`tech_fit` 以資料工程為主軸**(SQL/ETL/排程/爬蟲高權重,LLM/RAG 屬輔線)。
JD 要求 Spark / Airflow / dbt / Kafka 這類**他沒有實作過**的工具時扣分但**不歸零** ——
他自建過每日在線運行的排程與 ETL 管線,概念等價、只是工具不同,屬於「上手成本」
而不是「完全不會」。prompt 的這幾句有錨點測試(`tests/test_scoring.py`)擋回頭改。

### 6.3 選取規則 —— 相對排序,不是絕對門檻

**`scoring.mode: top_n`,取分數最高的前 5 則,下限 `top_n_floor: 60`。**

原本用 `threshold: 80`。改掉的理由是實測:拿同一次執行的原始資料連跑兩次 `--replay`
(同一份資料、同一個 prompt、`temperature: 0.2`),分數是 80/88、81/83、78/79、64/64 ——
**雜訊約 ±8 分,而門檻剛好落在 80。** 那等於讓整批最好的職缺隨機出現或消失,
而且從外面看就是「今天沒有好缺」,**不會有任何跡象**。

相對排序讓雜訊只影響排序,不會讓整天變空。`top_n_floor` 刻意訂在遠低於雜訊帶的位置,
只砍垃圾、不參與邊緣判斷。

> **拿 LLM 分數做硬切點之前先量雜訊** —— 同一份資料重跑兩次比對即可。
> 門檻若落在雜訊帶內,它就沒有鑑別力。

`scoring.threshold` 仍保留,因為卡片的分數燈號還在用它。
未達標者在摘要一行列出(上限 10 則)。

### 6.4 防幻覺機制(全部在程式端,不信任 LLM)

1. **`total` 程式端重算** = 五維相加。與模型回傳值差 > 5 記 warning,採用重算值。
2. **`verdict` 交叉驗證**:模型另輸出 `strong_apply|apply|maybe|skip`。若 `total ≥ scoring.threshold` 但 `verdict` 為 `skip`/`maybe`,以 verdict 為準降級(模型算術不可靠,定性判斷相對穩)。
3. **年資 hard rule**:優先讀搜尋列表的 `period` **數字**(已實測確認是實際年數),無此欄位才退回解析 `condition.workExp` 的中文。若要求 ≥ 5 年而 `exp_fit > 8`,**程式強制夾到 8** 並重算 total。能用規則做的事就用規則做,更別說 104 直接給了整數。

### 6.5 Structured output 三層保險

1. `response_format: json_schema` + `provider: {require_parameters: true}`(只路由到真的支援的 provider)
2. 容錯解析:剝 ` ```json ` 圍籬 → 抓首尾大括號
3. 全失敗時把原始輸出丟回模型要求重輸出

⚠️ Pydantic `model_json_schema()` 對 strict mode 常不相容,需 `to_strict_schema()` 後處理:inline `$defs`、所有欄位進 `required`、每層加 `additionalProperties: false`。需有單元測試 assert 這三點。

### 6.6 成本

估算每天約 60 筆新職缺 → **約 $0.02/天、$0.6/月**。`daily_cost_cap_usd` 預設 0.50,超過中止 LLM 階段並告警。

---

## 7. 通知規格

**Telegram，`parse_mode=HTML`**(不用 MarkdownV2:後者要跳脫 18 個字元,職缺標題與薪資滿是 `(` `)` `-` `.`,漏一個整則 400)。

- 先發一則**摘要**:今日新職缺數 → 粗篩留存 → 深評 → 達標數;達標清單一行一則;未達標區塊;耗時與 LLM 成本
- 再逐則發**職缺卡片**:分數燈號 + 職稱 + 公司/地點/薪資/學歷/年資 → 一句話總評 → 為什麼適合你(3 點,須點名履歷中的具體經歷對應 JD 的具體需求)→ 注意事項 → 履歷調整建議 → 細項分數
- inline keyboard:「看原始職缺」「公司其他職缺」
- 節流 1.2 秒/則(單一聊天室建議 < 1 msg/sec),處理 429 的 `parameters.retry_after`
- 單則上限 4096 字元,實作用 3800 留餘裕,超長在空行切,不切開 HTML tag
- **400 時降級為純文字重送一次** —— 標題偶爾有怪字元,寧可醜也不要整則消失

---

## 8. 資料模型

SQLite,四張表:

| 表 | 用途 | 關鍵欄位 |
|---|---|---|
| `jobs` | 職缺主檔與去重 | `job_no` (PK)、`first_seen_at`、`last_seen_at`、`appear_date`、`content_hash`、`matched_keywords`、`raw_summary`、`raw_detail`、`status` |
| `scores` | 每次評分結果 | `job_no`、`run_id`、`stage`、`model`、五維分數、`total_score`、`verdict`、`one_liner`、`highlights`、`red_flags`、`raw_response` |
| `runs` | 每次執行的稽核 | `started_at`、`status`、`jobs_fetched/new/scored/notified`、**`jobs_filtered_out`**(規則層濾掉的筆數)、**`requests_used`**、`llm_cost_usd`、`error_kind`、`error_detail` |
| `circuit_state` | 熔斷器 | `state`、`tripped_at`、`reason`、`consecutive_failures` |

新增欄位一律走 `storage/db.py` 的 `MIGRATIONS`:`CREATE TABLE IF NOT EXISTS`
對既有的表完全沒作用,少了遷移舊 db 會在 UPDATE 時炸 `no such column`。

**刻意保留 `raw_summary` / `raw_detail` 原始 JSON**:讓 `--replay` 能拿真實資料反覆重跑評分調 prompt 而不重爬(這是防封鎖的一環);104 改版時也能直接 diff 新舊結構。

---

## 9. 硬性防封鎖限制

> **保護順位:① 104 求職帳號 > ② 家中住宅 IP > ③ Modal 雲端 IP > ④ 功能完整度。**
> 需求衝突時一律按此順序犧牲。寧可少抓職缺,絕不冒險。

- **① 帳號**最不可失。停權 = 失去真實求職身分,無可替代。
- **② 家用 IP** 次之。`probe_api.py`、`bootstrap_profile.py`、本機 `--headful` 測試都從使用者家中網路打出;家用 IP 被鎖會影響日後正常用瀏覽器求職,且無法自行解除。**本機腳本的限制要比 Modal 上更嚴。**
- **③ Modal IP** 被鎖換個容器即可,是唯一可承受的損失。

以下規則**寫死在程式裡**。config 只能往更保守調,超過硬上限者 `config.py` 啟動時直接拒絕。

### 規則 1 — 偵測到封鎖訊號立刻全面停止,零重試

**重試是把「暫時被挑戰」變成「永久封 IP」的頭號原因。**

封鎖訊號:HTTP 429/503、頁面標題 `Just a moment...`、內文含 `Enable JavaScript and cookies`、Cloudflare 1020/1015、Turnstile 勾選框出現。

**403 要先分辨是哪一種**(這兩者處置完全相反):

| 情況 | 判定 | 動作 |
|---|---|---|
| `403` + body **空** | 我方 `Referer` 掉了或 origin 規則變了 | `SourceMisconfigured` —— 告警請人檢查設定,**不觸發熔斷**(冷卻 24 小時對設定錯誤毫無幫助) |
| `403` + body 有內容 | 104 真的上防護了 | `ChallengeBlocked` → 熔斷 → 建議切 `scrape.mode: browser` |

⚠️ 瀏覽器模式讀 body 可能失敗而得到空字串,那時必須 **fail-closed**;
所以 `detect_block()` 對 403 一律視為封鎖,分流只在 HTTP source 明確呼叫 `classify_403()` 時做。

任一出現 → 中止本次執行的**所有**後續請求(不是只跳過這一頁)→ `ChallengeBlocked` 拋到頂 → 觸發熔斷 → 告警。
僅**一般網路錯誤**(逾時、連線中斷)才允許重試,最多 1 次、退避 15 秒。

### 規則 2 — 熔斷器

`circuit_state` 表。任何一次執行以封鎖訊號結束即 tripped。熔斷期間 pipeline 開頭短路,**連瀏覽器都不開**,只發 Telegram 提醒。

冷卻期:第 1 次 **24 小時**(config 可調但下限 24h)→ 第 2 次連續 **72 小時** → 第 3 次**無限期**,須手動 `cli.py reset-circuit`。

理由:104 已在擋你時,隔天 08:00 再自動跑只會坐實「這個 IP 是機器人」。

### 規則 3 — 全域請求預算

| 限制 | 硬上限 |
|---|---|
| 每日執行次數 | **1**(DB 記錄當日已跑過即拒絕) |
| 每次總 HTTP 導覽數 | **40**(搜尋頁+詳細頁合計) |
| 每關鍵字頁數 | **3** |
| 每次詳細頁數 | **25** |
| 抓取並行度 | **1**(禁止 `asyncio.gather`) |
| Modal 容器並行 | **1**(`max_containers=1`) |

`scrape/budget.py` 的 `RequestBudget`:**每個 `page.goto()` 都必須先過它**。耗盡時丟 `BudgetExhausted`,pipeline 正常收尾(不算失敗、不觸發熔斷)。

### 規則 4 — 節流不可繞過

分頁間 3–5 秒、關鍵字間 5–9 秒、詳細頁間 4–8 秒,**全部帶 jitter**(固定間隔本身就是機器人指紋)。下限 3 秒寫死,config 設更低拒絕啟動。延遲由 `RequestBudget` 在 `goto()` 前強制執行,不靠呼叫端自己記得 sleep。

### 規則 5 — 關閉 Modal 自動重試

`retries=0`。Modal 的 function 級重試會在被擋後 5 分鐘整個再跑一次,正是規則 1 禁止的行為。

### 規則 6 — 開發期絕不反覆打 104

`probe_api.py` 一輩子只該跑 1–2 次,錄成 fixture 後開發**完全用 fixture**;偵測到已有 fixture 即拒絕執行(須 `--force`)。開發一律 `--from-fixtures`;真要連線必須 `--limit`,`--headful --limit N` 的 N 硬上限 **5**。

實作順序把 `scrape/` 排在倒數第二,就是為了讓抓取層完成時前面已全部用 fixture 驗證過,不需靠反覆重跑 debug。

### 規則 7 — 誠實而節制,而不是偽裝

`user_agent` 的正確做法在兩種模式下**剛好相反**,`config.py` 依 mode 強制驗證:

- **`http`**:必填,而且要**誠實標示用途與聯絡方式**
  (`JobFinder/0.1 (personal job-seeking daily digest; +email)`)。
  104 的 API 根本不檢查 UA(實測 `curl/8.x` 也照樣 200),偽裝成瀏覽器沒有任何好處,
  只會在對方看 log 時顯得心虛;誠實標示反而降低合規風險,也讓 104 想聯絡時有管道。
  設定含 `Mozilla` 會被拒絕啟動。
- **`browser`**:必須 `null` —— 偽造的 UA 與 Chromium 實際指紋不一致,反而最像機器人。
  另需 `locale=zh-TW`、`timezone_id=Asia/Taipei`、視窗 1440x900、`fonts-noto-cjk`、warmup 擬人動作。

兩種模式共通:只在 08:00 這種正常人會上網的時段跑,每日一次。

### 規則 8 — 告警必須送得出去

告警走 Telegram**不經過 104**,被擋時一定送得到。內容含 `page.title()`、URL、熔斷狀態、下次可執行時間、`/data/debug/` 截圖檔名。

### 規則 9 — 🚫 絕不登入 104 帳號,全程匿名訪客

**比 IP 保護更重要。** IP 被鎖可換網路;帳號停權則失去真實求職身分。登入 = 把自動化行為簽上自己的名字。

前提事實:**目標資料本來就不需要登入**,搜尋列表與職缺詳細對未登入訪客完全開放。登入拿不到任何本專案需要的額外欄位 —— 只有壞處沒有好處。

強制措施:
- **憑證從不存在於系統中**:`config.yaml`、`.env.example`、Modal Secret 都不得有 104 帳密欄位。沒有欄位可填就不可能誤登入。
- **專屬乾淨 profile**:`user_data_dir` 固定為 `./local_data/chrome-profile`(本機)或 `/tmp/chrome-profile`(Modal)。**嚴禁指向系統 Chrome 的 User Data 目錄** —— 那裡有使用者真實的 104 登入 cookie。
- **`assert_anonymous(context)` hard gate**:warmup 後、進搜尋頁前執行。掃 cookie jar,發現 104 身分標記(`104_session`、`ARJ`,或含 `token`/`member`/`login`/`uid` 的 `.104.com.tw` cookie)→ 立刻中止並告警。
- **profile 打包時過濾身分 cookie**:只保留 `cf_clearance` / `__cf_bm` 與語系偏好。連存都不存,就不會在下次執行被帶回來。`storage_state.json` 同樣過濾。
- **`bootstrap_profile.py` 明確警告**使用者不要登入,結束前自檢,偵測到登入態即刪除 profile 要求重做。

### 規則 10 — 只讀不互動 + 緊急開關

- 全程只做 GET 導覽。禁止表單送出、按鈕點擊(warmup 的滾動/滑鼠移動除外)、POST。
- **絕不碰投遞相關路徑**:`/job/*apply=form*`(robots.txt 亦明文禁止)、應徵、收藏、追蹤公司。這些是「已登入使用者的動作」,即使未登入被觸發也是強烈異常訊號。`urls.py` 維護黑名單,`budget.py` 在 `goto()` 前比對。
- **緊急開關**:`config.yaml` 的 `scrape.enabled` 或環境變數 `JOBFINDER_SCRAPE_DISABLED`,任一停用時 pipeline 短路只發通知。使用者可不改程式、不等 deploy 就立刻停掉排程。

---

## 10. 合規聲明

104 的 `robots.txt` 明文包含 `Disallow: /jobs/search/?*page=*`(正好命中本專案的目標 URL 模式),且對 AI/爬蟲 User-Agent 設有專門的 `Disallow: /` 區塊並要求填表申請許可。104 使用者條款亦禁止自動化擷取。

**本專案的定位與已知風險已向使用者說明並取得確認**:

- 純**個人求職自用**,不對外提供服務、不公開發布或轉散布抓取到的資料
- **每日僅執行一次**,並以 §9 的硬性限制在程式層強制節制請求量
- 全程**匿名訪客**身分,不登入、不投遞、不互動
- 抓取的是**公開可見**的職缺資訊,用途是協助使用者本人閱讀他原本就會手動瀏覽的內容

此定位若改變(例如對外提供服務、提高頻率、商業使用),**須重新評估合規性**,並考慮改用官方授權管道或第三方合規 API。

---

## 11. 驗收標準

**離線(不碰 104,應佔驗證的 99%)**
- [ ] `pytest` 全綠,含 `tests/test_guardrails.py`
- [ ] `cli.py run --from-fixtures --dry-run` 走完整流程並輸出可預覽的 HTML
- [ ] 護欄測試涵蓋:預算耗盡丟 `BudgetExhausted`、延遲 < 3 秒的 config 拒絕啟動、封鎖訊號不觸發任何重試、熔斷後 pipeline 短路且不開瀏覽器、含 `104_session` 的 context 被 `assert_anonymous()` 擋下、黑名單路徑丟例外、`persist_profile()` 不寫入身分 cookie
- [ ] 去重六情境測試通過(§5)
- [ ] `to_strict_schema()` 產出無 `$defs`、每層有 `additionalProperties: false`、欄位全在 `required`

**連線(一次性)** — 以下皆已完成
- [x] `probe_api.py` 錄到真實 JSON,欄位與 `normalize.py` 對得上(2026-08-21)
- [x] 端到端通,Telegram 收得到
- [x] `modal run modal_app.py::diagnose` ← **決定成敗的一刻**:**Modal 的資料中心 IP 過不了 Cloudflare**
      (`cf-mitigated: challenge`),真實瀏覽器更直接跳 Turnstile 互動挑戰。
      **因此改為 Windows 工作排程跑在本機**,`modal deploy` 不要執行。

**觀察期**
- [ ] 前 3 天檢查 `local_data/daily.log` 與日報內容
- [ ] 每次執行後查 `runs` 表的 **`jobs_filtered_out` vs `jobs_new`** ——
      **兩者相等代表規則層條件太窄或 104 改了欄位,日報會靜默歸零**
- [ ] 一週後查 `jobs_new` / `jobs_notified` / `requests_used` 趨勢,校準 `top_n` 與 `top_n_floor`

---

## 12. 已知風險

> 🟢 原本被列為「專案唯一存亡點」的 **「Modal 資料中心 IP 過不了 Cloudflare」已消除** ——
> 那個風險建立在「API 有 Cloudflare」的錯誤前提上(見 §4.1)。純 HTTP 模式下,
> 雲端 IP 與住宅 IP 沒有差別。

| 風險 | 影響 | 偵測 | 備案 |
|---|---|---|---|
| 🟠 **104 把 `Referer` 檢查換成真正的 bot detection** | 現在最大的技術風險,取代了原本的 Cloudflare 風險 | `classify_403()` 分辨得出來:空 body = 我方設定問題,有挑戰頁特徵 = 真的被擋 | `scrape.mode: browser` **一行切回瀏覽器方案** —— 那條路的程式碼與測試完整保留。再不行才是住宅代理($5–15/月,填 `scrape.proxy`) |
| 🟠 **API 結構再次改版** | 抓不到資料。內部 API 無版本承諾,`/jobs/search/list` 已經死過一次,而且這次改版還讓欄位型別全變(`data` 從物件變陣列、`salaryDesc` 消失) | `normalize.py` 記 schema drift;真實 fixture 的迴歸測試會第一個叫 | 端點路徑是 config 值;保留 raw JSON 可直接 diff 新舊結構;`--replay` 可離線重跑驗證修正 |
| 🟡 **便宜模型評分校準不穩** | 門檻對不上,推 0 則或推爆。同一職缺跑兩次可能差 15 分;且「專案豐富但年資 0–1 年」易被誤判為資深 | 程式端重算 total、verdict 交叉驗證、年資 hard rule(現在直接讀 `period` 整數,比解析中文可靠)、摘要永遠顯示「深評 N → 達標 M」,連 3 天 M=0 或 M≥10 表示未校準 | ① 每週抽 3 則跑兩次比對分差 ② `scoring.mode: threshold\|top_n` 一行切換成相對排序 ③ **Few-shot 校準**:從第一週實跑結果挑 90/70/40 分各一則人工標註寫進 prompt |
| 🟡 **薪資類型誤判** | 把年薪 567,000 顯示成「月薪 56 萬」,或讓所有年薪職缺在「薪資達標」判斷上不當過關 | `salary_type`(`s10`)有專屬測試,含真實資料的迴歸案例 | `monthly_equivalent()` 統一換算成月薪基準再比較 |
