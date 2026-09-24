@echo off
REM  ⚠️ chcp 必須是第一件事,擺在任何中文之前。
REM
REM  cmd.exe 是用「當下的主控台字碼頁」去解析批次檔本身的位元組。工作排程器起來的
REM  主控台預設是 cp950,於是這個檔裡的 UTF-8 中文在**執行前**就被拆成無效位元組,
REM  寫進 log 的已經是壞掉的資料 —— 不是顯示問題,任何檢視器都救不回來。
REM  實測(2026-08-22):「排程觸發」的 e6 8e 92 e7 a8 8b ... 落地變成 3f 92 e7 3f ...
REM
REM  切成 65001 之後,批次檔的解析與輸出都走 UTF-8,連 %DATE% 的「週六」也一併正確。
REM  擺在最前面還有第二個理由:cmd 是用位元組位移在檔案裡定位的,中途換字碼頁有機會
REM  讓它跑掉。全檔只有一種字碼頁最安全。
chcp 65001 >nul
REM ============================================================
REM  104 每日職缺日報 — Windows 工作排程器的進入點
REM
REM  為什麼跑在本機而不是雲端:實測 Modal 的 AWS 資料中心 IP 會被 104 的
REM  Cloudflare 判定為高風險訪客(純 HTTP 拿到 cf-mitigated: challenge,
REM  改用真實瀏覽器更直接跳出 Turnstile 互動挑戰)。台灣住宅 IP 則暢通無阻。
REM  詳見 CLAUDE.md 開頭那段。
REM
REM  安裝排程:powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
REM  手動測試:scripts\run_daily.cmd
REM  查看紀錄:Get-Content local_data\daily.log -Tail 40 -Encoding UTF8
REM            ⚠️ PowerShell 5.1 的 Get-Content 預設用 ANSI(cp950)讀檔,
REM               少了 -Encoding UTF8 就算 log 是好的也會顯示成亂碼。
REM ============================================================
setlocal

REM 切到專案根目錄(這個檔在 scripts\ 底下)
cd /d "%~dp0.."

REM Python 這端本來就沒問題,但還是明講:子行程的 stdout 一律 UTF-8
set PYTHONIOENCODING=utf-8

if not exist "local_data" mkdir "local_data"

echo. >> "local_data\daily.log"
echo ================================================== >> "local_data\daily.log"
echo [%DATE% %TIME%] 排程觸發 >> "local_data\daily.log"

".venv\Scripts\python.exe" -m jobfinder.cli run >> "local_data\daily.log" 2>&1
set RC=%ERRORLEVEL%

REM 第 3 道閘門「必備命中率」。只打 OpenRouter、不打 104,所以 pipeline 失敗或熔斷也照跑。
REM 必須排在 pipeline 之後、依序執行:它讀 jobs.db 快照,不能跟正在寫回的 pipeline 同時跑。
REM 它的成敗不影響排程的 exit code —— 那個代表的是「今天有沒有抓到職缺」。
".venv\Scripts\python.exe" -m jobfinder.cli hitrate >> "local_data\daily.log" 2>&1
echo [%DATE% %TIME%] 命中率 exit code=%ERRORLEVEL% >> "local_data\daily.log"

echo [%DATE% %TIME%] 結束,exit code=%RC% >> "local_data\daily.log"
exit /b %RC%
