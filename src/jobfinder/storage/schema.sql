-- SQLite schema。
-- 注意:journal_mode 刻意用 DELETE 不用 WAL —— WAL 的 -shm 檔用 mmap,
-- 而我們會把整個 db 檔在 Modal Volume 與本地之間搬來搬去,WAL 會壞掉。

PRAGMA foreign_keys = ON;

-- 職缺主檔與去重來源
CREATE TABLE IF NOT EXISTS jobs (
    job_no           TEXT PRIMARY KEY,
    job_name         TEXT NOT NULL,
    cust_name        TEXT NOT NULL,
    cust_no          TEXT,
    job_url          TEXT NOT NULL,
    area_desc        TEXT,
    salary_desc      TEXT,
    salary_low       INTEGER,
    salary_high      INTEGER,
    period_desc      TEXT,
    edu_desc         TEXT,
    appear_date      TEXT,                       -- 104 給的 YYYYMMDD
    first_seen_at    TEXT NOT NULL,              -- ISO8601
    last_seen_at     TEXT NOT NULL,
    last_new_at      TEXT NOT NULL,              -- 上次「被判定為新職缺」的時間;重新上架冷卻期以此為基準
    seen_count       INTEGER NOT NULL DEFAULT 1,
    content_hash     TEXT,                       -- 分辨「內容真的改了」與「雇主只是刷新上架日」
    matched_keywords TEXT,                       -- JSON array
    raw_summary      TEXT,                       -- 原始列表 JSON,供 --replay 與改版 diff
    raw_detail       TEXT,                       -- 原始詳細 JSON(粗篩通過者才有)
    status           TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new', 'screened_out', 'scored', 'notified'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_cust       ON jobs (cust_no);

-- 每次執行的稽核紀錄
CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL DEFAULT 'success'
                       CHECK (status IN ('success', 'partial', 'blocked', 'failed', 'skipped')),
    jobs_fetched       INTEGER NOT NULL DEFAULT 0,
    jobs_new           INTEGER NOT NULL DEFAULT 0,
    jobs_screened_in   INTEGER NOT NULL DEFAULT 0,
    jobs_deep_scored   INTEGER NOT NULL DEFAULT 0,
    jobs_notified      INTEGER NOT NULL DEFAULT 0,
    requests_used      INTEGER NOT NULL DEFAULT 0,   -- 稽核:這個數字失控代表護欄有漏洞
    llm_cost_usd       REAL    NOT NULL DEFAULT 0.0,
    schema_drift_count INTEGER NOT NULL DEFAULT 0,
    error_kind         TEXT,
    error_detail       TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at);

-- 每次評分的結果(粗篩與深評都存,方便回頭檢視模型當初怎麼想的)
CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no        TEXT NOT NULL REFERENCES jobs (job_no) ON DELETE CASCADE,
    run_id        INTEGER REFERENCES runs (id),
    stage         TEXT NOT NULL CHECK (stage IN ('screen', 'deep')),
    model         TEXT NOT NULL,
    total_score   INTEGER,
    tech_fit      INTEGER,
    exp_fit       INTEGER,
    domain_fit    INTEGER,
    growth_fit    INTEGER,
    practical_fit INTEGER,
    verdict       TEXT,
    one_liner     TEXT,
    highlights    TEXT,   -- JSON array
    red_flags     TEXT,   -- JSON array
    resume_tip    TEXT,
    adjustments   TEXT,   -- JSON array:程式端套用的修正(算術幻覺、verdict 降級、年資 hard rule)
    raw_response  TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scores_job ON scores (job_no, stage);
CREATE INDEX IF NOT EXISTS idx_scores_run ON scores (run_id);

-- 熔斷器。單列表(id 恆為 1)。見 SPEC.md 規則 2。
CREATE TABLE IF NOT EXISTS circuit_state (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    state                TEXT NOT NULL DEFAULT 'closed' CHECK (state IN ('closed', 'open')),
    tripped_at           TEXT,
    reason               TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO circuit_state (id, state, reason, consecutive_failures)
VALUES (1, 'closed', '', 0);
