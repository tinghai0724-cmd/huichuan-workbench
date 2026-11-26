# 慧川 AI 交流台（8081）常驻启动器：单实例探活 + 退出自动拉起
$ErrorActionPreference = 'Continue'
$aiDir = 'C:\Users\Administrator\Doubao\chats\2026-11-25\new-chat\huichuan'
Set-Location $aiDir
$port = 8081
$log  = Join-Path $aiDir '预览服务.out.log'
$say  = Join-Path $aiDir '预览服务.launcher.log'

$py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

function Say($m) { "[$((Get-Date).ToString('s'))] $m" | Out-File -FilePath $say -Append -Encoding utf8 }
function Port-Up {
  try { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -First 1) }
  catch { return $false }
}

$mutex = New-Object System.Threading.Mutex($false, 'Global\HuichuanAIConsoleLauncher')
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch { $owned = $false }
if (-not $owned) { Say 'another launcher alive, exit'; exit 0 }

Say "launcher up (python=$py, port=$port)"
while ($true) {
  if (Port-Up) { Start-Sleep -Seconds 8; continue }
  Say 'ai console not listening -> starting'
  & $py 'preview_server.py' '--port' "$port" *>> $log
  Say "ai console exited (code=$LASTEXITCODE), will retry"
  Start-Sleep -Seconds 3
}
