<#
  drive-analyzer — Windows installer.

  Installs the skill once into   %USERPROFILE%\.agents\skills\drive-analyzer   (the shared Agent Skills folder)
  and links it (directory junction, no admin needed) into every AI agent's skills folder that exists:
    Claude Code, Codex, Gemini CLI, Antigravity, OpenCode, Cursor, GitHub Copilot, Kimi.
  One copy → updating it updates every agent.

  Usage (from a clone):        powershell -ExecutionPolicy Bypass -File .\install.ps1
  One-liner (no clone):        irm https://raw.githubusercontent.com/<owner>/<repo>/main/install.ps1 | iex
  Remove:                      powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
  Only some agents:            .\install.ps1 -Agents claude,codex
#>
param(
  [switch]$Uninstall,
  [string[]]$Agents = @(),
  [string]$Repo = 'https://github.com/omar-hesham/drive-clean.git'
)
$ErrorActionPreference = 'Stop'
$Name = 'drive-analyzer'
$H = $env:USERPROFILE
$Canon = Join-Path $H ".agents\skills\$Name"

# agent key -> skills folder (relative to the user profile); a folder is used only if the agent is installed
$Targets = [ordered]@{
  'claude'          = '.claude\skills'
  'codex'           = '.codex\skills'
  'gemini'          = '.gemini\skills'
  'antigravity'     = '.gemini\config\skills'
  'antigravity-old' = '.gemini\antigravity\skills'
  'opencode'        = '.config\opencode\skills'
  'cursor'          = '.cursor\skills'
  'copilot'         = '.copilot\skills'
  'kimi'            = '.kimi\skills'
}
function AgentInstalled($rel) {
  $first = $rel.Split('\')[0]; if ($first -eq '.config') { $first = '.config\opencode' }
  Test-Path (Join-Path $H $first)
}

if ($Uninstall) {
  foreach ($k in $Targets.Keys) {
    $link = Join-Path (Join-Path $H $Targets[$k]) $Name
    if (Test-Path $link) { cmd /c rmdir "$link" | Out-Null; Write-Host "removed link  $link" }
  }
  if (Test-Path $Canon) { Remove-Item -LiteralPath $Canon -Recurse -Force; Write-Host "removed       $Canon" }
  return
}

# 1) get the files into the canonical folder
$src = $PSScriptRoot
if (-not $src -or -not (Test-Path (Join-Path $src 'SKILL.md'))) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is needed for the one-line install (or download the zip and run install.ps1 from it).' }
  $tmp = Join-Path $env:TEMP "$Name-src"; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  git clone --depth 1 $Repo $tmp | Out-Null; $src = $tmp
}
New-Item -ItemType Directory -Force -Path (Split-Path $Canon) | Out-Null
if ((Resolve-Path $src).Path.TrimEnd('\') -ne $Canon) {
  if (Test-Path $Canon) { Remove-Item -LiteralPath $Canon -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $Canon | Out-Null
  Get-ChildItem -LiteralPath $src -Force | Where-Object { $_.Name -notin '.git', '.github', 'tests', '__pycache__' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $Canon -Recurse -Force }
}
Write-Host "installed     $Canon"

# 2) link into every installed agent
foreach ($k in $Targets.Keys) {
  if ($Agents.Count -and ($Agents -notcontains $k) -and ($Agents -notcontains ($k -replace '-old',''))) { continue }
  $rel = $Targets[$k]
  if (-not (AgentInstalled $rel)) { continue }
  $dir = Join-Path $H $rel; $link = Join-Path $dir $Name
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  if (Test-Path $link) {
    $item = Get-Item -LiteralPath $link -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { cmd /c rmdir "$link" | Out-Null }
    else { Rename-Item -LiteralPath $link -NewName "$Name.bak-$(Get-Date -Format yyyyMMddHHmmss)" }
  }
  New-Item -ItemType Junction -Path $link -Target $Canon | Out-Null
  Write-Host ("linked        {0,-16} {1}" -f $k, $link)
}

# 3) check Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if ($py) { Write-Host "python        $(& $py.Source --version 2>&1)" }
else { Write-Warning 'Python 3.8+ was not found. Install it (winget install Python.Python.3.12) — the skill needs it.' }
Write-Host "`nDone. Restart your agents, then ask: 'حلل الدرايف D:' or 'analyze my D: drive'."
Write-Host "Without an agent:  python `"$Canon\scripts\drive_analyzer.py`" analyze D:\ --serve"
