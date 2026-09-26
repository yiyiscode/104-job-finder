# todo.md — 已封存

> ⚠️ **這份是 2026-08 首次交付時的清單,內容已經過期,保留只為了保存當時的判斷。**
> 目前的進度與下一步請看 **[RESUME.md](RESUME.md)**。

## 當時寫錯、現在已經不成立的兩件事

| 當時寫的 | 實際狀況 |
|---|---|
| 「fixture 目前是依調查結果**手工建的合成資料**,`normalize.py` 的欄位對應還是假設」 | **已不成立。** `tests/fixtures/*.json` 是 2026-08-21 用 `scripts/probe_api.py` 錄下的**真實 104 回應**,欄位對應都對過了 |
| 「最後才是 `modal run modal_app.py::daily_run`」 | **已驗證失敗。** Modal 的 AWS 資料中心 IP 過不了 104 的 Cloudflare(`cf-mitigated: challenge`),改用真實瀏覽器更直接跳 Turnstile 互動挑戰。**因此改為 Windows 工作排程跑在本機**,`modal deploy` 不要執行 |

程式規模當時寫「4,447 行 / 34 個模組,2,014 行測試」,現在是 36 模組 5,289 行 + 2,993 行測試、286 個測試。

---

## 當時的交付內容(仍然正確的部分)

**文件**:`CLAUDE.md`(專案上下文)、`SPEC.md`(詳細規格)、`README-dev.md`(安裝與啟用)。

**使用者最在意的三件事怎麼落實的** —— 這一段的設計至今沒變:

### ① 絕不登入 104

- `config.yaml`、`.env.example`、Modal Secret 都沒有帳密欄位,而且 `config.py` 會掃描整份設定,出現 `username`/`password`/`token` 之類的鍵就拒絕啟動
- 進搜尋頁前跑 `assert_anonymous()`,發現 `104_session`/`ARJ`/含 token、member 的 104 cookie 就中止整趟並告警
- profile 存檔用白名單(只留 `cf_clearance`/`__cf_bm`),身分 cookie 連存都不存
- `bootstrap_profile.py` 開頭警告不要登入,結束前自檢,偵測到就刪掉 profile 要求重做

### ② 降到最低的被鎖機率

- 封鎖訊號(403/429/503、`Just a moment...`、Turnstile、1020)→ **零重試**,一路拋到頂
- 熔斷器:被擋一次冷卻 24h → 第二次 72h → 第三次要手動 `reset-circuit`
- `RequestBudget` 是唯一的導覽入口:節流(3–5s 帶 jitter)與計數不可能被繞過;總導覽數硬上限 40、全程序列無併發
- `--headful` 不帶 `--limit` 直接拒絕執行;`probe_api.py` 已有 fixture 就拒絕跑
- 緊急剎車 `JOBFINDER_SCRAPE_DISABLED=1`

### ③ 順帶修正了原始需求的三個錯誤

`jobexp=3` 其實是「1~3年」不是「3年以下」(已改 `1,3`)、`order=15` 是符合度排序不是日期(已改用 `isnew`)、`area=6001006000` 是新竹縣市合併。

---

## 當時抓到的兩個真 bug(已修,有回歸測試)

1. `Database` 在倉庫端沒檔案時會沿用 `/tmp` 的舊 DB —— 在 Modal 上就是「暖容器把已刪掉的熔斷狀態復活」
2. `--replay` 被去重擋掉導致深評 0 筆,等於整個旗標作廢
