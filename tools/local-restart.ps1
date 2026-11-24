# 本地更新：重建前端产物（如需）并重启工作台服务
param(
  [int]$Port = 0,
  [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$webDir = Join-Path $root 'comfy_web'
$py = Join-Path $webDir 'server.py'
if (-not (Test-Path $py)) { throw "找不到 $py" }

if ($Port -le 0) {
  $Port = 8787
  $cfgPath = Join-Path $webDir 'config.local.json'
  if (Test-Path $cfgPath) {
    try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json; if ($cfg.port) { $Port = [int]$cfg.port } } catch {}
  }
}

# 配置不存在就从示例拷一份，提醒填凭据
$cfgPath = Join-Path $webDir 'config.local.json'
if (-not (Test-Path $cfgPath)) {
  Copy-Item (Join-Path $webDir 'config.example.json') $cfgPath
  "已生成 comfy_web/config.local.json，请先填 ComfyUI 地址与账号密码"
}

# 语法自检
python -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" $py
if ($LASTEXITCODE -ne 0) { throw 'server.py 语法检查未通过' }

if ($NoRestart) { "语法检查通过（按 -NoRestart 未重启）"; return }

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*server.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "已停止旧服务 PID $($_.ProcessId)" }

Start-Sleep -Seconds 2
$logDir = Join-Path $webDir 'data'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Process -FilePath python -ArgumentList "$py", '--port', "$Port" `
  -WorkingDirectory $webDir -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logDir 'server.out.log') `
  -RedirectStandardError (Join-Path $logDir 'server.err.log')

# 等服务起来
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:$Port/api/login/status" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { "本地工作台已启动：http://127.0.0.1:$Port"; break }
  } catch {}
  if ($i -eq 19) {
    "服务未在 10 秒内起来，日志："
    Get-Content (Join-Path $logDir 'server.err.log') -Tail 20
  }
}
