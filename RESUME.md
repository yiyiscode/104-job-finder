# RESUME.md — 目前進度 / 下一步

> 說「讀 RESUME.md 繼續」即可接續。背景與更早的優化方向見 [docs/handoff-2026-09-23.md](docs/handoff-2026-09-23.md)。

**最後更新:2026-09-23**　分支 `feat/target-semiconductor-finance`,commit `88499e2`。281 個測試全過。

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

## 下一步(依優先序)

### 1. 跑 3–5 次真實執行,然後重看分數分布 ⬅ 最優先

```bash
Start-ScheduledTask -TaskName "JobFinder Daily"
Get-Content local_data\daily.log -Tail 40 -Encoding UTF8
python -m jobfinder.cli status
```

要看三個數字:
- `runs.jobs_filtered_out` / `jobs_new` —— **兩者相等代表過濾條件太窄或 104 欄位變了**,日報會靜默歸零
- `jobs_screened_in` 與深評分數分布 —— 過濾後 `max_per_day: 10` 不再是真正的篩子
  (推估約 4.5 筆/天),`threshold: 80` 回到主導地位,**很可能要往下調或改 `mode: top_n`**
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
