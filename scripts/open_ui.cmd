@chcp 65001 >nul
@echo off
REM ============================================================
REM  Job Finder Web UI launcher - target of the desktop shortcut.
REM  Double-click: start the UI and open the browser. If it is already
REM  running, only open the browser (never a second server).
REM  Close this window (or Ctrl+C) to stop it.
REM
REM  Binds 127.0.0.1 only (via `jobfinder ui`), reads a jobs.db snapshot,
REM  never talks to 104.
REM
REM  This file is deliberately ASCII-only. Under chcp 65001, cmd.exe
REM  mis-tracks byte offsets on lines containing multi-byte UTF-8 and
REM  executes fragments of Chinese comments as commands (seen 2026-09-30).
REM  The Chinese docs live in scripts\install_ui_shortcut.ps1.
REM ============================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PORT=8501
set URL=http://127.0.0.1:%PORT%

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. From the project root run:
    echo   uv venv --python 3.12 ^&^& uv pip install -e ".[dev,ui]"
    pause
    exit /b 1
)

REM Already running -> just open the browser. Only a LISTENING socket on
REM 127.0.0.1 counts; another program on 0.0.0.0 is not ours.
netstat -ano | findstr /C:"127.0.0.1:%PORT% " | findstr LISTENING >nul
if not errorlevel 1 (
    start "" "%URL%"
    exit /b 0
)

REM Open the browser once Streamlit actually listens (up to 60 s);
REM a fixed delay is often too short on a cold start.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command ^
  "for ($i = 0; $i -lt 60; $i++) { try { (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1', %PORT%); Start-Process '%URL%'; break } catch { Start-Sleep 1 } }"

title Job Finder UI - close this window to stop
".venv\Scripts\python.exe" -m jobfinder.cli ui --port %PORT%
set RC=%ERRORLEVEL%

REM Closing the window never reaches here; a non-zero exit means startup
REM failed, so keep the window open to show the error.
if not "%RC%"=="0" pause
exit /b %RC%
