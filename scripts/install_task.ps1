<#
.SYNOPSIS
    把「104 每日職缺日報」註冊成 Windows 工作排程,每天 08:00 執行。

.DESCRIPTION
    關鍵設定是 -StartWhenAvailable:電腦在 08:00 沒開機時,開機後會盡快補跑一次。
    程式本身有「每日僅執行一次」的護欄,所以補跑不會造成重複推播。

    不需要系統管理員權限 —— 這個排程跑在你自己的使用者身分下。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 07:30
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall
#>
param(
    [string]$TaskName = "JobFinder Daily",
    [string]$Time = "08:00",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $ProjectRoot "scripts\run_daily.cmd"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除排程:$TaskName"
    exit 0
}

if (-not (Test-Path $Runner)) { throw "找不到 $Runner" }
if (-not (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe"))) {
    throw "找不到虛擬環境。請先跑:uv sync --all-extras"
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    throw "找不到 .env —— 沒有 Telegram 與 OpenRouter 憑證就跑不起來"
}

$action = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "104 每日職缺日報:抓取 -> LLM 評分 -> Telegram 推送。錯過會在開機後補跑。" `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host ""
Write-Host "已註冊排程" -ForegroundColor Green
Write-Host "  名稱      : $TaskName"
Write-Host "  執行       : $Runner"
Write-Host "  時間      : 每天 $Time"
Write-Host "  下次執行   : $($info.NextRunTime)"
Write-Host "  狀態      : $($task.State)"
Write-Host ""
Write-Host "錯過的話會在開機後盡快補跑(程式的每日一次護欄會防止重複推播)。"
Write-Host ""
Write-Host "手動測試 : Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "查看紀錄 : Get-Content local_data\daily.log -Tail 40 -Encoding UTF8"
Write-Host "移除排程 : powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall"
