# 慧川工作台看门狗：端口/接口不通就把常驻任务拉起来（每 3 分钟由计划任务调用）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$log  = Join-Path (Join-Path $root 'comfy_web') 'data\watchdog.log'
$port = 8787

function Say($m) { "[$((Get-Date).ToString('s'))] $m" | Out-File -FilePath $log -Append -Encoding utf8 }

$listen = $false
try { $listen = [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -First 1) } catch {}
if ($listen) { exit 0 }

Say 'port 8787 is down -> start task Huichuan-ComfyWorkbench'
try {
  Start-ScheduledTask -TaskName 'Huichuan-ComfyWorkbench'
  Say 'start task requested'
} catch {
  Say ("start task failed: " + $_.Exception.Message)
  exit 1
}
