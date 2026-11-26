# 慧川工作台常驻启动器（计划任务用）
# 特性：单实例互斥、端口探活、进程退出自动拉起、永不主动退出（由任务计划/看门狗兜底）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$web  = Join-Path $root 'comfy_web'
Set-Location $web
$port = 8787

$candidates = @(
  'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe',
  "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
  'C:\Python313\python.exe'
)
$py = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = 'python' }

$logDir = Join-Path $web 'data'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log    = Join-Path $logDir 'server.out.log'
$launch = Join-Path $logDir 'launcher.log'

function Say($m) { "[$((Get-Date).ToString('s'))] $m" | Out-File -FilePath $launch -Append -Encoding utf8 }
function Port-Up {
  try { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -First 1) }
  catch { return $false }
}

# 只保留一个启动器，避免双开抢端口
$mutex = New-Object System.Threading.Mutex($false, 'Global\HuichuanComfyWorkbenchLauncher')
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch { $owned = $false }
if (-not $owned) { Say 'another launcher is alive, exit'; exit 0 }

Say "launcher up (python=$py, port=$port)"
while ($true) {
  if (Port-Up) { Start-Sleep -Seconds 8; continue }
  Say 'server not listening -> starting'
  & $py 'server.py' '--port' "$port" *>> $log
  Say "server process exited (code=$LASTEXITCODE), will retry"
  Start-Sleep -Seconds 3
}
