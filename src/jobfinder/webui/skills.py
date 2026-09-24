"""技能詞典 + 同義詞正規化。純字串比對:零成本、可重現、可稽核,不用 LLM。

詞典原本在 docs/skill-demand/skill_demand.py(2026-09-04,AI 主敘事時期),
搬進來時補上資料工程/BI 工具,並把 Tableau / Power BI 從「Data Visualization」拆出來。
高誤判風險的短代碼(go/js/r/ml/rag/rest…)都加了上下文限制。

計數一律 per-job 去重:一個職缺同一個技能只算 1 次。
"""

from __future__ import annotations

import re
from typing import Any

SKILLS: dict[str, str] = {
    # 程式語言
    "Python": r"python",
    "Java": r"\bjava\b(?!\s*script)",
    "C++": r"c\+\+|cpp",
    "C#": r"c#|c\s?sharp|csharp",
    "C": r"\bc\s*(?:語言|/c\+\+|、c\+\+)|\(c\)|\bc\b(?=\s*語言)",
    "Go": r"golang|go\s*語言|go/|/go\b|\bgo\b(?=\s*lang)",
    "JavaScript": r"javascript|\bjs\b",
    "TypeScript": r"typescript",
    "SQL": r"\bsql\b",
    "R": r"\br\s*語言|\br程式|(?<![a-z])r\b(?=\s*/\s*python)|python\s*/\s*r\b",
    "Scala": r"scala",
    "Rust": r"\brust\b",
    "MATLAB": r"matlab",
    "Kotlin": r"kotlin",
    "Swift": r"\bswift\b",
    "PHP": r"\bphp\b",
    "Shell/Bash": r"\bbash\b|shell script|shell 腳本|\bshell\b",
    "Perl": r"\bperl\b",
    # ML / DL 框架與函式庫
    "PyTorch": r"pytorch|py\s?torch",
    "TensorFlow": r"tensorflow|tensor\s?flow",
    "Keras": r"keras",
    "JAX": r"\bjax\b",
    "scikit-learn": r"scikit-?learn|sklearn",
    "XGBoost": r"xgboost",
    "LightGBM": r"lightgbm",
    "ONNX": r"onnx",
    "OpenCV": r"opencv|open\s?cv",
    "Hugging Face": r"hugging\s?face|huggingface",
    "spaCy": r"spacy",
    "NLTK": r"nltk",
    "Pandas": r"pandas",
    "NumPy": r"numpy",
    # LLM / GenAI
    "LLM": r"\bllm\b|\bllms\b|大型語言模型|大語言模型",
    "RAG": r"\brag\b|retrieval[- ]augmented|檢索增強",
    "LangChain": r"langchain|lang\s?chain",
    "LlamaIndex": r"llama\s?index|llamaindex",
    "Vector Database": r"vector\s*(?:database|db|store)|向量資料庫|向量檢索|"
    r"pinecone|weaviate|milvus|faiss|chroma|qdrant|pgvector",
    "Prompt Engineering": r"prompt\s*engineering|prompt\s*工程|提示工程|提示詞工程",
    "Fine-tuning": r"fine[- ]?tun|finetun|微調模型|模型微調",
    "LoRA": r"\blora\b|qlora",
    "RLHF": r"rlhf",
    "AI Agent": r"ai\s*agent|agent\s*框架|multi-?agent|agentic|智能體|代理人框架",
    "OpenAI / GPT": r"openai|chatgpt|gpt-?4|gpt-?3|\bgpt\b",
    "Claude": r"\bclaude\b|anthropic",
    "Gemini": r"\bgemini\b",
    "Llama": r"\bllama\b(?!\s?index)",
    "Ollama": r"ollama",
    "vLLM": r"vllm",
    "Stable Diffusion": r"stable\s?diffusion",
    "Diffusion Model": r"diffusion\s*model|擴散模型",
    "Multimodal": r"multi-?modal|多模態|多模型態",
    # NLP / CV 任務
    "NLP": r"\bnlp\b|自然語言處理|自然語言理解",
    "Computer Vision": r"computer\s*vision|電腦視覺|機器視覺|影像辨識|影像處理|圖像辨識|视觉",
    "OCR": r"\bocr\b|光學字元|文字辨識",
    "YOLO": r"\byolo\b",
    "Transformer": r"transformer",
    "BERT": r"\bbert\b",
    "Speech / ASR": r"\basr\b|speech\s*recognition|語音辨識|聲學模型",
    "TTS": r"\btts\b|text[- ]to[- ]speech|語音合成",
    "Object Detection": r"object\s*detection|物件偵測|目標檢測",
    "Segmentation": r"segmentation|影像分割|語意分割",
    "Recommendation System": r"recommend(?:er|ation)\s*system|推薦系統|推薦演算法",
    "Time Series": r"time[- ]series|時間序列|時序預測",
    "Reinforcement Learning": r"reinforcement\s*learning|強化學習",
    "Knowledge Graph": r"knowledge\s*graph|知識圖譜",
    # MLOps / Infra / DevOps
    "Docker": r"docker|容器化",
    "Kubernetes": r"kubernetes|k8s|k3s",
    "MLflow": r"mlflow",
    "Kubeflow": r"kubeflow",
    "Airflow": r"airflow",
    "Kafka": r"kafka",
    "Spark": r"\bspark\b|pyspark",
    "Hadoop": r"hadoop",
    "Ray": r"\bray\b(?=\s*(?:框架|cluster|serve|tune|\.io))|ray\.io",
    "CI/CD": r"ci\s*/\s*cd|\bci/cd\b|cicd|持續整合|持續部署",
    "Jenkins": r"jenkins",
    "GitHub Actions": r"github\s*actions",
    "GitLab CI": r"gitlab\s*ci",
    "Terraform": r"terraform",
    "Ansible": r"ansible",
    "Prometheus": r"prometheus",
    "Grafana": r"grafana",
    "Linux": r"\blinux\b|ubuntu|centos",
    "Git": r"\bgit\b(?!hub|lab)|版本控制",
    # 雲端
    "AWS": r"\baws\b|amazon\s*web\s*services|sagemaker|\bec2\b|\bs3\b|lambda",
    "GCP": r"\bgcp\b|google\s*cloud|vertex\s*ai|bigquery",
    "Azure": r"\bazure\b",
    "Databricks": r"databricks",
    # Web / API / 後端
    "FastAPI": r"fastapi|fast\s?api",
    "Flask": r"flask",
    "Django": r"django",
    "Node.js": r"node\.?js|nodejs",
    "Spring / Spring Boot": r"spring\s*boot|springboot|spring\s*framework|\bspring\b",
    ".NET": r"\.net\b|dotnet|asp\.net",
    "REST API": r"restful|rest\s*api|\brest\b",
    "GraphQL": r"graphql",
    "gRPC": r"grpc",
    "Microservices": r"microservice|微服務",
    "Redis": r"redis",
    "PostgreSQL": r"postgres|postgresql",
    "MySQL": r"mysql|mariadb",
    "MongoDB": r"mongodb|mongo\b",
    "Elasticsearch": r"elasticsearch|elastic\s*search|\belk\b",
    "Snowflake": r"snowflake",
    "ETL / Data Pipeline": r"\betl\b|data\s*pipeline|資料管線|資料流程|數據管道",
    "Data Warehouse": r"data\s*warehouse|資料倉儲|數據倉庫",
    # 前端
    "React": r"react(?:\.js|js)?\b",
    "Vue": r"vue(?:\.js|js)?\b",
    "Angular": r"angular",
    # GPU / 邊緣 / 硬體
    "CUDA": r"\bcuda\b",
    "TensorRT": r"tensorrt",
    "GPU": r"\bgpu\b|顯示卡運算|gpu 加速",
    "Edge AI / Embedded": r"edge\s*ai|edge\s*computing|邊緣運算|embedded\s*system|嵌入式",
    "FPGA": r"\bfpga\b",
    # 概念 / 方法
    "Machine Learning": r"machine\s*learning|機器學習|\bml\b",
    "Deep Learning": r"deep\s*learning|深度學習|類神經網路|神經網路",
    "Statistics": r"statistics|統計學|統計分析|統計方法",
    "Data Mining": r"data\s*mining|資料探勘|數據挖掘",
    "A/B Testing": r"a/b\s*test|ab\s*test|a/b 測試",
    "Feature Engineering": r"feature\s*engineering|特徵工程",
    "Model Deployment": r"model\s*deployment|模型部署|模型佈署|模型上線",
    "MLOps": r"mlops",
    "Data Visualization": r"data\s*visuali|資料視覺化|數據視覺化",
    # 資料工程 / BI(2026-09-24 補:主敘事改為資料工程後,舊詞典幾乎沒有這一塊)
    "Tableau": r"tableau",
    "Power BI": r"power\s?bi\b|\bpowerbi\b",
    "Looker / Superset": r"looker|superset|metabase",
    "Excel / VBA": r"\bexcel\b|\bvba\b",
    "dbt": r"\bdbt\b",
    "Hive": r"\bhive\b",
    "Trino / Presto": r"trino|presto",
    "Flink": r"\bflink\b",
    "Iceberg / Delta Lake": r"iceberg|delta\s*lake|\bhudi\b",
    "Redshift": r"redshift",
    "BigQuery": r"bigquery|big\s*query",
    "Oracle": r"\boracle\b",
    "SQL Server": r"sql\s*server|\bmssql\b|\bt-?sql\b",
    "ETL 工具": r"\bssis\b|informatica|\bnifi\b|airbyte|talend",
    "NoSQL": r"\bnosql\b",
    "Data Modeling": r"data\s*model(?:ing|ling)|資料模型|數據模型|維度模型|star\s*schema|星狀",
    "Data Governance": r"data\s*governance|資料治理|數據治理|data\s*quality|資料品質|數據品質",
}

COMPILED = {name: re.compile(pat, re.IGNORECASE) for name, pat in SKILLS.items()}


def match_skills(text: str | None) -> set[str]:
    if not text:
        return set()
    return {name for name, rx in COMPILED.items() if rx.search(text)}


def detail_skill_text(detail: dict[str, Any]) -> str:
    """全文層的文字來源:JD + 其他條件 + 擅長工具 + 工作技能。``detail`` 是 ``data`` 那一層。"""
    jd = detail.get("jobDetail") or {}
    cond = detail.get("condition") or {}
    parts = [
        cond.get("other") or "",
        jd.get("jobDescription") or "",
        " ".join(x.get("description", "") for x in (cond.get("skill") or [])),
        " ".join(x.get("description", "") for x in (cond.get("specialty") or [])),
    ]
    return "\n".join(parts)


def summary_skill_text(summary: dict[str, Any]) -> str:
    """摘要層的文字來源:職稱 + 列表摘要(中位數約 123 字)+ 電腦技能欄。"""
    parts = [
        summary.get("jobName") or "",
        summary.get("description") or "",
        _flatten(summary.get("pcSkills")),
    ]
    return " ".join(parts)


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    return "" if value is None else str(value)
