"""職缺技能需求分析（唯讀 jobs.db）。

口徑（見 memory/skill-demand-analysis.md）:
- 母體：jobs 表中有 raw_detail 的 209 筆
- 分群：raw_detail.data.jobDetail.jobCategory[0].description（104 官方職類）
- 技能：預定義詞典 + 同義詞正規化，per-job 去重計數
- 技能文字來源：condition.other + jobDetail.jobDescription
                 + condition.skill[].description + condition.specialty[].description
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── 技能詞典：canonical -> regex pattern（已對 lower-case 文字比對）─────────────
# 高誤判風險的短代碼（go/js/ts/r/cv/ml/rag/rest…）都加了上下文限制。
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
    "Vector Database": r"vector\s*(?:database|db|store)|向量資料庫|向量檢索|pinecone|weaviate|milvus|faiss|chroma|qdrant|pgvector",
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
    "Data Visualization": r"data\s*visuali|資料視覺化|數據視覺化|tableau|power\s?bi|\bpowerbi\b",
}

COMPILED = {name: re.compile(pat, re.IGNORECASE) for name, pat in SKILLS.items()}

# 職類分群：職缺數 >= MIN_GROUP 的單獨列，其餘歸「其他（長尾）」
MIN_GROUP = 3


def skill_text(data: dict) -> str:
    jd = data.get("jobDetail") or {}
    cond = data.get("condition") or {}
    parts = [
        cond.get("other") or "",
        jd.get("jobDescription") or "",
        " ".join(x.get("description", "") for x in (cond.get("skill") or [])),
        " ".join(x.get("description", "") for x in (cond.get("specialty") or [])),
    ]
    return "\n".join(parts)


def match_skills(text: str) -> set[str]:
    return {name for name, rx in COMPILED.items() if rx.search(text)}


def main() -> None:
    ap = argparse.ArgumentParser(description="從 jobs.db 統計職缺技能需求次數（唯讀）。")
    ap.add_argument("--db", default="local_data/jobs.db", help="jobs.db 路徑")
    ap.add_argument("--out", default=None, help="輸出目錄（預設：此腳本所在目錄）")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT job_no, job_name, raw_detail FROM jobs "
        "WHERE raw_detail IS NOT NULL AND raw_detail != ''"
    ).fetchall()
    conn.close()

    N = len(rows)
    overall = collections.Counter()
    by_cat_skill: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    cat_jobcount = collections.Counter()
    per_job: list[tuple[str, str, str, list[str]]] = []

    for r in rows:
        d = json.loads(r["raw_detail"])
        data = d.get("data") or {}
        jd = data.get("jobDetail") or {}
        cats = jd.get("jobCategory") or []
        cat = cats[0].get("description", "（未分類）") if cats else "（未分類）"
        cat_jobcount[cat] += 1

        found = match_skills(skill_text(data))
        overall.update(found)
        by_cat_skill[cat].update(found)
        per_job.append((r["job_no"], r["job_name"], cat, sorted(found)))

    # 分群：大類單獨、小類併入「其他（長尾）」
    big = {c for c, n in cat_jobcount.items() if n >= MIN_GROUP}
    grouped_jobcount = collections.Counter()
    grouped_skill: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for c, n in cat_jobcount.items():
        key = c if c in big else "其他（長尾）"
        grouped_jobcount[key] += n
    for c, ctr in by_cat_skill.items():
        key = c if c in big else "其他（長尾）"
        grouped_skill[key].update(ctr)

    # ── 主控台輸出 ──────────────────────────────────────────────────
    print(f"母體：{N} 筆有 raw_detail 的職缺\n")
    print("=" * 60)
    print("職類分佈（primary jobCategory）")
    print("=" * 60)
    for c, n in cat_jobcount.most_common():
        mark = "" if c in big else "  → 併入「其他（長尾）」"
        print(f"{n:4d}  {c}{mark}")

    print("\n" + "=" * 60)
    print(f"全體技能需求排行（母體 {N} 筆，per-job 去重）")
    print("=" * 60)
    print(f"{'技能':<24}{'職缺數':>7}{'佔比':>9}")
    for name, cnt in overall.most_common():
        if cnt == 0:
            continue
        print(f"{name:<24}{cnt:>7}{cnt / N * 100:>8.1f}%")

    zero = sorted(n for n in SKILLS if overall[n] == 0)
    print(f"\n詞典中 0 命中（{len(zero)} 項）：{', '.join(zero)}")

    print("\n" + "=" * 60)
    print("分職類技能排行（每類 Top 15）")
    print("=" * 60)
    order = [c for c, _ in grouped_jobcount.most_common()]
    for c in order:
        gn = grouped_jobcount[c]
        print(f"\n── {c}（{gn} 筆）" + "─" * max(0, 40 - len(c)))
        for name, cnt in grouped_skill[c].most_common(15):
            print(f"   {name:<22}{cnt:>5}{cnt / gn * 100:>8.1f}%")

    # ── CSV ────────────────────────────────────────────────────────
    with (out_dir / "skill_overall.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["skill", "job_count", "pct_of_population"])
        for name, cnt in overall.most_common():
            w.writerow([name, cnt, f"{cnt / N * 100:.1f}"])

    with (out_dir / "skill_by_category.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["job_category", "category_job_count", "skill", "job_count", "pct_within_category"])
        for c in order:
            gn = grouped_jobcount[c]
            for name, cnt in grouped_skill[c].most_common():
                w.writerow([c, gn, name, cnt, f"{cnt / gn * 100:.1f}"])

    with (out_dir / "skill_per_job.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["job_no", "job_name", "job_category", "matched_skills"])
        for jn, nm, c, sk in per_job:
            w.writerow([jn, nm, c, "; ".join(sk)])

    print(f"\nCSV 已寫入：{out_dir}")
    print("  skill_overall.csv / skill_by_category.csv / skill_per_job.csv")


if __name__ == "__main__":
    main()
