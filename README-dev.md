# 104 每日職缺日報

每天早上 08:00(台北時間)自動到 104 抓取職缺,用 LLM 對照
[profile-de.md](profile-de.md) 評分,把「今天新出現、在目標產業、而且分數排得上前面」的
整理成日報推送到 Telegram。

**目標是資料工程師／資料科學家的職缺,而且收斂在半導體與金融的大公司。**
不是 AI 工程師 —— 那格競爭最激烈,改由 104 的公開履歷承接 inbound。

- 需求規格與驗收標準:[SPEC.md](SPEC.md)
- 給 AI 助理的專案上下文:[CLAUDE.md](CLAUDE.md)
- 目前進度與下一步:[RESUME.md](RESUME.md)
- 系統導覽與自我測驗:[docs/system-guide.html](docs/system-guide.html)

```
Windows 工作排程 08:00
  └─ 三道護欄:緊急開關 → 熔斷器 → 每日一次   ← 全都在發任何請求之前
  └─ httpx 直接打 104 的 JSON API(帶 Referer,全程序列)
  └─ normalize → SQLite 去重(只留今天新出現的)
  └─ 規則層:產業(半導體/金融的代碼前綴)+ 公司規模(≥500 人)   ← 在 LLM 之前
  └─ 粗篩(批次 15 筆)→ 深評(逐筆)→ 程式端校正
  └─ 相對排序取前 N 名 → Telegram 日報(預設只發一則,`notify.style`)
```

## 實際跑出來的數字

不是設計目標,是 `local_data/jobs.db` 現在的內容(2026-08-21 → 2026-09-23):

| 項目 | 數字 |
|---|---|
| 真實執行 | **23 次**,橫跨 34 天 |
| 抓到的職缺 | `jobs` 表 **1,638** 筆 |
| 深評紀錄 | `scores` 表 **636** 筆(含離線重跑) |
| 累計推播 | **158** 則 |
| 單次 LLM 成本 | 平均 **$0.046**,最高 $0.18 |
| 單次請求數 | 上限 40,實測最高 **40**(護欄真的有咬住) |
| 程式規模 | 36 模組 5,289 行 + **2,993 行測試** |
| 測試 | **286 個,全離線**,跑真實錄下來的 fixture |

其中 **112 個是護欄專屬測試**(`test_guardrails.py`、`test_http_source.py`、
`test_targeting.py`)。護欄沒有測試等於沒有護欄。

---

## ⚠️ 動手前先讀這段

保護順位:**① 104 求職帳號 > ② 家中住宅 IP > ③ 功能完整度**。

- **絕不登入 104 帳號。** 目標資料對未登入訪客完全開放,登入零好處。帳號被停權 =
  失去真實求職身分,無可替代。程式裡連填帳密的欄位都沒有,設定檔出現 `password`
  之類的鍵會直接拒絕啟動。
- **被擋就停,絕不重試。** 429/503/挑戰頁出現時立刻中止整趟,並啟動熔斷器
  (24h → 72h → 需手動解除)。
- **開發時用 `--from-fixtures`,不要反覆打 104。**

完整的八條硬性限制在 [CLAUDE.md](CLAUDE.md),理由在 [SPEC.md §9](SPEC.md)。

### Cloudflare 是「看 IP 信譽」的(這點來回搞錯過兩次)

**兩件事同時為真,不是二選一:**

| 來源 | 帶 `Referer` | 結果 |
|---|---|---|
| 你家的台灣住宅 IP | ❌ | `403` · **0 bytes** · 無 Cloudflare 標記 → origin 層規則 |
| 你家的台灣住宅 IP | ✅ | **`200`** · 連續 19 個請求全過 |
| AWS 資料中心 IP | ✅ | **`403` · `cf-mitigated: challenge`** |

台灣住宅 IP 被放行到 origin(那裡才輪到 `Referer` 白名單把關);資料中心 IP
在碰到 origin 之前就被 Cloudflare 攔下。**這就是為什麼這個系統跑在你的電腦上而不是雲端。**

所以走純 HTTP,不開瀏覽器 —— 從你家 IP 出去根本沒有挑戰要過。

---

## 安裝

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/Scripts/activate          # Windows;macOS/Linux 用 source .venv/bin/activate
```

**不需要安裝瀏覽器。** 只有切到 `scrape.mode: browser` 備援時才需要
`patchright install chromium`。

驗證安裝:

```bash
pytest                                                # 286 個測試,全離線
jobfinder run --from-fixtures --fake-llm --dry-run    # 用真實 104 資料跑完整流程
```

---

## 啟用步驟

`tests/fixtures/*.json` 已經是 **2026-08-21 錄下的真實 104 回應**,欄位對應都驗證過了,
所以剩下的只有接三個外部服務。**前兩步零風險、可無限重跑。**

### 1. Telegram(先做這個)

告警是走 Telegram 送的,所以這條路要先通 —— 不然 104 那邊出事時你只會發現
「今天沒收到日報」,卻分不出是被擋了、Modal 掛了、還是單純沒新職缺。

1. Telegram 搜尋 `@BotFather` → `/newbot` → 取得 **TELEGRAM_BOT_TOKEN**
2. **主動對你的新 bot 送一則訊息**(這步不能省,否則 bot 無法主動發訊給你)
3. 瀏覽器開 `https://api.telegram.org/bot<TOKEN>/getUpdates`,
   從 `result[0].message.chat.id` 取得 **TELEGRAM_CHAT_ID**
4. `cp .env.example .env` 並填入
5. 驗證:

```bash
python scripts/send_test_message.py
```

手機應收到兩則訊息。卡片標題刻意含 `<` `>` `&` —— 那些字元正常顯示、按鈕可點,
就代表 HTML 跳脫是對的。

### 2. OpenRouter

1. [openrouter.ai](https://openrouter.ai) 註冊 → Keys 建立 key → 儲值($5 夠跑一年,
   實際約 $0.6/月)
2. 填進 `.env` 的 `OPENROUTER_API_KEY`
3. **驗證 model id**(不要跳過):

```bash
python scripts/check_models.py
python scripts/check_models.py --search flash   # 找便宜且支援 structured output 的
```

目前 `config.yaml` 裡的兩個 id(`google/gemini-2.5-flash-lite` / `google/gemini-2.5-flash`)
**已經在真實執行中驗證過**。但模型汰換很快 —— 寫死一個已下架的 id 不會在啟動時報錯,
而是變成每天早上一則失敗告警,所以換 model 時務必重跑這個腳本。

### 3. 本機驗證

```bash
jobfinder run --from-fixtures --dry-run   # 用真 LLM 跑真實資料,看評分品質與排版
```

會在 `local_data/preview.html` 產生可用瀏覽器開的預覽。這一步調
`targeting`、`search.keywords` 與 `scoring`。

**選取用的是相對排序不是絕對門檻**(`scoring.mode: top_n`)。原因見下面
「[為什麼不用絕對門檻](#為什麼不用絕對門檻)」—— 深評分數的雜訊實測約 ±8 分,
任何卡在雜訊帶裡的門檻都只是在擲骰子。

滿意之後,真的連一次 104:

```bash
jobfinder run --limit 5    # 約 6 秒
```

### 4. 排程(跑在本機,不在雲端)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
```

註冊成 Windows 工作排程,每天 08:00 執行。不需要管理員權限。

**電腦沒開機的話會補跑** —— `-StartWhenAvailable` 讓它在開機後盡快執行一次,
而程式的「每日一次」護欄確保補跑不會重複推播。

```powershell
Start-ScheduledTask -TaskName "JobFinder Daily"     # 手動觸發一次
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8          # 看執行紀錄
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall   # 移除
```

#### 為什麼不用 Modal

原本的計畫是 Modal cron,但**雲端兩種模式都實測過不了**:

| 嘗試 | 結果 |
|---|---|
| Modal + 純 HTTP | `403` · `cf-mitigated: challenge` |
| Modal + Patchright 真實瀏覽器 | **更糟** —— 首頁直接跳 Turnstile **互動**挑戰 |
| 你家的台灣住宅 IP | ✅ 連續 19 個請求全 `200` |

用真實瀏覽器反而被升級成互動挑戰,代表 AWS 資料中心 IP 本身就被 104 的 Cloudflare
歸類為高風險訪客,不是換做法能繞過的。

`modal_app.py` 完整保留。哪天你接了台灣住宅代理(填 `config.yaml` 的 `scrape.proxy`),
就能切回雲端全自動。**在那之前不要跑 `modal deploy`** —— cron 會每天失敗,
把熔斷器燒到需要人工解除。

診斷指令(想確認雲端現在被擋成什麼樣):

```bash
modal run modal_app.py::diagnose    # 發一個請求,印出 egress IP、cf-mitigated、body
```

---

## 怎麼把「AI 工程師」擋在日報外面

這是整個專案最花時間查的一段,結論跟直覺相反。

**症狀**:關鍵字明明已經全換成資料工程(`資料工程師`、`資料倉儲工程師`、`BI工程師`…),
推播出來的還是一堆 AI 職缺。

**根因**:**104 的關鍵字搜尋是全文模糊比對,不是職稱比對。** 搜「資料工程師」會撈回
JD 裡提到「資料工程」的 AI 職缺 —— 實測那天推的「AI研發工程師」是被 `資料工程師`
撈進來的,「AI 工程師(AI Gateway 開發與整合)」是被 `資料科學家` 撈進來的。
**換關鍵字改變不了會撈到什麼。** 104 的職稱搜尋參數是 `kwop`,而它在 robots.txt
的禁用清單裡,所以這條路不走。

**解法**:在抓回來之後、進 LLM 之前加一層規則過濾,用**產業代碼 + 公司規模**。
那天推的 10 則裡有 8 則因此被擋掉,而它們全是軟體/服務業小公司的 AI 職缺。

```yaml
targeting:
  industry_prefixes:            # 104 的產業代碼是階層式的,填前綴就涵蓋整群
    - "1001006"   # 半導體業:半導體製造 / IC設計 / 其他半導體
    - "1001005"   # 電子零組件:PCB / 被動元件 / 其他電子零組件
    - "1001004"   # 光電及光學
    - "1001003"   # 電腦及消費性電子製造
    - "1004"      # 金融投顧及保險:銀行 / 金控 / 證券期貨 / 壽險 / 產險
  min_employee_count: 500
```

三個刻意的設計:

1. **不在 104 的搜尋參數做。** 搜尋請求數 = 關鍵字 × 頁數,跟有沒有帶產業參數
   **完全無關** —— 在對方那端過濾省不到任何請求預算,卻要為一個沒實測過的參數
   多打一次 104。程式端過濾是零額外請求、零封鎖風險、可以完全離線測試。
2. **跑在粗篩之前。** 不符合的職缺不該花 LLM token,更不該吃掉每次 18 個詳細頁名額。
   判斷依據 `coIndustry` / `employeeCount` **搜尋列表裡就有**,不必抓詳細頁
   (1,638 筆真實資料驗證,缺失率 0)。
3. **訊號消失時 fail-open 不是 fail-closed。** 104 哪天不給 `employeeCount` 了,
   fail-closed 會讓日報靜靜變成 0 則 —— **跟「今天沒新職缺」長得一模一樣**,
   可能好幾週才發現。所以某欄位在整批裡缺超過一半時,停用該條件並大聲告警。

### 一個試過但行不通的方案

104 其實有正規的職務類別代碼(`2007001022` 資料工程師 vs `2007001020` AI工程師),
而且搜尋列表裡就有、零額外請求,看起來是更精準的解法。但雇主亂掛:

| 職缺 | 它實際掛的 `jobCat` |
|---|---|
| 「數據工程師(學士/碩士)」 | 統計精算人員 / 軟體工程師 |
| 玉山「Data & AI Platform」(整批最好的缺) | 其他資訊專業人員 / 雲端工程師 |
| 「AI研發工程師」 | **有**資料工程師 |

硬篩 `jobCat` 會殺掉最好的缺、留下 AI 缺,剛好做反。所以用產業代碼而不是職務類別 ——
**公司屬於什麼產業,雇主沒有動機亂填。**

---

## 日常維運

```bash
jobfinder status                 # 最近 14 次執行 + 熔斷器狀態 + requests_used 稽核
jobfinder reset-circuit          # 熔斷後人工解除
jobfinder run --replay 42        # 拿第 42 次的原始 JSON 重跑評分,不重爬
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8    # 排程的執行紀錄
```

**緊急剎車**:把 `.env` 的 `JOBFINDER_SCRAPE_DISABLED` 改成 `1`,下次排程就會直接短路
(一個請求都不發)。或把 `config/config.yaml` 的 `scrape.enabled` 改成 `false`。
兩者都是**立即生效**,不需要重新部署任何東西 —— 這是跑在本機的好處之一。

### 收到告警時怎麼判斷

| 告警 | 意思 | 該做什麼 |
|---|---|---|
| 「403 但這是設定問題不是被封鎖」 | body 空的 403 = `Referer` 掉了 | 檢查 `config.yaml` 的 `scrape.referer`。**熔斷器沒有啟動**,修好就能跑 |
| 「104 抓取被中止」 | 真的被擋了,熔斷器已啟動 | **不要手動重跑。** 家用 IP 被擋是嚴重訊號,先停幾天再說 |
| 摘要帶「schema drift」 | 104 可能改版了 | 跑 `pytest tests/test_normalize.py` 看是哪些欄位對不上 |

### 為什麼不用絕對門檻

原本用 `threshold: 80`。後來拿同一次執行的原始資料連跑兩次 `--replay`
(**同一份資料、同一個 prompt**,`temperature: 0.2`),分數是這樣的:

```
玉山銀行 Data & AI Platform    80 / 88     ← 整批最好的那則
和碩 大數據分析/後端            81 / 83
中華精測 AI應用開發(EDA)        78 / 79
欣興電子 生成式AI平台            64 / 64
```

**雜訊約 ±8 分,而門檻剛好是 80。** 也就是整批最好的職缺會隨機出現或消失,
而且從外面看起來就是「今天沒有好缺」—— 不會有任何跡象。

所以改成 `scoring.mode: top_n`:雜訊只影響「誰排前面」,不會讓整天變空。
搭配 `top_n_floor: 60` 擋掉職缺荒日子的垃圾 —— 那個值刻意訂在遠低於雜訊帶的位置,
只砍真正的垃圾,不參與邊緣判斷。

> 通用教訓:**拿 LLM 分數做硬切點之前,先量雜訊。**
> 量法很便宜——同一份資料重跑兩次比對即可。門檻若落在雜訊帶內,它就沒有鑑別力。

### 量體不對時調哪裡

看 `jobfinder status` 的趨勢後調 `config/config.yaml`:

| 症狀 | 調法 |
|---|---|
| 推太少(連續幾天 0 則) | `targeting.min_employee_count` 500 → 300,或 `scoring.top_n_floor` 60 → 50 |
| 推太多雜訊 | 收斂 `targeting.industry_prefixes`,或調低 `scoring.top_n` |
| `jobs_filtered_out` 等於 `jobs_new` | **過濾條件太窄,或 104 改了欄位** —— 日報會靜默歸零,優先查這個 |

---

## 已知限制

- **104 的 API 沒有版本承諾。** 舊教學用的 `/jobs/search/list` 已經 404 死掉過一次,
  而且這次改版連欄位型別都變了(`data` 從物件變陣列、`salaryDesc` 消失)。
  改版時 `normalize.py` 會記 schema drift,摘要訊息裡會出現警告。
- **`Referer` 白名單隨時可能換成真正的 bot detection。** 告警分辨得出來
  (空 body = 設定問題,挑戰頁 = 真被擋)。屆時的選項是接台灣住宅代理,
  或把 `scrape.mode` 切成 `browser`(程式碼與測試都在,但那條路在資料中心 IP 上已證實無效)。
- **關鍵字無法精確鎖定職稱。** 104 的搜尋是全文模糊比對,`kwop`(職稱搜尋)在
  robots.txt 禁用清單裡。所以職缺的組成只能靠抓回來之後的規則層收斂,
  不能靠搜尋條件。
- **`top_n` 模式下「今天最好的」不等於「今天值得投的」。** 相對排序保證每天有東西看,
  但沒有保證品質 —— `top_n_floor` 只擋垃圾,不保證中間那幾則值得花時間。
- **需要電腦在 08:00 前後開機。** 沒開機的話開機後會補跑;
  但如果連續幾天沒開,那幾天的職缺就真的錯過了(`isnew=3` 只涵蓋三日內)。
- **合規**:104 的 robots.txt 的 `Disallow: /jobs/search/?*page=*` 針對的是搜尋頁面路徑;
  本專案打的 API 路徑落在 `Allow: /jobs/` 之下,且避開所有 robots.txt 明列的禁用參數。
  `Content-Signal: ai-train=no, search=yes, ai-input=yes` 也明示個人推論用途是允許的。
  定位為個人求職自用、每日一次、不對外發布 —— 詳見 [SPEC.md §10](SPEC.md)。
  若要改變用途,請重新評估。
