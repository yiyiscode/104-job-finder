# CLAUDE.md

104 人力銀行每日職缺日報。每天 08:00(台北)抓取符合條件的 AI 工程師職缺 → LLM 對照 `profile.md` 評分 → 只把「今天新出現且達標」的推送到 Telegram。

詳細需求與驗收標準見 [SPEC.md](SPEC.md)。

---

## ⚠️ 先讀這段:Cloudflare 是「看 IP 信譽」的,不是「沒有」

**這個專案在這件事上錯過兩次,方向還相反,所以請完整讀完再動手。**

| 時間 | 判斷 | 錯在哪 |
|---|---|---|
| 最初研究 | 「API 有 Cloudflare,必須用真實瀏覽器」 | 用 WebFetch 打的 —— **那個管道設不了 `Referer`,而且從資料中心出去**。兩個因素混在一起 |
| 我的更正 | 「104 沒有 Cloudflare,只有 Referer 白名單」 | 只在**家用台灣住宅 IP** 驗證過,就推論成通則 |

**實測數據(2026-08-22):**

| 來源 | 請求 | 結果 |
|---|---|---|
| 家用台灣住宅 IP | 不帶 `Referer` | `403` · **0 bytes** · 無 Cloudflare 標記 → origin 層規則 |
| 家用台灣住宅 IP | 帶 `Referer` | **`200` · 117 KB JSON** · 連續 19 個請求全過 |
| Modal(AWS 雪梨 `52.63.49.208`) | 帶 `Referer` | **`403` · `cf-mitigated: challenge` · `Just a moment...`** |

**正確的理解是:Cloudflare 的挑戰依 IP 信譽觸發。**
台灣住宅 IP 被放行到 origin(那裡才輪到 `Referer` 白名單把關);
資料中心 IP 在碰到 origin 之前就被 Cloudflare 攔下。

所以兩件事**同時為真**,不是二選一:
- `Referer` 白名單存在(信譽夠的 IP 會遇到它)
- Cloudflare 挑戰也存在(信譽不夠的 IP 會先遇到它)

診斷指令:`modal run modal_app.py::diagnose` —— 從 Modal 的 IP 發**一個**請求,
把 egress IP、`cf-mitigated`、`server`、body 全部印出來。分辨這三種狀況只能靠看回應本身:

| 症狀 | 意思 |
|---|---|
| `403` + body 空 | origin 的 `Referer` 規則 → 我方設定問題,**不熔斷** |
| `403` + `cf-mitigated: challenge` | Cloudflare 依 IP 信譽出的挑戰 → 換 IP 或用真實瀏覽器 |
| `429` / `503` | 速率限制 → 熔斷 |

---

## 🚫 絕對禁止(違反這些比功能壞掉嚴重得多)

保護順位:**① 104 求職帳號 > ② 家中住宅 IP > ③ Modal 雲端 IP > ④ 功能完整度**。
衝突時一律按這個順序犧牲。**寧可少抓職缺,絕不冒險。**

1. **絕不登入 104 帳號。** 不加帳密設定欄位、不寫登入流程、不碰系統 Chrome 的 User Data 目錄。目標資料對未登入訪客完全開放,登入零好處。帳號被停權 = 使用者失去真實求職身分,無可替代。
2. **絕不對封鎖訊號重試。** 429/503、`Just a moment...`、Turnstile → 立刻中止整次執行。重試是把「暫時被挑戰」變成「永久封 IP」的頭號原因。
3. **絕不併發抓取。** 不用 `asyncio.gather` 打 104,全程序列。
4. **絕不繞過 `RequestBudget.acquire()`。** 每個對 104 的請求 —— HTTP 或瀏覽器 —— 都必須先過它(強制節流 + 計數 + 路徑黑名單)。
5. **絕不碰投遞相關路徑。** `apply=form`、應徵、收藏、追蹤公司 — 這些是「已登入使用者的動作」,觸發即異常訊號。
6. **開發期絕不反覆打 104。** 用 `--from-fixtures`。真要連線必須 `--limit`(硬上限 5)。
7. **絕不放寬硬上限。** config 只能往更保守調;`config.py` 對超限值直接拒絕啟動。要改上限必須先在此檔說明理由。
8. **絕不在 query string 放 robots.txt 禁用的參數**(`kwop`、`hotJob`、`recommendJob`、`irsTag`、`expansionType` 等)。`urls.py` 會擋,別想繞過。

完整規則與理由見 [SPEC.md](SPEC.md)。

---

## 指令

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"   # 安裝
pytest                                   # 全部離線,跑真實 fixture
ruff check src tests && ruff format src tests

# 開發:完全不碰 104
python -m jobfinder.cli run --from-fixtures --fake-llm --dry-run
python -m jobfinder.cli run --replay <RUN_ID>    # 拿舊 raw JSON 重跑評分,調 prompt 用
python -m jobfinder.cli reset-circuit            # 熔斷後人工解除
python -m jobfinder.cli status                   # 最近執行與 requests_used 稽核

# 本機 Web UI(週一選家):只讀 jobs.db 快照、不連 104,只綁 127.0.0.1
uv pip install -e ".[ui]"                        # streamlit 是選用依賴,排程環境不必裝
python -m jobfinder.cli ui                       # http://127.0.0.1:8501

# 第 3 道閘門「必備命中率」:只打 OpenRouter、不連 104。每日排程在 pipeline 之後自動跑
python -m jobfinder.cli hitrate --dry-run        # 只列出會算哪些,不花錢
python -m jobfinder.cli hitrate --fake-llm       # 技能詞典粗估,離線開發用(數字不可信)
python -m jobfinder.cli hitrate                  # 真的算,每次 ≤ max_jobs_per_run 筆、≤ cost_cap_usd

# 祕密掃描(git hook 本體)。clone 後要跑一次 git config core.hooksPath .githooks
python scripts/check_secrets.py --staged         # pre-commit 自動跑:金鑰格式 + .env 實際值 + .env/*.db 路徑
python scripts/check_secrets.py --history        # pre-push 自動跑:整個 commit 歷史(轉公開前必跑)

# 會連線 104 —— 每條都是一次性的,不要反覆跑
python scripts/probe_api.py                      # 錄 fixture,已有檔案會拒絕執行
python -m jobfinder.cli run --limit 5

# 排程(每天 08:00,錯過會在開機後補跑)
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
Start-ScheduledTask -TaskName "JobFinder Daily"          # 手動觸發一次
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8               # 看排程的執行紀錄

# Modal(目前不用,見下方「為什麼跑在本機」)
modal run modal_app.py::diagnose        # 從雲端 IP 發一個請求,看被擋成什麼樣
```

---

## 架構

```
Windows 工作排程器 (08:00 每日,錯過開機後補跑)
  └─ scripts/run_daily.cmd → jobfinder run
  └─ 三道護欄:緊急開關 / 熔斷器 / 每日一次 —— 全都在發任何請求之前
  └─ HttpJobSource(預設)
       ├─ 逐關鍵字 GET /jobs/search/api/jobs(帶 Referer)
       └─ 逐筆 GET /job/ajax/content/{detail_id}(Referer = 該職缺自己的頁面)
     └ 備援 PlaywrightJobSource(mode: browser):真實瀏覽器 + response 攔截
  └─ normalize → SQLite 去重(只留今天新出現的)
  └─ targeting.py 規則層:產業(半導體/金融代碼前綴)+ 公司規模(≥500人)
  └─ Stage 1 粗篩(批次 15 筆) → Stage 2 深評(逐筆)→ 程式端校正
  └─ Telegram 摘要 + 每職缺一則卡片
```

### 模組邊界

`ports.py` 定義 `JobSource / Scorer / Notifier` 三個 Protocol。這不只為了測試 ——
**它讓「抓取方式整個換掉」變成換一個實作的事**:這次從瀏覽器改成純 HTTP,
`pipeline.py`、`storage/`、`scoring/`、`notify/` 一行都沒改。

```
src/jobfinder/
  config.py      Pydantic 設定驗證。硬上限在此強制,超限拒絕啟動
  models.py      領域模型 JobSummary / JobDetail / ScoredJob / RunReport
  ports.py       三個 Protocol
  pipeline.py    唯一的流程編排。不做任何 IO,全靠注入
  normalize.py   raw JSON → 領域模型。⚠️ 唯一認識 104 JSON 結構的地方
  http_source.py ⭐ 預設抓取來源。純 httpx,無瀏覽器
  fixture_source.py  --from-fixtures / --replay 的離線來源
  targeting.py   ⭐ 產業/規模規則層。跑在粗篩前,不符合的連 LLM 都看不到
  scrape/
    budget.py    ⚠️ RequestBudget.acquire():所有請求的唯一守門員
    blocking.py  ⚠️ 封鎖訊號偵測 + 三種 403 分辨 + 熔斷器 + assert_anonymous()
    urls.py      URL 組裝 + 路徑黑名單 + robots.txt 禁用參數
    browser.py / interceptor.py / search.py / detail.py / playwright_source.py
                 瀏覽器備援(mode: browser 才會用到)
  storage/       Volume<->local 檔案同步、去重邏輯
  scoring/       兩階段 LLM 評分 + 程式端校正
  notify/        Telegram
  webui/         ⭐ 本機 Web UI(Streamlit)。三道閘門、標記、技能趨勢
    gates.py     閘門規則 + 說明文字(兩者放一起,改規則的人一定看得到說明)
    snapshot.py  ⚠️ jobs.db 只讀快照 —— 見 docs/adr/0001
    decisions.py 標記存獨立的 decisions.db,只追加
    app.py       唯一 import streamlit/pandas 的檔案
```

領域用詞(閘門 vs 規則層、摘要層 vs 全文層、標記…)見 [CONTEXT.md](CONTEXT.md)。

---

## 104 的真實欄位(已實測,別憑印象改)

fixture 是 2026-08-21 錄下的真實回應。以下每一條都是踩過或驗證過的:

**搜尋回應 `/jobs/search/api/jobs`**
- **`data` 本身就是陣列**,沒有 `data.list`;總數在 `metadata.pagination.total / .lastPage`
- **`jobNo` 與連結尾段是不同的 ID**(`jobNo=15305872` 但 `link.job` 是 `/job/94234`)。
  詳細頁 API **只吃連結尾段**,拿 `jobNo` 去打會回 404「職務不存在」。
  領域模型分成 `job_no`(去重主鍵)與 `detail_id`(打 API 用)。
- **沒有 `salaryDesc`**,只有 `salaryLow`/`salaryHigh`,薪資字串要自己組
- **`s10` 是薪資類型**(混淆過的欄位名,等同詳細頁的 `salaryType`):
  `10`=面議 `50`=月薪 `60`=年薪。**不看它就會把年薪 567,000 寫成「月薪 56 萬」**
- **`period` 是「年資 + 1」**(0 = 不拘、2 = 1年以上、3 = 2年以上),一律用
  `normalize.period_to_years()` 換算。⚠️ 2026-09-24 以前這裡寫「是實際年數」,**錯了一個月**,
  全站多算 1 年(使用者在 104 看到「1年以上」、卡片寫「2年以上」)。對 369 筆詳細頁 `workExp` 驗證:
  0→不拘 266、2→1年以上 59、3→2年以上 41。送 `jobexp=1,3` 回 0/2/3,送 `jobexp=10` 回 6~9
- **`optionEdu` 是 int 陣列**:`3`=專科 `4`=大學 `5`=碩士 `6`=博士;多碼取最低者加「以上」
- **`tags` 是 dict**,以參數名為 key。`desc` 為空時**不要**退回 `param`(那是 `wf1`/`wf7` 之類的內部代碼)
- **`coIndustry` / `employeeCount` 列表就有**,不必抓詳細頁(1,637 筆真實資料缺失率 0)。
  `coIndustry` 是階層式代碼:`1001006` 半導體業 / `1001005` 電子零組件 /
  `1001004` 光電及光學 / `1001003` 電腦及消費性電子製造 / `1004` 金融投顧及保險。
  **產業過濾一律比對代碼前綴不比對中文名** —— 104 改名稱時代碼不會變
- `description` **不是完整 JD**(中位數 123 字),詳細頁還是得抓。而且它含換行,
  進 `to_screen_block()` 前要壓成單行,否則會打散批次 prompt 的「每筆四行」結構

**詳細回應 `/job/ajax/content/{detail_id}`**
- `condition.edu` 與 `condition.workExp` 已經是**文字**,不用查代碼表
- `jobDetail.salary` 是組好的字串;`jobCategory` 是 `{code, description}` 陣列
- 打錯 ID 會回 `{"error": {"code": 11201, "message": "職務不存在"}}` —— 要記 drift 不是 crash

**搜尋參數**(原始需求裡是錯的,別改回去)
- `jobexp` 是**級距值不是上限**:`1`=1年以下 `3`=1~3年 `5`=3~5年。要 3 年以下就送 `1,3`
- **不要用 `order`**:`order=15` 是符合度排序不是日期,且改版會漂移
- `area`:`6001001000` 台北 / `6001002000` 新北 / `6001005000` 桃園 / `6001006000` **新竹縣市(合併)**
- `/jobs/search/list` **已 404 死掉**,網路上 2019–2022 的教學全部過時

---

## 慣例與已知地雷

**設定**:`config/config.yaml` 是唯一真實來源。秘密走環境變數(本機 `.env`,Modal 用 Secret,變數名相同 → 程式碼零分支)。

**兩種 403 完全不同,處置相反**:
- `403` + **body 空** → 我方 `Referer` 掉了。丟 `SourceMisconfigured`,告警但**不熔斷**(冷卻 24 小時對設定錯誤毫無幫助)
- `403` + body 有挑戰頁特徵 → 104 真的上防護了。丟 `ChallengeBlocked` → 熔斷 → 考慮切 `mode: browser`
- 瀏覽器路徑讀 body 可能失敗,所以 `detect_block()` 對 403 維持 **fail-closed**;分流只在 HTTP source 做

**`user_agent` 必填且要誠實標示**(config 強制驗證,含 `Mozilla` 直接拒絕啟動):
- 104 的 API 不檢查 UA,偽裝成瀏覽器沒好處,誠實反而降低合規風險、也讓 104 有管道聯絡
- **browser 模式完全不讀這個欄位** —— `scrape/browser.py` 一律用 Chromium 自己的真實 UA。
  在那邊偽造 UA 才危險(與實際指紋不一致),而我們的做法是**根本不傳**

**Modal / Volume**:
- **SQLite 不能直接在 Volume 上跑**(網路 FS 無 POSIX 鎖)→ 複製到 `/tmp` 操作,結束原子換名寫回 + `volume.commit()`。用 `PRAGMA journal_mode=DELETE`,**不要 WAL**
- `Database.open()` 會先刪掉本地殘留檔。少了這步,倉庫端沒有 db 時會沿用上次跑剩的 —— 在 Modal 上就是「暖容器把已刪掉的熔斷紀錄復活」

**LLM**:
- 兩階段都用便宜模型。**粗篩必須批次**(履歷 context 是固定成本,逐筆呼叫等於把履歷付 15 次)
- **不信任模型的數字**:`total` 程式端重算;`verdict` 交叉驗證;年資用 hard rule 夾住 ——
  現在可以直接讀 `period` 數字,比對中文跑正規表達式可靠得多
- 使用者「專案豐富但年資 0–1 年」是 LLM 容易誤判的組合,它會把人當成資深工程師
- model id **不要憑記憶寫**,跑 `scripts/check_models.py` 對 OpenRouter `/models` 驗證

**Telegram**:`parse_mode` 用 **HTML 不用 MarkdownV2**(後者要跳脫 18 個字元,職缺標題滿是 `(` `)` `-` `.`,漏一個整則 400)。400 時降級純文字重送。

**測試**:護欄本身一定要有測試(`tests/test_guardrails.py`、`tests/test_http_source.py`)。
**護欄沒有測試等於沒有護欄。**

---

## 現況:跑在本機,不在雲端

**已上線。** Windows 工作排程器每天 08:00 觸發,431 個測試全綠(2026-09-24,含 Web UI 與命中率)。

### 為什麼跑在本機而不是 Modal

不是偷懶,是**雲端兩種模式都實測過不了**:

| 嘗試 | 結果 |
|---|---|
| Modal + 純 HTTP | `403` · `cf-mitigated: challenge` · `Just a moment...` |
| Modal + Patchright 真實瀏覽器 | **更糟** —— 首頁直接跳 Turnstile **互動**挑戰,程式無法自動通過 |
| 家用台灣住宅 IP + 純 HTTP | ✅ 連續 19 個請求全 `200`,Cloudflare 連挑戰都沒出 |

瀏覽器模式反而被升級成互動挑戰,代表 **AWS 資料中心 IP 本身就被 104 的 Cloudflare
歸類為高風險訪客** —— 不是換個做法就能繞過的。

`modal_app.py` 與整套瀏覽器程式碼**完整保留**。哪天接了台灣住宅代理
(填 `config.yaml` 的 `scrape.proxy`),就能切回雲端全自動。
在那之前 `modal deploy` **不要執行** —— cron 會每天失敗然後把熔斷器燒到需要人工解除。

### 本機排程的細節

- `scripts/run_daily.cmd` —— 排程的進入點。切工作目錄、設 UTF-8、附加到 `local_data/daily.log`
- `scripts/install_task.ps1` —— 註冊/移除排程。不需要管理員權限
- **`-StartWhenAvailable`**:08:00 沒開機的話,開機後會補跑。程式的「每日一次」護欄
  確保補跑不會重複推播
- `-RunOnlyIfNetworkAvailable`、`-MultipleInstances IgnoreNew`、逾時 30 分鐘

### 已校準的評分參數(依真實資料調過,別隨手改回去)

- **`scoring.mode: top_n`(2026-09-23 改,別改回 threshold)** —— 深評分數的雜訊實測
  約 **±8 分**:同一份資料、同一個 prompt 連跑兩次 replay,整批最好的那則一次 80、
  一次 88。`threshold: 80` 剛好卡在雜訊帶正中央,等於讓最好的職缺隨機出現或消失,
  而且**不會有任何跡象**(看起來就像「今天沒有好缺」)。相對排序讓雜訊只影響排序。
  搭配 `top_n_floor: 60` 擋掉職缺荒日子的垃圾 —— 那個值刻意遠低於雜訊帶,只砍垃圾、
  不參與邊緣判斷。`threshold: 80` 仍保留,因為卡片的分數燈號還在用它。
- ~~`scoring.threshold: 80`~~ —— 原本的校準理由(70 分頂到上限、70–79 與 80+ 有斷層)
  在產業/規模過濾上線後已失效:量體剩約 4.5 筆/天,`max_per_day: 10` 根本碰不到
- `verdict` 用 **enum 約束**。沒有它,便宜模型會自己發明 `worth_a_shot` 這種值
  (實測 22 筆中 9 筆),而 verdict 是交叉驗證機制的一半
- **無效的 verdict 回 `None` 不是 `"maybe"`** —— `maybe` 是負面訊號會觸發降級,
  但模型用字不合格不代表它認為職缺不好。曾因此讓一個 75 分的職缺掉到 68 分消失
- prompt 明講**「待遇面議」給中間值不要扣分** —— 台灣八成職缺不揭露薪資,
  拿它扣分等於懲罰八成的職缺,還讓 practical_fit 失去鑑別力

### 產業／規模規則層(2026-09-23 新增,`targeting.py`)

**方向已定案:聚焦半導體／電子與金融的大公司(員工 ≥500)。** 關鍵字同步從
AI 主敘事換成資料工程(`資料工程師`/`資料倉儲工程師`/`BI工程師`/`資料科學家`…),
並**刻意拿掉 `系統整合工程師`、`流程自動化`** —— 它們命中的主要是 SI／接案公司
(舊資料「電腦系統整合服務業」125 筆,量第二大),與「聚焦大廠」反向拉扯。

三件別改回去的事:

1. **過濾放在程式端不放在 104 的搜尋參數。** 搜尋請求數 = 關鍵字 × 頁數,
   跟有沒有帶產業參數無關 —— 在 104 端過濾**省不到任何預算**,卻要為一個沒實測過的
   參數多打一次 104。程式端過濾是零額外請求、零封鎖風險、可離線測試。
2. **過濾跑在粗篩之前。** 不符合的職缺不該花 LLM token,更不該吃掉每次 18 個詳細頁名額。
3. **訊號消失時 fail-open 不是 fail-closed。** 104 哪天不給 `employeeCount` 了,
   fail-closed 會讓日報靜靜變成 0 則 —— 跟「今天沒新職缺」長得一模一樣,可能幾週才發現。
   所以某欄位在整批裡缺超過一半時,**停用該條件並大聲告警**(`MISSING_RATIO_LIMIT`)。

**量級參考:** 舊資料 32 天、DE 類關鍵字命中的 977 筆,過濾後剩 143 筆 ≈ 4.5 筆/天。
實際 replay run 27(2026-09-23,238 筆新職缺的大日子):**94 → 過濾掉 87 → 深評 4 → 推播 4**。

**這一層解決的正是「日報都是 AI 工程師」。** run 27 用新關鍵字推的 10 則裡,
有 8 則是軟體/服務業小公司的 AI 職缺 —— 因為 **104 的關鍵字是全文模糊比對不是職稱比對**,
搜「資料工程師」會撈回 JD 裡提到「資料工程」的 AI 職缺(實測:「AI研發工程師」是被
`資料工程師` 撈進來的)。換關鍵字解決不了,**產業/規模過濾把那 8 則全部擋掉了**。

⚠️ **不要改用 `jobCat`(職務類別)做過濾 —— 已驗證失敗。** 104 確實有正規的職務類別代碼
(`2007001022` 資料工程師 vs `2007001020` AI工程師),而且列表就有、零額外請求,看起來是
完美解法。但雇主亂掛:「數據工程師(學士/碩士)」的 jobCat 是**統計精算人員/軟體工程師**,
整批最好的玉山 Data & AI Platform 是**其他資訊專業人員/雲端工程師**,反而「AI研發工程師」
的 jobCat **有**資料工程師。硬篩會殺掉最好的缺、留下 AI 缺,剛好做反。

`scoring/prompts.py` 也同步換成資料工程視角:`tech_fit` 的高權重是 SQL/ETL/排程/爬蟲
而非 LLM/RAG;`domain_fit` 直接點名他在金融(國泰人壽 CAP)與半導體(晶圓缺陷分類)
的真實實績;JD 要求 Spark/Airflow/dbt 這類他沒實作過的工具**扣分但不歸零**
(他自建過等價的排程與 ETL 管線)。`tests/test_scoring.py` 有錨點測試擋回頭改。

### 改動時的注意事項

- 動 `scrape/` 或 `http_source.py` 之前先看 `tests/test_guardrails.py`
- 動 `targeting.py` 或 config 的 `targeting` 之前先看 `tests/test_targeting.py`。
  **`industry_prefixes` 只能填數字代碼** —— 填中文名稱不會報錯、只會默默把職缺濾光,
  所以 `config.py` 直接拒絕啟動
- `runs` 表加欄位要走 `storage/db.py` 的 `MIGRATIONS`。`CREATE TABLE IF NOT EXISTS`
  對既有的表完全沒作用,少了遷移舊 db 會在 UPDATE 時炸 `no such column`
- **改 `jobs.status` 的 CHECK 要整表重建** —— SQLite 沒有 `DROP CONSTRAINT`。
  `_widen_jobs_status_check()` 做這件事,兩個必踩的雷:`scores` 對 `jobs` 有
  `ON DELETE CASCADE`,**外鍵沒關掉就 DROP TABLE 會把 scores 整張連帶刪光**;
  而 `PRAGMA foreign_keys` 在交易裡是 no-op,必須在 BEGIN 之前設。
  重建會連索引一起丟掉,所以 `ensure_schema()` 會再跑一次 schema.sql 補回來
- **兩種淘汰原因分開記**:`filtered_out`(規則層濾掉,LLM 沒看過)vs
  `screened_out`(LLM 粗篩刷掉,`scores` 有 `stage='screen'` 紀錄)。
  共用一個值就無法做 precision/recall 標註
- 加新的抓取行為時,**一定要走 `RequestBudget.acquire()`**,不要自己發請求
- **`webui/` 不得 import `scrape`/`http_source`/`pipeline`/`storage`/`httpx`**(`tests/test_webui_boundary.py` 會擋)。
  UI 按鈕觸發抓取 = 繞過預算、節流、熔斷三道護欄;用 `storage.Database` = 結束時 checkpoint 把 jobs.db 整檔寫回。
  **UI 絕不直接開 jobs.db**:pipeline 會 `os.replace` 蓋回它,Windows 上檔案被開著就會 checkpoint 失敗、
  當次資料與熔斷器狀態靜靜遺失。一律 `snapshot.take_snapshot()` 後讀副本(docs/adr/0001)
- **閘門 ≠ 規則層。** 規則層(`targeting.py`,≥500 人 + 產業)決定哪些職缺值得花 LLM;
  閘門(`webui/gates.py`,≥30 人、不限產業)是使用者挑家的條件,在 UI 端對全部職缺計算。
  別把兩者合併 —— 放寬規則層會改變每天的 LLM 成本與 18 個詳細頁名額
- **命中率(`hitrate/`)** 讀 jobs.db 快照、寫獨立的 `hitrate.db`,以 (job_no, 履歷雜湊) 為鍵 ——
  改 `profile-de.md` 會讓全部結果失效、下次排程重算(每次 30 筆,251 筆要約 9 天補完)。
  **LLM 只給逐條判定,百分比與「核心不符」由程式算**。別把「核心條件」拿掉:平均分數會把
  「卡在一個核心技術」稀釋掉(華碩 8vyka:平均 88%、人工 61%,差在 AWS 這一條)。
  改 prompt 會讓數字位移(同一筆 83% → 75%),改完要重跑驗證、並考慮清掉舊結果
- webui 的邏輯模組不能 import streamlit/pandas —— 256+ 個測試要在沒裝 `[ui]` 的環境也能跑
- **`scripts/` 底下每個腳本都必須有 argparse** —— `tests/test_scripts.py` 會驗
  `--help` 印得出 `usage:`。少了它,`--help` 會把整個腳本跑一遍
  (`send_test_message.py` 曾因此在測試裡真的送出 6 次 Telegram 訊息)
- fixture 的詳細頁一律命名 `detail_{detail_id}.json`(跟真實 API 的 key 一致)
- `--replay` / `--from-fixtures` 會跳過去重(`rescore_all`),不然第二次跑永遠是 0 筆
