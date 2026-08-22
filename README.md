# 104 每日職缺日報

每天早上 08:00(台北時間)自動到 104 抓取符合條件的 AI 工程師職缺,用 LLM 對照
[profile.md](profile.md) 評分,把「今天新出現且達標」的整理成日報推送到 Telegram。

- 需求規格與驗收標準:[SPEC.md](SPEC.md)
- 給 AI 助理的專案上下文:[CLAUDE.md](CLAUDE.md)
- 系統導覽與自我測驗:[docs/system-guide.md](docs/system-guide.md)

```
Windows 工作排程 08:00 → 三道護欄 → httpx 直接打 104 的 JSON API(帶 Referer)
    → SQLite 去重(只留今天新出現的)→ 粗篩(批次)→ 深評(逐筆)→ 程式端校正
    → Telegram 摘要 + 每職缺一則卡片
```

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
pytest                                                # 256 個測試,全離線
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

`config.yaml` 裡的兩個 model id 是**未驗證的占位值**。寫死一個已下架的 id 不會在啟動時
報錯,而是變成每天早上一則失敗告警。

### 3. 本機驗證

```bash
jobfinder run --from-fixtures --dry-run   # 用真 LLM 跑真實資料,看評分品質與排版
```

會在 `local_data/preview.html` 產生可用瀏覽器開的預覽。這一步調
`scoring.threshold` 與 `search.keywords`。

門檻目前是 **80**,那是依真實資料校準過的:70 分在單一關鍵字下就會頂到每日上限 10 則,
而 70–79 與 80+ 之間有明顯斷層。

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

### 調整門檻

第一週很可能推 0 則或推爆 10 則。看 `jobfinder status` 的趨勢後調
`config/config.yaml`:

- 推太少 → 調低 `scoring.threshold`
- 推太多雜訊 → 調高 `scoring.threshold`,或收斂 `search.keywords`
- 分數怎麼調都不準 → 把 `scoring.mode` 改成 `top_n`,改用相對排序

---

## 已知限制

- **104 的 API 沒有版本承諾。** 舊教學用的 `/jobs/search/list` 已經 404 死掉過一次,
  而且這次改版連欄位型別都變了(`data` 從物件變陣列、`salaryDesc` 消失)。
  改版時 `normalize.py` 會記 schema drift,摘要訊息裡會出現警告。
- **`Referer` 白名單隨時可能換成真正的 bot detection。** 告警分辨得出來
  (空 body = 設定問題,挑戰頁 = 真被擋)。屆時的選項是接台灣住宅代理,
  或把 `scrape.mode` 切成 `browser`(程式碼與測試都在,但那條路在資料中心 IP 上已證實無效)。
- **需要電腦在 08:00 前後開機。** 沒開機的話開機後會補跑;
  但如果連續幾天沒開,那幾天的職缺就真的錯過了(`isnew=3` 只涵蓋三日內)。
- **合規**:104 的 robots.txt 的 `Disallow: /jobs/search/?*page=*` 針對的是搜尋頁面路徑;
  本專案打的 API 路徑落在 `Allow: /jobs/` 之下,且避開所有 robots.txt 明列的禁用參數。
  `Content-Signal: ai-train=no, search=yes, ai-input=yes` 也明示個人推論用途是允許的。
  定位為個人求職自用、每日一次、不對外發布 —— 詳見 [SPEC.md §10](SPEC.md)。
  若要改變用途,請重新評估。
