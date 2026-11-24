# 把本地工作台推到 GitHub（不需要安装 git，走 GitHub REST API）
#
# 用法示例：
#   pwsh tools/publish.ps1 -Repo tinghai0724/huichuan-workbench -Token ghp_xxx -Message "首页改版"
#   pwsh tools/publish.ps1 -Repo tinghai0724/huichuan-workbench -Create -Pages -Message "首次上线"
#
# Token 也可以放在仓库根目录的 gh_token.txt（已 gitignore），或环境变量 GH_TOKEN。
param(
  [Parameter(Mandatory = $true)][string]$Repo,      # owner/repo
  [string]$Token = '',
  [string]$Message = '',
  [string]$Branch = 'main',
  [switch]$Create,                                  # 仓库不存在时创建
  [switch]$Private,                                 # 配合 -Create，建私有仓库
  [switch]$Pages,                                   # 开启 GitHub Pages（源 = /docs）
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$Api = 'https://api.github.com'

# ---------- token ----------
if (-not $Token) { $Token = $env:GH_TOKEN }
if (-not $Token) {
  $f = Join-Path $root 'gh_token.txt'
  if (Test-Path $f) { $Token = (Get-Content $f -Raw).Trim() }
}
if (-not $Token) { throw '缺少 GitHub Token：用 -Token 传，或写进根目录 gh_token.txt，或设 GH_TOKEN' }

$H = @{ Authorization = "Bearer $Token"; Accept = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28' }

function GApi($method, $path, $body = $null) {
  $uri = if ($path -match '^https?://') { $path } else { $Api + $path }
  $lastErr = $null
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      $params = @{ Uri = $uri; Method = $method; Headers = $H; TimeoutSec = 120 }
      if ($null -ne $body) {
        $params.Body = ($body | ConvertTo-Json -Depth 20 -Compress)
        $params.ContentType = 'application/json; charset=utf-8'
      }
      return Invoke-RestMethod @params
    } catch {
      $lastErr = $_
      $msg = "$($_.Exception.Message)"
      # 5xx / 超时 才重试；4xx（权限、参数）直接抛出
      if ($msg -match '\(4\d\d\)' -or $_.ErrorDetails.Message -match 'rate limit') { throw }
      if ($attempt -lt 3) { Start-Sleep -Seconds (2 * $attempt); Write-Host "  ↻ 第 $attempt 次失败，重试 $method $path" }
    }
  }
  throw $lastErr
}

# ---------- 1. 仓库 ----------
$owner, $name = $Repo.Split('/', 2)
if (-not $name) { throw "Repo 要写成 owner/repo，例如 tinghai0724/huichuan-workbench" }

$repoExists = $true
try { $info = GApi GET "/repos/$Repo" } catch { $repoExists = $false }

if (-not $repoExists) {
  if (-not $Create) { throw "仓库 $Repo 不存在，加 -Create 让我创建" }
  $payload = @{ name = $name; description = 'ComfyUI 本地工作台（监管 + 出图 + 资产库）'; private = [bool]$Private; auto_init = $true }
  $info = GApi POST '/user/repos' $payload
  "已创建仓库：$($info.html_url)"
}

# ---------- 2. 收集要上传的文件 ----------
$skipDirs = @('comfy_out', '__pycache__', '.git', '.idea', 'node_modules')
$skipFiles = @('gh_token.txt', '.github_token', 'object_info.json')
function Test-Skip([string]$rel) {
  $parts = $rel -split '[\\/]'
  foreach ($p in $parts) { if ($skipDirs -contains $p) { return $true } }
  $leaf = $parts[-1]
  if ($skipFiles -contains $leaf) { return $true }
  if ($leaf -like '*.log') { return $true }
  if ($rel -like 'comfy_web/data/*') { return $true }
  if ($rel -eq 'comfy_web/config.local.json') { return $true }
  return $false
}

$files = @()
Get-ChildItem -Path $root -Recurse -File -Force | ForEach-Object {
  $rel = $_.FullName.Substring($root.Length + 1).Replace('\', '/')
  if (-not (Test-Skip $rel)) { $files += [pscustomobject]@{ Rel = $rel; Full = $_.FullName } }
}

# ---------- 3. 远程当前状态 ----------
$headSha = $null
$remoteTree = @{}
try {
  $ref = GApi GET "/repos/$Repo/git/ref/heads/$Branch"
  $headSha = $ref.object.sha
  $tree = GApi GET "/repos/$Repo/git/trees/$headSha`?recursive=1"
  foreach ($t in $tree.tree) { if ($t.type -eq 'blob') { $remoteTree[$t.path] = $t.sha } }
  "远程 HEAD：$($headSha.Substring(0,7)) / 现有文件 $($remoteTree.Count) 个"
} catch {
  "分支 $Branch 还不存在，将创建首个提交"
}

function Get-BlobSha([string]$path) {
  $bytes = [System.IO.File]::ReadAllBytes($path)
  $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)`0")
  $all = New-Object byte[] ($header.Length + $bytes.Length)
  [Array]::Copy($header, 0, $all, 0, $header.Length)
  [Array]::Copy($bytes, 0, $all, $header.Length, $bytes.Length)
  $sha1 = [System.Security.Cryptography.SHA1]::Create()
  ([BitConverter]::ToString($sha1.ComputeHash($all))).Replace('-', '').ToLower()
}

# ---------- 4. 逐文件比对、上传有变化的 ----------
$treeEntries = @()
$added = 0; $changed = 0; $same = 0
foreach ($f in $files) {
  $localSha = Get-BlobSha $f.Full
  if ($remoteTree.ContainsKey($f.Rel) -and $remoteTree[$f.Rel] -eq $localSha) {
    $same++
    continue
  }
  if ($DryRun) { "  [dry] 需上传 $($f.Rel)"; $changed++; continue }
  $content = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($f.Full))
  $blob = GApi POST "/repos/$Repo/git/blobs" @{ content = $content; encoding = 'base64' }
  $treeEntries += @{ path = $f.Rel; mode = '100644'; type = 'blob'; sha = $blob.sha }
  if ($remoteTree.ContainsKey($f.Rel)) { $changed++ } else { $added++ }
  Write-Host "  ↑ $($f.Rel)"
}

# 本地已删除、但远程还在的文件 → 从树里移除
$localPaths = $files.Rel
$deleted = 0
foreach ($p in $remoteTree.Keys) {
  if (Test-Skip $p) { continue }
  if ($localPaths -notcontains $p) {
    $treeEntries += @{ path = $p; mode = '100644'; type = 'blob'; sha = $null }
    $deleted++
  }
}

if ($DryRun) { "dry-run：新增/修改 $changed，删除 $deleted，未变 $same"; return }
if ($treeEntries.Count -eq 0 -and $headSha) { "没有变化，跳过提交（未变 $same 个文件）"; return }

# ---------- 5. 建树 + 提交 + 推进分支 ----------
$treeBody = @{ tree = $treeEntries }
if ($headSha) { $treeBody.base_tree = (GApi GET "/repos/$Repo/git/commits/$headSha").tree.sha }
$newTree = GApi POST "/repos/$Repo/git/trees" $treeBody

if (-not $Message) { $Message = "更新 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
$commitBody = @{ message = $Message; tree = $newTree.sha }
if ($headSha) { $commitBody.parents = @($headSha) }
$commit = GApi POST "/repos/$Repo/git/commits" $commitBody

if ($headSha) {
  GApi PATCH "/repos/$Repo/git/refs/heads/$Branch" @{ sha = $commit.sha; force = $false } | Out-Null
} else {
  GApi POST "/repos/$Repo/git/refs" @{ ref = "refs/heads/$Branch"; sha = $commit.sha } | Out-Null
}

"已提交 $($commit.sha.Substring(0,7))：新增 $added / 修改 $changed / 删除 $deleted / 未变 $same"
"仓库：https://github.com/$Repo"

# ---------- 6. 开启 Pages ----------
if ($Pages) {
  try {
    GApi POST "/repos/$Repo/pages" @{ source = @{ branch = $Branch; path = '/docs' } } | Out-Null
    "已开启 GitHub Pages（源：$Branch /docs）"
  } catch {
    # 已开启会返回 409，直接查状态
    try {
      $p = GApi GET "/repos/$Repo/pages"
      "Pages 已存在：$($p.html_url)"
    } catch { "Pages 开启失败：$($_.Exception.Message)" }
  }
  Start-Sleep -Seconds 3
  try {
    $p = GApi GET "/repos/$Repo/pages"
    "线上地址：$($p.html_url)"
  } catch {}
}
