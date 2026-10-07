<#
.SYNOPSIS
    在桌面建立「Job Finder UI」捷徑,雙擊就能開 Web UI,不必開終端機或 Claude Code。

.DESCRIPTION
    捷徑指向 scripts\open_ui.cmd:沿用專案的 .venv 跑 `jobfinder ui`,只綁 127.0.0.1。
    open_ui.cmd 刻意寫成純 ASCII:chcp 65001 下,cmd 會把含中文的註解行切碎、當成指令執行
    (2026-09-30 實測)。中文說明一律寫在這個檔。
    隨用隨開 —— 關掉那個黑色視窗就結束,不用時沒有服務在背景跑。

    不需要系統管理員權限。桌面路徑用 GetFolderPath 取得,相容 OneDrive 重導向的桌面。

    -App selfeval 建立「Job Finder 自評」捷徑(scripts\open_selfeval.cmd,port 8502):
    逐張卡片評想去程度、分數準不準與技能自評。畫面跟 Job Finder UI 分開,資料共用 decisions.db。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_ui_shortcut.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_ui_shortcut.ps1 -App selfeval
    powershell -ExecutionPolicy Bypass -File scripts\install_ui_shortcut.ps1 -App selfeval -Uninstall
#>
param(
    [ValidateSet("jobfinder", "selfeval")]
    [string]$App = "jobfinder",
    [string]$Name = "",
    [switch]$Uninstall
)

$Apps = @{
    jobfinder = @{ Name = "Job Finder UI"; Launcher = "open_ui.cmd"; Port = 8501;
                   Desc = "開啟 Job Finder Web UI" }
    selfeval  = @{ Name = "Job Finder 自評"; Launcher = "open_selfeval.cmd"; Port = 8502;
                   Desc = "開啟 Job Finder 自評 app" }
}
$Spec = $Apps[$App]
if (-not $Name) { $Name = $Spec.Name }

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $ProjectRoot ("scripts\" + $Spec.Launcher)
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
$lnk.Description = "$($Spec.Desc)(只綁 127.0.0.1,關閉黑色視窗即結束)"
# 用 Python 的圖示,比 .cmd 的預設齒輪好認
$lnk.IconLocation = "$Python,0"
$lnk.Save()

Write-Host "已建立捷徑:$Shortcut"
Write-Host "雙擊即可開啟 http://127.0.0.1:$($Spec.Port);關掉黑色視窗就結束。"
