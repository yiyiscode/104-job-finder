# 履歷用量化數據 —— 104 職缺自動化推薦系統

> 資料截至 **2026-10-09**(`local_data/jobs.db` 最後更新 10-09 19:29、`local_data/daily.log` 10-09 19:35)。
> 期間:**2026-08-22 ～ 2026-10-08**,只計**真的連線 104 的執行**(`runs.requests_used > 0`),
> 排除 11 次離線重播/測試。之後要更新數字,照「重算方式」重跑即可。

---

## 履歷條列(中文)

- **獨立開發 104 人力銀行職缺自動化推薦系統**:每日定時抓取、篩選、以 LLM 評分後推播到 Telegram。
  至今穩定運行 **35 次**,累計處理 **2,313 筆不重複職缺**(來自 **1,329 家公司**)。
- **爬蟲**:每次約 **104 秒**抓取約 **350 筆**職缺,平均只發出 **29 次請求**。
  設有節流、請求預算上限與封鎖偵測自動熔斷,確保合規、低負載。
- **兩段式篩選**:先以產業/規模規則過濾,再用 LLM 批次粗篩與逐筆深評。
  **2,300 筆新職缺中篩掉 92%,只推薦 184 筆(8%)**;規則層加粗篩只需約 **6 秒**。
- **效率**:將每天約 **1 小時**的人工搜尋與篩選,縮短為約 **4 分鐘**的全自動流程。
  35 次累計約省下 **35 小時**,LLM 費用全期僅 **US$1.28**。

### 可選第 5 條

- **將評分模型從 Gemini 遷移到 Claude**:先用 10 筆實測額度與穩定度,再重評 **408 筆**歷史職缺,
  評分一致性提升,並依實際標記重新校準推薦門檻。

---

## 寫進履歷前要知道的事

| 數字 | 注意 |
|---|---|
| 每天 1 小時 | **使用者自評的手動基準**,不是量測值。面試被問就照實說是自己估計的 |
| 約 4 分鐘 | 全流程**中位數**(234 秒),最長約 10 分鐘;含刻意的節流等待(保護 104 端,不是效能不足) |
| 篩掉 92% | **混合兩版規則**。09-23 加上產業/規模過濾後是 **96%**(1,093 → 44);想強調現行系統就用 96% 並註明「09-23 改版後」 |
| 省下 35 小時 | 35 次 × 1 小時,**沒扣掉看日報的時間**;嚴謹寫法:「節省約 30 小時以上」 |
| 一致性提升(第 5 條) | Gemini 的 ±8 是「整批最好那則一次 80、一次 88」,Claude 的 4.5 是 10 筆兩次的平均差 —— 量法不同,**不建議寫成數字對比** |

---

## 數據與出處

| 指標 | 數值 | 依據 |
|---|---|---|
| 運行期間 | 08-22 ～ 10-08,**35 次**實際抓取 | `runs` 表(`status IN ('success','partial') AND requests_used > 0`) |
| 每次抓到職缺(跨關鍵字去重後) | 中位數 **352 筆**;35 次合計 12,103 筆次 | `daily.log`「抓到 X 筆」(`pipeline.py` `merge_summaries` 之後才 log) |
| 累計不重複職缺 | **2,313 筆**、**1,329 家公司** | `jobs` 表 `count(*)`、`count(distinct cust_no)` |
| 搜尋階段耗時 | 中位數 **104 秒** | `daily.log`:第 1 個請求 → 「抓到」那行 |
| 每次對 104 的請求數 | 平均 **28.8 次**(硬上限 40) | `runs.requests_used`、`config.yaml` `max_navigations_per_run` |
| 節流設定 | 分頁間 3–5 秒、關鍵字間 5–9 秒、詳細頁間 4–8 秒 | `config.yaml` `*_delay_range` |
| 全流程耗時 | 中位數 **234 秒**、平均 276 秒、最長 611 秒 | `runs.started_at` → `finished_at` |
| 規則層 + 粗篩耗時 | 中位數 **6 秒** | `daily.log`:「抓到」→「粗篩:」 |
| 全期漏斗 | 新職缺 **2,300** → 深評 674 → 推播 **184(8%)** | `runs` 的 `jobs_new` / `jobs_deep_scored` / `jobs_notified` 加總 |
| 09-23 後漏斗(13 次) | 新職缺 **1,093** → 規則層濾掉 **728(67%)** → 深評 146 → 推播 **44(4%)** | 同上,`started_at >= '2026-09-23'` |
| LLM 實付成本 | 全期 **US$1.28**(OpenRouter);10-06 起改用 Claude 訂閱,不另計費 | `runs.llm_cost_usd` 加總 |
| Claude 重評 | 408 筆有全文的職缺 | `scores` 中 `model LIKE 'claude-cli:%'` 且 `stage='deep'` |

**注意 `runs.jobs_fetched`(合計 16,837)不能用** —— 它是跨關鍵字去重**之前**的數量(`pipeline.py`
`report.jobs_fetched = len(batch.jobs)`),同一職缺被多個關鍵字撈到會重複算。

**量不到的**:深評逐筆耗時(那幾行 log 沒有時間戳)。Claude 實測每筆約 25–53 秒(`scripts/claude_probe.py`)。

---

## 重算方式

```python
# 以唯讀模式讀 jobs.db
import sqlite3
c = sqlite3.connect("file:local_data/jobs.db?mode=ro", uri=True)
live = "FROM runs WHERE status IN ('success','partial') AND requests_used > 0"
c.execute(f"SELECT count(*), sum(jobs_new), sum(jobs_filtered_out), sum(jobs_deep_scored),"
          f" sum(jobs_notified), avg(requests_used), sum(llm_cost_usd) {live}").fetchone()
c.execute("SELECT count(*), count(DISTINCT cust_no) FROM jobs").fetchone()
```

搜尋階段與粗篩耗時要解析 `daily.log`:以「排程觸發」切段,取 `request [1/`、`抓到 X 筆`、`粗篩:` 三行的時間戳相減。
