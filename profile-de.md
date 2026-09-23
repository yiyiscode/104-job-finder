# 林芳義 Fang-Yi Lin

az045317@gmail.com ｜ [github.com/yiyiscode](https://github.com/yiyiscode) ｜ 台中以北

## 定位

**流程自動化 / 系統整合工程師。** 統計本科出身，專長是把「靠人工做的重複流程」變成穩定會跑的自動化管線——排程、爬取外部資料源、清洗轉換、結構化回填、異常處理。目前有一套自建的排程系統每日在線上運行。同時具備 ML/LLM 建模能力，能與資料團隊對話並理解模型端的資料需求。

---

## 專案

### 104 職缺日報 — 從人工找工作到自動化管線 ｜ 上線運行中

- 每日 08:00 排程觸發，自動爬取 104 上符合條件的職缺，去重後比對前日快照，只輸出「今日新增」項目
- 以 LLM 對照個人履歷評分，過濾掉不值得投遞的職缺，將結果推送至 Telegram
- 完整資料流：外部來源擷取 → 去重與差異比對 → 評分過濾 → 結構化輸出 → 通知
- **技術**：Python、排程、爬蟲、LLM API、Telegram Bot
- **驗證**：面試中作為正式簡報主題呈現，獲面試官正面評價

### 國泰人壽 CAP 實習 — 資安大數據分析 ｜ 2025/02–2025/06

- 重構前處理與訓練 pipeline，**訓練效率提升 6 倍**
- 自建 BI 分析工具，支援特徵探索與模型迭代，縮短分析循環
- 建置 BERT Embedding + CNN 混合架構的惡意網址分類模型
- **技術**：Python、PyTorch、BERT、資料前處理管線、BI 工具開發

### i 郵箱點位分析 BI 平台 ｜ 2024 郵政大數據競賽 Top 15／100+ 隊（技術負責）
- 整合多來源地理與營運資料，建置互動式分析平台供選點決策使用
- 資料處理、模型訓練與前端呈現全端負責，部署於 AWS
- **技術**：Python、Streamlit、Folium、PyCaret、AWS

### Solerie 產學合作 — 金幣瑕疵自動檢測 ｜ 2024/02–2024/07
- 以 Arduino 介接硬體，建立**端到端自動化檢測流程**：影像擷取 → 偵測 → 裁切去背 → 瑕疵量化
- YOLO 做金幣偵測與裁切，SIFT + FLANN 特徵匹配搭配 Otsu 閾值計算瑕疵比例
- **技術**：Python、OpenCV、YOLO、Arduino 整合

---

## 建模與 ML 專案（輔）

| 專案 | 技術 | 成果 |
|---|---|---|
| 半導體晶圓缺陷分類 | Gradient Boosting、SHAP、AutoML | 極度不平衡資料（0.14% 正樣本／5,000 筆）；FNR 100%→0%；20 次 repeated stratified split 平均 Recall 0.90、AP 0.75 |
| RAG 資安文件問答系統 | FastAPI、LangChain、ChromaDB、PyMuPDF | 端到端 grounded answer；引用可追溯至 File/Section/Page/Chunk ID；證據不足時拒答 |
| 資安 LLM 領域微調 | Ollama、Transformers | 4 epoch domain fine-tuning；val loss 2.50→2.44；perplexity 11.56 |

---

## 技能

| 領域 | 內容 |
|---|---|
| **資料工程 / 自動化** | Python、SQL、爬蟲、排程、ETL 流程設計、API 串接、資料清洗與轉換、Pandas、NumPy |
| **後端 / 部署** | FastAPI、Pydantic、RESTful API、AWS（SageMaker、Bedrock）、GCP、Git |
| **BI / 視覺化** | Streamlit、Power BI、Tableau、Plotly、Folium |
| **統計 / ML** | Scikit-learn、XGBoost、PyCaret、SHAP、LIME、不平衡資料處理、threshold tuning、實驗設計 |
| **DL / LLM** | PyTorch、TensorFlow、CNN、BERT、LangChain、RAG、Prompt Engineering、Fine-tuning |
| **CV** | OpenCV、YOLO、SIFT/FLANN |
| **其他語言** | R、SAS、JavaScript、Excel VBA |

---

## 學歷

- **國立臺灣科技大學** 資訊管理研究所 M.S.　2023–2025（研究生優秀入學獎學金）
- **國立臺北大學** 統計學系 B.S.　2019–2022

## 兵役

**服役期滿，2026/03 退伍**　<!-- 請補上正確的入伍—退伍月份 -->

## 語言

TOEIC 745（2026/05）

---
