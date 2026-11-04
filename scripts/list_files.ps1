<#
  drive-analyzer / list_files.ps1 — FALLBACK lister for machines without Python.
  Writes a UTF-8 TSV: rel <TAB> size <TAB> mtime_epoch <TAB> ctime_epoch
  Then stage the TSV into the workspace and run:
    python drive_analyzer.py import-listing files.tsv --db inv.sqlite --root "D:\" --display-root "D:\"
  Read-only. Skips junctions/symlinks and folders it can't open.
#>
param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'SilentlyContinue'
$Root = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\') + '\'
$epoch = [datetime]'1970-01-01T00:00:00Z'
$w = New-Object System.IO.StreamWriter($Out, $false, (New-Object System.Text.UTF8Encoding($false)))
$w.WriteLine("rel`tsize`tmtime`tctime")
$stack = New-Object System.Collections.Generic.Stack[string]
$stack.Push($Root)
while ($stack.Count) {
  $d = $stack.Pop()
  try { $di = New-Object System.IO.DirectoryInfo($d); $entries = $di.GetFileSystemInfos() } catch { continue }
  foreach ($e in $entries) {
    if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
    if ($e -is [System.IO.DirectoryInfo]) { if ($e.Name -ne '_to_delete') { $stack.Push($e.FullName) }; continue }
    $rel = $e.FullName.Substring($Root.Length)
    $m = [long]($e.LastWriteTimeUtc - $epoch).TotalSeconds; $c = [long]($e.CreationTimeUtc - $epoch).TotalSeconds
    $w.WriteLine("$rel`t$($e.Length)`t$m`t$c")
  }
}
$w.Close()
New-Item -ItemType File -Force -Path ($Out + '.done') | Out-Null
