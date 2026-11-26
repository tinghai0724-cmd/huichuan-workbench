# 给「计划任务 / 后台常驻」用的静默启动器：不弹窗、日志落盘
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root 'comfy_web'
Set-Location $web

# 优先系统装的 Python，退回 PATH 里的 python
$candidates = @(
  'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe',
  "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
  'C:\Python313\python.exe'
)
$py = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = 'python' }

$logDir = Join-Path $web 'data'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
& $py 'server.py' '--port' '8787' *>> (Join-Path $logDir 'server.out.log')
