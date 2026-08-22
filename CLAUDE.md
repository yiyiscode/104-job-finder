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
  scrape/
    budget.py    ⚠️ RequestBudget.acquire():所有請求的唯一守門員
    blocking.py  ⚠️ 封鎖訊號偵測 + 三種 403 分辨 + 熔斷器 + assert_anonymous()
    urls.py      URL 組裝 + 路徑黑名單 + robots.txt 禁用參數
    browser.py / interceptor.py / search.py / detail.py / playwright_source.py
                 瀏覽器備援(mode: browser 才會用到)
  storage/       Volume<->local 檔案同步、去重邏輯
  scoring/       兩階段 LLM 評分 + 程式端校正
  notify/        Telegram
```

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
- **`period` 是實際要求年資的數字**(0 = 不拘),不是級距代碼 ——
  送 `jobexp=1,3` 回傳 0/2/3,送 `jobexp=10` 回傳 6~9
- **`optionEdu` 是 int 陣列**:`3`=專科 `4`=大學 `5`=碩士 `6`=博士;多碼取最低者加「以上」
- **`tags` 是 dict**,以參數名為 key。`desc` 為空時**不要**退回 `param`(那是 `wf1`/`wf7` 之類的內部代碼)
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

**已上線。** Windows 工作排程器每天 08:00 觸發,256 個測試全綠。

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

- `scoring.threshold: 80` —— 70 分在單一關鍵字下就頂到每日上限,而 70–79 與 80+ 之間有斷層
- `verdict` 用 **enum 約束**。沒有它,便宜模型會自己發明 `worth_a_shot` 這種值
  (實測 22 筆中 9 筆),而 verdict 是交叉驗證機制的一半
- **無效的 verdict 回 `None` 不是 `"maybe"`** —— `maybe` 是負面訊號會觸發降級,
  但模型用字不合格不代表它認為職缺不好。曾因此讓一個 75 分的職缺掉到 68 分消失
- prompt 明講**「待遇面議」給中間值不要扣分** —— 台灣八成職缺不揭露薪資,
  拿它扣分等於懲罰八成的職缺,還讓 practical_fit 失去鑑別力

### 改動時的注意事項

- 動 `scrape/` 或 `http_source.py` 之前先看 `tests/test_guardrails.py`
- 加新的抓取行為時,**一定要走 `RequestBudget.acquire()`**,不要自己發請求
- **`scripts/` 底下每個腳本都必須有 argparse** —— `tests/test_scripts.py` 會驗
  `--help` 印得出 `usage:`。少了它,`--help` 會把整個腳本跑一遍
  (`send_test_message.py` 曾因此在測試裡真的送出 6 次 Telegram 訊息)
- fixture 的詳細頁一律命名 `detail_{detail_id}.json`(跟真實 API 的 key 一致)
- `--replay` / `--from-fixtures` 會跳過去重(`rescore_all`),不然第二次跑永遠是 0 筆
