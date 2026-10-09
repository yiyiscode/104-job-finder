# 職缺技能需求分析報告

**資料來源**：`local_data/jobs.db` → `jobs` 表（唯讀，只跑 SELECT）
**產出日期**：2026-09-04
**母體**：209 筆有 `raw_detail`（完整 JD＋條件）的職缺
**分群鍵**：`raw_detail.data.jobDetail.jobCategory[0].description`（104 官方職類）
**技能抽取**：預定義技能詞典（116 項）＋ 同義詞正規化，per-job 去重計數
**技能文字欄位**：`condition.other` ＋ `jobDetail.jobDescription` ＋ `condition.skill[].description` ＋ `condition.specialty[].description`

---

## 1. 母體與職類分佈

`jobs` 表共 1031 筆，其中只有 **209 筆**有詳細頁（粗篩通過才會抓 detail）。其餘 822 筆只有中位數約 123 字的短描述，技能訊號不足，已排除。

| 職類（primary jobCategory） | 職缺數 |
|---|---:|
| AI工程師 | 81 |
| 軟體工程師 | 60 |
| 演算法工程師 | 17 |
| 資料科學家 | 7 |
| 資料工程師 | 6 |
| 全端工程師 | 4 |
| 後端工程師 | 4 |
| 數據分析師／資料分析師 | 3 |
| 其他 22 個單/雙筆職類 | 27（併為「其他（長尾）」）|

---

## 2. 全體技能需求排行（母體 209 筆）

| # | 技能 | 職缺數 | 佔比 |
|--:|---|--:|--:|
| 1 | Python | 178 | 85.2% |
| 2 | LLM | 117 | 56.0% |
| 3 | Machine Learning | 91 | 43.5% |
| 4 | AI Agent | 68 | 32.5% |
| 5 | Deep Learning | 66 | 31.6% |
| 5 | Git | 66 | 31.6% |
| 7 | RAG | 59 | 28.2% |
| 7 | PyTorch | 59 | 28.2% |
| 9 | SQL | 57 | 27.3% |
| 10 | Linux | 56 | 26.8% |
| 11 | C++ | 55 | 26.3% |
| 12 | Docker | 53 | 25.4% |
| 13 | Computer Vision | 49 | 23.4% |
| 14 | OpenAI / GPT | 48 | 23.0% |
| 15 | Claude | 46 | 22.0% |
| 16 | Prompt Engineering | 45 | 21.5% |
| 17 | TensorFlow | 41 | 19.6% |
| 18 | JavaScript | 36 | 17.2% |
| 18 | REST API | 36 | 17.2% |
| 20 | Vector Database | 35 | 16.7% |
| 20 | NLP | 35 | 16.7% |
| 22 | PostgreSQL | 33 | 15.8% |
| 23 | C# | 32 | 15.3% |
| 24 | LangChain | 31 | 14.8% |
| 25 | Gemini | 26 | 12.4% |
| 25 | CI/CD | 26 | 12.4% |
| 27 | Fine-tuning | 24 | 11.5% |
| 27 | FastAPI | 24 | 11.5% |
| 29 | Model Deployment | 23 | 11.0% |
| 30 | Azure | 22 | 10.5% |
| 31 | MySQL | 21 | 10.0% |
| 31 | GCP | 21 | 10.0% |
| 33 | AWS | 20 | 9.6% |
| 33 | Edge AI / Embedded | 20 | 9.6% |
| 33 | ETL / Data Pipeline | 20 | 9.6% |
| 36 | Kubernetes | 19 | 9.1% |
| 36 | GPU | 19 | 9.1% |
| 38 | React | 18 | 8.6% |
| 38 | Node.js | 18 | 8.6% |

（完整 116 項見 `skill_overall.csv`）

---

## 3. 分職類技能排行

### AI工程師（81 筆）
Python 85.2% · LLM 65.4% · AI Agent 38.3% · PyTorch 35.8% · Machine Learning 33.3% · Deep Learning 32.1% · Prompt Engineering 29.6% · RAG 29.6% · SQL 29.6% · OpenAI/GPT 23.5% · Git 23.5% · Computer Vision 22.2% · NLP 22.2% · Claude 21.0% · Docker 19.8%

### 軟體工程師（60 筆）
Python 86.7% · LLM 53.3% · Machine Learning 46.7% · **C++ 43.3%** · Git 33.3% · Docker 31.7% · Computer Vision 30.0% · RAG 28.3% · AI Agent 28.3% · Linux 28.3% · Deep Learning 26.7% · OpenAI/GPT 25.0% · Claude 23.3% · LangChain 23.3% · SQL 21.7%

### 演算法工程師（17 筆）
**Python 94.1% · Machine Learning 82.4% · Deep Learning 70.6%** · TensorFlow 47.1% · Computer Vision 47.1% · Linux 41.2% · PyTorch 41.2% · C++ 41.2% · Git 35.3% · Statistics 29.4% · GCP 23.5% · MLOps 23.5% · ETL/Data Pipeline 23.5%

### 資料工程師（6 筆）
**Python 100% · SQL 83.3%** · LLM 66.7% · ETL/Data Pipeline 66.7% · AI Agent 66.7% · Vector Database 50% · PostgreSQL 50% · Machine Learning 50% · Git 50%

### 後端工程師（4 筆）
**REST API 100% · Docker 100% · Git 100%** · LLM 75% · PostgreSQL 75% · TypeScript 75% · Linux 75% · JavaScript 75% · Python 75% · Claude 75% · OpenAI/GPT 75%

### 全端工程師（4 筆）
**LLM 100%** · Docker 75% · Git 75% · Computer Vision 75% · Python 75% · Scala 50% · Microservices 50% · Kubernetes 50% · CI/CD 50% · Node.js 50%

### 資料科學家（7 筆）
Python 85.7% · Machine Learning 71.4% · LLM 57.1% · SQL 42.9% · TensorFlow 42.9% · Deep Learning 42.9% · PyTorch 42.9%

（完整資料見 `skill_by_category.csv`）

---

## 4. 觀察

1. **Python 是絕對門檻**（85%），演算法／資料工程師職類幾乎 100%。
2. **GenAI 技能已成主流需求**：LLM 56%、AI Agent 33%、RAG 28%、Prompt Engineering 22%、Vector Database 17%、LangChain 15%。這些在「AI工程師」職類佔比更高（LLM 65%、AI Agent 38%）。
3. **具體 LLM 供應商點名**：OpenAI/GPT 23%、Claude 22%、Gemini 12% —— 多出現在 JD 的「GPT、Claude、Llama 等」列舉句。
4. **職類差異明顯**：
   - 軟體工程師 → C++ 權重高（43%），偏系統／效能。
   - 演算法工程師 → ML/DL 理論 + TensorFlow/CV，偏研究。
   - 後端／全端 → Docker、Git、REST API、CI/CD 等工程實務近 100%。
   - 資料工程師 → SQL、ETL、Vector DB。
5. **MLOps／雲端是次階需求**：Docker 25%、CI/CD 12%、Kubernetes 9%、三大雲各 ~10%。

---

## 5. 方法侷限（重要）

- **母體有選擇偏差**：209 筆全是通過本專案粗篩（AI 工程師取向、年資門檻）的職缺，非 104 全站隨機樣本。長尾職類樣本數 ≤7，百分比僅供參考。
- **詞典法會漏詞典外技能**：116 項詞典外的技能不會被計入。詞典中 8 項零命中：Ansible、Kubeflow、NLTK、RLHF、Ray、Rust、Terraform、spaCy。
- **關鍵字比對可能有偽陽/偽陰**：短代碼（Go/R/C/JS/ML/REST 等）已加上下文限制，但無法 100% 排除。`skill_per_job.csv` 可逐筆檢視命中結果。
- **列舉句放大效應**：JD 常寫「GPT、Claude、Llama、Gemini 等」，這類點名會讓具體模型名的佔比偏高，不代表該職缺「必須用過」該模型。

---

## 產出檔案（皆在 scratchpad，未動 repo）

| 檔案 | 內容 |
|---|---|
| `skill_demand.py` | 分析腳本（argparse，唯讀 DB） |
| `skill_overall.csv` | 全體 116 項技能次數＋佔比 |
| `skill_by_category.csv` | 分職類技能次數＋類內佔比 |
| `skill_per_job.csv` | 每筆職缺命中的技能清單（供稽核） |
