<#
.SYNOPSIS
    在桌面建立「Job Finder UI」捷徑,雙擊就能開 Web UI,不必開終端機或 Claude Code。

.DESCRIPTION
    捷徑指向 scripts\open_ui.cmd:沿用專案的 .venv 跑 `jobfinder ui`,只綁 127.0.0.1。
    open_ui.cmd 刻意寫成純 ASCII:chcp 65001 下,cmd 會把含中文的註解行切碎、當成指令執行
    (2026-09-30 實測)。中文說明一律寫在這個檔。
    隨用隨開 —— 關掉那個黑色視窗就結束,不用時沒有服務在背景跑。

    不需要系統管理員權限。桌面路徑用 GetFolderPath 取得,相容 OneDrive 重導向的桌面。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_ui_shortcut.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_ui_shortcut.ps1 -Uninstall
#>
param(
    [string]$Name = "Job Finder UI",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $ProjectRoot "scripts\open_ui.cmd"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk"

if ($Uninstall) {
    if (Test-Path $Shortcut) {
        Remove-Item $Shortcut
        Write-Host "已移除捷徑:$Shortcut"
    } else {
        Write-Host "沒有找到捷徑:$Shortcut"
    }
    exit 0
}

if (-not (Test-Path $Launcher)) { throw "找不到 $Launcher" }
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "找不到虛擬環境。請先跑:uv sync --all-extras"
}
& $Python -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "虛擬環境沒有 streamlit。請先跑:uv sync --all-extras"
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($Shortcut)
$lnk.TargetPath = $Launcher
$lnk.WorkingDirectory = $ProjectRoot
$lnk.Description = "開啟 Job Finder Web UI(只綁 127.0.0.1,關閉黑色視窗即結束)"
# 用 Python 的圖示,比 .cmd 的預設齒輪好認
$lnk.IconLocation = "$Python,0"
$lnk.Save()

Write-Host "已建立捷徑:$Shortcut"
Write-Host "雙擊即可開啟 http://127.0.0.1:8501;關掉黑色視窗就結束。"
