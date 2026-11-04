# Running the analyzer on the user's Windows PC (desktop bridge)

Field-tested on Windows 11 with Python 3.14: 76k files / 94 GB scanned, hashed and classified in ~15 s;
Recycle Bin, permanent delete and quarantine verified through the panel API. Big system drives (millions of files) take minutes, so the launch is
always detached.

## 0. Tools you'll use
- `Windows-MCP__PowerShell` — native shell, **not** limited to connected folders, so it can read any
  drive. Calls die around 30–60 s; keep each call short.
- `device_request_folder_access` / `device_commit_files` / `device_stage_files` — move files in and out
  of **one connected working folder**. You do not need to connect the drive being analyzed.
- `device_bash` is a *Linux VM* on the PC, not Windows. Fallback only (see the end).

## 1. Working folder
Use `C:\Users\<user>\claude-tools\drive-analyzer\` (`get_device_info` gives the home folder names).
Create it with PowerShell if missing, then ask once:
`device_request_folder_access(["C:\\Users\\<user>\\claude-tools"], reason=...)`.

## 2. Ship the scripts
Copy `scripts/*` from the skill into `/mnt/user-data/outputs/drive-analyzer/scripts/`, then
`device_commit_files` each file to `...\claude-tools\drive-analyzer\scripts\` with `force: true`.

**Trap:** the outputs folder syncs asynchronously. If you overwrite a file there and commit right
away, the *old* version can land on the PC. Wait ~5 s after copying, and verify:
`sha256sum` locally vs `(Get-FileHash <path>).Hash` on the PC (compare the first 16 chars).

## 3. Check Python
`Get-Command python, py -ErrorAction SilentlyContinue | Select Name, Source`.
No Python → use `scripts/list_files.ps1` (section 7).

## 4. Launch detached
```powershell
$base='C:\Users\<user>\claude-tools\drive-analyzer'; $out="$base\out\<DRIVE>-<yyyymmdd>"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$py=(Get-Command python).Source
$a=@('-X','utf8','-u',"$base\scripts\drive_analyzer.py",'analyze','D:\','--out',$out,'--display-root','D:\')
$p=Start-Process -FilePath $py -ArgumentList $a -WindowStyle Hidden -PassThru `
   -RedirectStandardOutput "$out\log.txt" -RedirectStandardError "$out\err.txt"
"PID=$($p.Id)"
```
- `-u` keeps `log.txt` live (otherwise it fills only at the end).
- Quoting: `Start-Process` joins the arguments with spaces and does not quote them. Arguments without
  spaces (`D:\`) are fine as they are. Put a path with spaces in its own quotes, and never let it end
  in `\"`: Windows reads `\"` as an escaped quote. Prefer `--display-root 'D:\'` unquoted.
- On a cloud or virtual drive (Google Drive for Desktop, OneDrive on-demand), add `--no-hash`.
- The output folder holds a `.drive-analyzer-out` marker, so scanning `C:\` skips it automatically.

## 5. Poll
```powershell
$out='...'; Start-Sleep 25; Get-Content "$out\progress.json"; "done=" + (Test-Path "$out\done.flag"); Get-Content "$out\err.txt" -Tail 5
```
`progress.json` shows the step (scan/dupes/classify), files counted, and seconds. A non-empty `err.txt`
together with a dead PID (`Get-Process -Id <pid>`) means a crash: read it, fix it, re-launch the same
command (the scan resumes; finished top-level folders are kept).

## 6. Bring results back
`device_stage_files` → `summary.json`, `report.html`, and `items.csv` if you need detail. They appear under
`/mnt/user-data/uploads/claude-tools/drive-analyzer/out/<run>/`. Re-staging can briefly show the old
copy, so wait 2 s and check a field that changed.

Re-classifying after overrides is fast enough for one PowerShell call on drives up to a few hundred
thousand files. On bigger drives, launch it detached too:
`python -X utf8 drive_analyzer.py classify --db <out>\inventory.sqlite --overrides <out>\overrides.json`
followed by `report --db ... --out <out>`.

## 6b. Open the live panel for the user
```powershell
$o='<out>'; Start-Process -FilePath (Get-Command python).Source -WindowStyle Hidden `
  -ArgumentList @('-X','utf8','-u','<scripts>\drive_analyzer.py','serve','--db',"$o\inventory.sqlite",'--open') `
  -RedirectStandardOutput "$o\serve.log" -RedirectStandardError "$o\serve.err"
```
`--open` launches the user's default browser on the PC. This call sometimes outlives the bridge's
60 s limit even though the server started fine. Don't re-run it. Check `<out>\panel_url.txt` and
`serve.log` (`PANEL http://127.0.0.1:…`), then give the user that URL. Also tell them about
`<out>\open-panel.cmd`, which reopens the panel any time without an agent.

To verify actions remotely, call the panel API with `Invoke-RestMethod` and header `X-Token` (the
token is the `t=` value), or read the `actions` table in `inventory.sqlite`. Don't enumerate the Recycle Bin
through `Shell.Application`: it is slow enough to hit the timeout. To confirm recycled items, read the
`$I…` files under `<drive>\$Recycle.Bin` instead (the original path is UTF-16 text from byte 28).

## 7. No Python on the PC
Launch `list_files.ps1 -Root D:\ -Out <out>\files.tsv` detached. When `files.tsv.done` exists, stage
the TSV (keep it under 400 MB), then in the workspace run `import-listing` → `dupes` (name+size only) →
`classify` → `report`. Duplicates are then "probable", not hash-verified, and `apply` cannot run in
the workspace. Execute approved moves on the PC with a small PowerShell loop built from `plan.json`
(`Move-Item -LiteralPath`, then write a manifest).

## 8. Linux VM fallback (`device_bash`)
If Windows-MCP is unavailable, connect the drive folder itself. It mounts at `$HOME/mnt/<name>`;
run `python3 drive_analyzer.py analyze $HOME/mnt/<name> --out <scratch> --display-root 'D:\' --budget 150`
and repeat the call until it exits 0 (exit code 3 = continue). The scan is slower through the mount,
and Windows attributes (cloud placeholders) aren't visible, so add `--no-hash` on synced folders.
