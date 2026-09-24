# RESUME.md — 目前進度 / 下一步

> 說「讀 RESUME.md 繼續」即可接續。背景與更早的優化方向見 [docs/handoff-2026-09-23.md](docs/handoff-2026-09-23.md)。

**最後更新:2026-09-24 晚**　分支 `feat/webui`(未合併進 master)。397 個測試全過。

---

## 2026-09-24 晚:本機 Web UI(`feat/webui`)

週一選家用的 Streamlit 兩頁 UI。規格 `docs/webui-spec.md`(**未追蹤,使用者的檔案,沒有 commit**)。

```bash
uv pip install -e ".[ui]"
python -m jobfinder.cli ui        # http://127.0.0.1:8501
```

| 頁 | 內容 |
|---|---|
| 候選清單 | 全部職缺 + 三道閘門燈號(規則說明在頁首 expander)、日期/產業/職稱類別/職稱關鍵字/英文/pipeline 狀態篩選、投/不投/待確認標記、匯出 MD/CSV |
| 技能趨勢 | 日期/產業/職稱類別縮範圍;分群熱圖;摘要層 vs 全文層分開報;週變化;薪資與規模分佈;每張圖標 N 與偏誤 |

**grilling 定案(別改回去):**

1. **母體是全部職缺,閘門在 UI 端算,不改 pipeline 規則層。** 規格的硬門檻(≥30 人、不限產業)比規則層寬;
   放寬規則層會改變每天的 LLM 成本與詳細頁名額
2. **標記存獨立的 `decisions.db`,jobs.db 只讀快照**(docs/adr/0001)。pipeline 會整檔 `os.replace`,
   寫在 jobs.db 會被覆蓋;UI 開著原檔會讓 Windows 上的 checkpoint 失敗、資料靜靜遺失
3. 標記**只追加**、取最新;不投必填原因,DB CHECK 與 Python 兩邊擋
4. 閘門②③、命中率 **UI 不呼叫 LLM**,顯示「未判定/未計算」;新人培訓只做正則。**未判定不算卡住**
5. 產業/職稱類別預設各只選一類(半導體/電子 × 資料工程),使用者要求
6. `jobfinder ui` 永遠只綁 127.0.0.1

**用真實資料看到、還沒處理的:**

- **經歷 ≥2 年排除刷掉 43%**(736/1721)—— 搜尋送 `jobexp=1,3` 會回 period 2、3。規則是使用者定的,看過畫面後再決定要不要鬆
- **預設視圖很空**:半導體/電子 × 資料工程,全期間只有 26 筆、本週 2 筆(9/23 前是 AI 關鍵字)。累積幾週會好
- **既有 bug(沒修,不在這次範圍):`normalize.format_salary` 對「以上」型薪資**會輸出「月薪 39,000~9,999,999 元」
  (104 用 `9999999` 表示無上限,190 筆)。Telegram 卡片應該也有同樣問題。UI 端已自己處理
- 技能詞典從 `docs/skill-demand/skill_demand.py` 搬進 `webui/skills.py` 並補資料工程工具;
  舊腳本(未追蹤)沒動

**下一步:** 使用者試用 → 回饋 → 合併進 master(default branch,要先問)。之後 P2 做閘門②的 LLM 判斷與命中率,
UI 欄位已經預留。

---

## 這次做完的事

目標:**主敘事從 AI 工程師換成資料工程師/資料科學家,並聚焦半導體與金融的大公司。**

| # | 改動 | 位置 |
|---|---|---|
| 1 | 關鍵字拿掉 `系統整合工程師`、`流程自動化`(命中 SI/接案,與聚焦大廠反向拉扯),換成 `資料倉儲工程師`、`BI工程師` | `config/config.yaml` |
| 2 | 新增產業/規模規則層:產業代碼前綴 + 員工數 ≥500,跑在粗篩**之前** | `src/jobfinder/targeting.py` |
| 3 | `JobSummary.industry_code`(來自 `coIndustry`)、`RunReport.jobs_filtered_out` | `models.py` / `normalize.py` |
| 4 | `runs` 表加 `jobs_filtered_out`,含 `MIGRATIONS` 遷移機制 | `storage/db.py`、`schema.sql`、`repo.py` |
| 5 | Telegram 摘要的漏斗補上「目標產業/規模」那一關 | `notify/format.py` |
| 6 | prompt 換成資料工程視角(tech_fit 高權重改 SQL/ETL/排程;domain_fit 點名金融與半導體實績;地點補台中) | `scoring/prompts.py` |
| 7 | 25 個新測試(`test_targeting.py` 18 + pipeline 4 + prompt 錨點 3) | `tests/` |

**已驗證**:`load_config()` 通過、`run --from-fixtures --fake-llm --dry-run` 跑得完、ruff 乾淨、281 passed。
**未驗證**:尚未用新設定連線真實的 104。

---

## 追加:診斷「日報都是 AI 工程師」(同日下午)

使用者回報最近日報都是 AI 職缺。查證結果:

| 發現 | 證據 |
|---|---|
| **根因是召回不是關鍵字** | 104 的關鍵字是**全文模糊比對不是職稱比對**。run 27 的「AI研發工程師」是被 `資料工程師` 撈進來的,「AI 工程師(AI Gateway)」是被 `資料科學家` 撈進來的 |
| **產業/規模過濾已經解決它** | run 27 推的 10 則,在新過濾下只有 2 則存活(玉山銀行、和碩)。被刷掉的 8 則全是軟體/服務業小公司的 AI 職缺 |
| **prompt 改動貢獻很小** | replay 兩次比對,分數只動 ±6,且方向不單一 |
| ⚠️ **深評分數雜訊約 ±8 分** | 同資料同 prompt 跑兩次:玉山 80 / 88、和碩 81 / 83、EDA 78 / 79、欣興 64 / 64 |

**驗證失敗、別再試的方案:`jobCat` 職務類別過濾。** 104 有正規代碼(`2007001022` 資料工程師 vs `2007001020` AI工程師)且列表就有,但雇主亂掛:「數據工程師(學士/碩士)」掛**統計精算人員**,最好的玉山那則掛**其他資訊專業人員**,反而「AI研發工程師」**有**資料工程師。硬篩會殺掉最好的缺、留下 AI 缺。

**因此改用 `mode: top_n` + `top_n_floor: 60`**(commit `38670c1`)。順手修掉 top_n 模式的兩個既有問題:沒有品質下限、摘要在 top_n 下仍謊稱「達標(≥80)」。

replay run 27 在最終設定下的結果:`94 → 過濾掉 87 → 粗篩留 4 → 深評 4 → 推播 4`(88 玉山 / 83 和碩 / 79 中華精測 / 64 欣興電子)。

---

## 2026-09-24:第一次真實執行成功 ✅

08:00 電腦沒開,**15:01 補跑**(`-StartWhenAvailable` 正常運作),`exit code=0`。

```
run 33:抓 492 → 新 102 → 產業/規模濾掉 89 → 剩 13 → 粗篩留 6 → 深評 6 → 推播 4
請求 24/40 · 成本 $0.0127(過濾層上線前平均 $0.046)
```

推播的 4 則全部在目標產業:中國信託(銀行 25,000人)×2、仁寶電腦(電腦及週邊 9,000人)、
統一綜合證券(證券 1,500人)。**零則 AI 職缺。**

**兩件事驗證了 2026-09-23 的決策:**

1. 被深評但沒推的兩則(台達電「機械手臂AI工程師」57 分、「AI for Science」39 分)
   正是 AI 職稱,被 `top_n_floor: 60` 擋掉
2. **最高分只有 76** —— 如果還用 `threshold: 80`,今天會是 **0 則**,而且看起來
   就像「今天沒好缺」。±8 雜訊那個論證當天就應驗了

起飛前檢查的三個指標全過:請求 24(遠低於 38)、`jobs_filtered_out` 89 ≠ `jobs_new` 102、
推 4 則落在預期的 2–4。昨天的 `notify_failed` 沒再發生。

---

## 2026-09-24 追加:粗篩紀錄與兩種淘汰狀態

使用者問「淺評結果有沒有存進 DB」—— **沒有**,而且順帶查出一個我前一天做出來的問題。

| 問題 | 現況 | 處置 |
|---|---|---|
| 淺評結果從沒進過 DB | `scores` 642 筆**全是 `deep`**。schema 的 CHECK 允許 `screen`、`save_score` 也留了 `stage` 參數,但唯一呼叫點在 `_deep_score_all` 且沒傳 | 新增 `repo.save_screen_drops()`,只寫被刷掉的 |
| `screened_out` 被兩種原因共用 | run 33 的 96 筆裡,89 筆是規則層濾的、7 筆是 LLM 刷的,**事後分不出來** | 規則層濾掉的改標 `filtered_out` |

**遷移的兩個雷(都踩過才知道):** SQLite 不能 ALTER 掉 CHECK 約束,放寬它要整表重建。
而 `scores` 對 `jobs` 有 `ON DELETE CASCADE`,**外鍵沒關掉就 `DROP TABLE jobs` 會把
`scores` 整張連帶刪光**;`PRAGMA foreign_keys` 在交易裡又是 no-op,必須在 `BEGIN` 之前設。
已先在真實 DB 的副本上驗過(1,721 jobs / 642 scores 一筆不差、索引都在),備份在
`local_data/jobs.db.bak-20260924-151551`。

### ⚠️ 開始存粗篩紀錄之後,第一批資料就顯示一件事

`--replay 33` 跑出來:「數位金融處數智應用科資料分析師」(統一證券)在 **run 33 通過粗篩、
深評 70 分、實際推播了**;在 replay 裡卻被粗篩給 **rough 30** 刷掉,理由是
「偏向商業分析與報表製作,非技術開發」。

**同一則職缺、同一個 prompt,粗篩的結論從「進」變成「連看都不看」。** 差別在批次組成
(run 33 篩 13 筆、replay 篩 6 筆)—— CLAUDE.md 早就記過「後段被前段錨定」。

這比深評的 ±8 更嚴重:深評的雜訊只影響排名,**粗篩的雜訊決定一則職缺有沒有機會被看到**。
`rough_threshold: 45` 是不是太高,現在終於有資料可以查了 —— **累積幾天再看,n=1 不能下結論。**

---

## 已完成:2026-09-24 08:00 的第一次真實執行

**2026-09-23 決定不 force 跑,等排程自然觸發。** 當天排程已在 11:32 補跑過(run 27,36 次請求),
再跑一次等於繞過「每日一次」護欄、今天第二次打 104,而 run 27 已把 238 筆職缺註冊進 DB,
去重會讓它們全變「不是新的」—— 收穫小、足跡加倍,不划算。

**起飛前檢查(2026-09-23 14:xx 全部確認過):**

| 項目 | 狀態 |
|---|---|
| 熔斷器 | `closed`,連續失敗 0 次 |
| 排程 | Ready,NextRunTime `2026/9/24 08:00` |
| 「今天已執行過」 | 以明天 08:00 判定 → `False`,不會被擋 |
| `scrape.enabled` / 環境變數 | 都允許抓取,mode=`http` |
| 請求預算 | 10 關鍵字 × 2 頁 = 20 搜尋 + 詳細頁上限 18 = **最壞 38**(硬上限 40) |
| Telegram | `getMe` / `getChat` 都 200 —— 今天的 `notify_failed` 是 `httpx.ConnectError`,**網路瞬斷不是 bug** |
| 選取模式 | `top_n=5`,`top_n_floor=60`;履歷 `profile-de.md` |

**跑完先看這三件事:**

1. `python -m jobfinder.cli status` —— `請求` 應在 38 以下,狀態不是 `blocked`
2. `runs.jobs_filtered_out` vs `jobs_new` —— **兩者相等代表過濾太窄或 104 欄位變了**,日報會靜默歸零
3. 實際推幾則 —— 預期 2–4 則。連續幾天 0 則就往下調 `top_n_floor` 或 `min_employee_count`

```bash
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8
python -m jobfinder.cli status
```

---

## 後續(依優先序)

### 1. 跑 3–5 次真實執行,然後重看分數分布

```bash
Start-ScheduledTask -TaskName "JobFinder Daily"
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8
python -m jobfinder.cli status
```

要看三個數字:
- `runs.jobs_filtered_out` / `jobs_new` —— **兩者相等代表過濾條件太窄或 104 欄位變了**,日報會靜默歸零
- 每天實際推幾則 —— `top_n: 5` 是上限,量體推估 4.5 筆/天,所以多數日子會推 2–4 則。
  連續好幾天 0 則就要往下調 `top_n_floor` 或放寬 `min_employee_count`
- `requests_used` —— 應該仍在 38 以下

```sql
-- 分數分布
SELECT (total_score/10)*10 AS bucket, COUNT(*) FROM scores
WHERE stage='deep' AND created_at > '2026-09-23' GROUP BY bucket;
```

### 2. 量體不足時的兩個旋鈕(先跑再調,不要預先動)

| 症狀 | 調法 | 代價 |
|---|---|---|
| 每天 < 2 筆 | `targeting.min_employee_count` 500 → 300 | 會混進中型系統商 |
| 每天 < 2 筆 | 刪掉 `industry_prefixes` 的 `1001003`(電腦及消費性電子)反而更聚焦;或加 `1001002` 電信 | 前者更少後者更多 |
| 好缺被 60 分下限擋掉 | `top_n_floor` 60 → 50 | 職缺荒的日子會推出低分卡片 |
| 想提高召回 | `max_pages_per_keyword` 2 → 3 | **總導覽數會變 30 + 詳細頁,硬上限是 40**,要同步把 `max_details_per_run` 降到 10,且完全沒有餘裕。handoff 硬限制 #2:預算用盡會靜默砍掉後面的關鍵字 |

### 3. 仍未做的 handoff 項目

- **P0 repo 轉公開**(轉之前必須驗 commit 歷史沒有 `.env`/金鑰)、README 補真實數字與架構圖
- **P1** 其餘規則:排除輪班/on-call/夜班、排除職稱含實習/工讀;記錄「達標但被每日上限丟掉」的職缺
- **P2** 「想不想去」閘門(自有產品 vs SI)、必備條件命中率、人工標註 30–50 筆算 precision/recall
- **P3** Docker、SQLite→PostgreSQL 遷移、CI 只跑測試不部署

---

## 這次定案、別改回去的決策

1. **過濾放程式端不放 104 的搜尋參數。** 搜尋請求數 = 關鍵字 × 頁數,與產業參數無關 —— 在 104 端過濾省不到預算,卻要為沒實測過的 `indcat` 多打一次 104。
2. **過濾跑在粗篩之前。** 不符合的職缺不該花 LLM token,更不該吃掉每次 18 個詳細頁名額。
3. **訊號消失時 fail-open。** `employeeCount` 哪天消失,fail-closed 會讓日報靜默變 0 則,跟「今天沒新職缺」無法分辨。
4. **`industry_prefixes` 只能填數字代碼。** 填中文名稱不報錯、只會默默濾光,所以 `config.py` 直接拒絕啟動。
5. **不驗證 `indcat`。** 規則層先上線看效果,召回不足時再考慮。
6. **`mode: top_n` 不要改回 `threshold`。** 深評分數雜訊 ±8,任何卡在雜訊帶的絕對門檻都會讓最好的職缺隨機消失,而且沒有任何跡象。
7. **不用 `jobCat` 過濾。** 已實測失敗,理由見上一節。
8. **職稱含 AI 但在半導體大廠的職缺照留。** 那些是大廠的 AI 平台/智慧製造職缺,實際做的是資料管線 —— 跟原本抱怨的「軟體小公司 AI 工程師」是兩回事(2026-09-23 使用者確認)。
