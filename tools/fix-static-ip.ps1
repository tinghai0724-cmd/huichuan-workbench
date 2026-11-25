$log = 'C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\net-apply.log'
function L($m) { "$(Get-Date -Format 'HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

L 'start fix-static-ip'
$alias = '以太网 2'
$ip = '192.168.0.126'
$mask = '255.255.254.0'
$gw = '192.168.1.1'
$dns1 = '192.168.1.1'

L "set address static $ip $mask gw $gw"
$out = & netsh interface ipv4 set address name="$alias" static $ip $mask $gw 2>&1
L ("netsh address -> " + ($out -join ' / '))

Start-Sleep -Seconds 6

L "set dns static $dns1"
$out = & netsh interface ipv4 set dnsservers name="$alias" static $dns1 primary 2>&1
L ("netsh dns -> " + ($out -join ' / '))

Start-Sleep -Seconds 8

$addr = Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue
foreach ($a in $addr) { L ("ip = " + $a.IPAddress + "/" + $a.PrefixLength + " origin=" + $a.PrefixOrigin) }
$cfg = Get-NetIPConfiguration -InterfaceAlias $alias -ErrorAction SilentlyContinue
L ("gw = " + $cfg.IPv4DefaultGateway.NextHop + "  dns = " + (($cfg.DNSServer | ForEach-Object { $_.ServerAddresses }) -join ','))
L ("ping gw = " + (Test-Connection -ComputerName $gw -Count 2 -Quiet -ErrorAction SilentlyContinue))
try { $r = Invoke-WebRequest 'http://192.168.0.126:8787/' -TimeoutSec 8 -UseBasicParsing; L ("8787 http " + $r.StatusCode) } catch { L ("8787 FAIL " + $_.Exception.Message) }
try { $null = Resolve-DnsName 'www.baidu.com' -ErrorAction Stop; L 'dns resolve ok' } catch { L ('dns FAIL ' + $_.Exception.Message) }
L 'done'
