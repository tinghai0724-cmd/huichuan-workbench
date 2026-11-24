# 从 comfy_web/index.html 生成 GitHub Pages 版（docs/index.html）
# 用法：
#   pwsh tools/build-docs.ps1                      # 不带默认后端地址
#   pwsh tools/build-docs.ps1 -Api https://xxx.example.com   # 预置后端地址
param(
  [string]$Api = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root 'comfy_web\index.html'
$outDir = Join-Path $root 'docs'
$out  = Join-Path $outDir 'index.html'

if (-not (Test-Path $src)) { throw "找不到 $src" }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$html = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)

# 在唯一的业务 <script> 之前插入一行默认后端地址配置
$inject = ''
if ($Api) {
  $escaped = $Api.TrimEnd('/')
  $inject = "<script>window.CW_DEFAULT_API = " + ($escaped | ConvertTo-Json) + ";</script>`n"
} else {
  $inject = "<script>window.CW_DEFAULT_API = '';</script>`n"
}

$idx = $html.IndexOf('<script>')
if ($idx -lt 0) { throw 'index.html 里没找到 <script> 标签' }
$html = $html.Substring(0, $idx) + $inject + $html.Substring($idx)

[System.IO.File]::WriteAllText($out, $html, [System.Text.Encoding]::UTF8)
"已生成 $out" + $(if ($Api) { "（默认后端 $Api）" } else { '（未预置后端地址，可用 ?api= 指定）' })
