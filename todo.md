交付內容
文件(你明確要求的兩份)

CLAUDE.md — 專案上下文:七條「絕對禁止」、架構、指令、已知地雷、現況與下一步
SPEC.md — 詳細規格:功能需求表、資料來源、去重規則、評分規格、十條硬性防封鎖限制、合規聲明、驗收標準、風險備案
README.md — 安裝與啟用步驟
程式 — 4,447 行 / 34 個模組,2,014 行測試

你最在意的三件事,實際是怎麼落實的
① 絕不登入 104

config.yaml、.env.example、Modal Secret 都沒有帳密欄位,而且 config.py 會掃描整份設定,出現 username/password/token 之類的鍵就拒絕啟動
每次執行 warmup 後、進搜尋頁前跑 assert_anonymous(),發現 104_session/ARJ/含 token、member 的 104 cookie 就中止整趟並告警
profile 存檔用白名單(只留 cf_clearance/__cf_bm),身分 cookie 連存都不存;Chrome 的二進位 Cookies 檔乾脆完全不打包
bootstrap_profile.py 開頭警告不要登入,結束前自檢,偵測到就刪掉 profile 要求重做
② 降到最低的被鎖機率

封鎖訊號(403/429/503、Just a moment...、Turnstile、1020)→ 零重試,一路拋到頂
熔斷器:被擋一次冷卻 24h → 第二次 72h → 第三次要手動 reset-circuit。熔斷期間連瀏覽器都不開
RequestBudget 是唯一的導覽入口:節流(3–5s 帶 jitter)與計數不可能被繞過;總導覽數硬上限 40、全程序列無併發
Modal retries=0 + max_containers=1
--headful 不帶 --limit 直接拒絕執行;probe_api.py 已有 fixture 就拒絕跑
緊急剎車 JOBFINDER_SCRAPE_DISABLED=1
護欄有 61 個專屬測試 —— 護欄沒有測試等於沒有護欄。

順帶修正了原始需求的三個錯誤
jobexp=3 其實是「1~3年」不是「3年以下」(已改 1,3)、order=15 是符合度排序不是日期(已改用 isnew)、area=6001006000 是新竹縣市合併。

已驗證 vs 未驗證
跑通的:離線端到端、去重六情境、HTML 跳脫與 400 降級、年資 hard rule(資深架構師從 96 分掉到 48)、--replay、Modal API 簽名(Cron 的 timezone、max_containers 都確認存在)。

沒驗證的:104 的真實欄位。 fixture 目前是依調查結果手工建的合成資料,normalize.py 的欄位對應還是假設。

過程中抓到兩個真 bug:Database 在倉庫端沒檔案時會沿用 /tmp 舊 DB(暖容器會復活已刪掉的熔斷狀態);--replay 被去重擋掉導致深評 0 筆,等於整個旗標作廢。都已修並補上回歸測試。

你的下一步

uv venv --python 3.12 && uv pip install -e ".[dev]"
patchright install chromium

python scripts/send_test_message.py      # 先確認告警管道通(被擋時就靠它)
python scripts/check_models.py           # 驗證 OpenRouter model id 真的存在
python scripts/bootstrap_profile.py      # 建乾淨 profile ← 不要登入
python scripts/probe_api.py              # 錄真實 JSON + 欄位對照報告
config.yaml 裡的兩個 model id 是我填的占位值,請務必跑 check_models.py 驗證 —— 我不憑記憶保證那兩個 id 現在還在。

最後才是 modal run modal_app.py::daily_run。那一刻決定 Modal 的美國資料中心 IP 能不能過 104 的 Cloudflare —— 本機成功不代表雲端會成功。過不了的話,最划算的是花 $5–15/月接台灣住宅代理,只要填 config.yaml 的 scrape.proxy,程式一行不用改。