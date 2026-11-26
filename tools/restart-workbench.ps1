# 安全重启慧川工作台：只杀占用 8787 的那个进程，不误伤其它 Python 服务（如 8081 的 AI 交流台）
$ErrorActionPreference = 'Continue'
$taskName = 'Huichuan-ComfyWorkbench'

function Say($m) { $m }

$owners = @()
try {
  $owners = (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)
} catch {}

Say "停止计划任务 $taskName"
try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 2

foreach ($procId in $owners) {
  if ($procId -and $procId -ne $PID) {
    Say "结束占用 8787 的进程 PID=$procId"
    try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
  }
}
Start-Sleep -Seconds 3

Say "启动计划任务 $taskName"
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 16

try {
  $r = Invoke-WebRequest 'http://127.0.0.1:8787/' -UseBasicParsing -TimeoutSec 10
  Say "工作台已就绪 => HTTP $($r.StatusCode)"
} catch {
  Say "工作台还没响应：$($_.Exception.Message)（稍等几秒再看，或点看门狗任务）"
}
