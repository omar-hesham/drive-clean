---
name: drive-analyzer
description: >-
  Analyze any drive or folder (a Windows disk such as C: or D:, an external HDD/USB, a Mac/Linux
  folder, an uploaded folder, or a Google Drive account) and report what is on it: sizes per folder
  and file type, file dates and age, the biggest files, and exact duplicates. Sort everything into
  four recommendations (keep, maybe/نص نص, delete, unknown/مش عارف), then open a local action panel
  where the user can select items and send them to the Recycle Bin, delete them permanently, or
  open them in Explorer. Use it whenever the user wants to know what is filling a disk, free up
  space, clean up or organize a drive, or find junk, duplicates or old files, even if they only
  name a drive letter or complain that the disk is full. Arabic triggers: "حلل الدرايف"،
  "الهارد اتملى"، "إيه اللي واكل المساحة"، "نضّف الـ C"، "إيه اللي أمسحه وإيه اللي أسيبه".
  English triggers: "analyze my disk", "disk full", "what can I delete", "clean up my drive".
license: MIT
metadata:
  version: 1.1.0
  requires: Python 3.8+ (standard library only). Recycle Bin actions need Windows.
---

# Drive Analyzer: احتفظ بإيه، وامسح إيه

Turn "my disk is full" into a report the owner can trust and act on from one screen. Every
recommendation names the item, its size, its last-modified date and a one-line reason **why**. For
things marked delete, the reason also says how they come back ("بيرجع بـ npm install"، "تنزله تاني").

Reply in the user's language. For Arabic speakers use Egyptian Arabic with technical terms in English.
The report and panel are in Arabic (RTL).

`SKILL_DIR` below means the folder that contains this file. The engine is
`SKILL_DIR/scripts/drive_analyzer.py`. It uses the Python standard library only.

## The four buckets

| bucket | label | meaning |
|---|---|---|
| `delete` | امسح | regenerable or junk: caches, temp files, partial downloads (browser/yt-dlp), crash dumps, old installers in download folders, `node_modules` of inactive projects, hash-verified duplicate copies |
| `maybe` | نص نص | probably removable, but a human should glance at it: ISOs/VM disks, downloaded movies and YouTube rips, archives already extracted, AI models, cracked software (flagged as a security risk), old downloads, backups |
| `unknown` | مش عارف | unclear what it is: unknown file types, portable apps/games outside Program Files. **Ask the user** |
| `keep` | احتفظ | personal files, projects, system, installed apps |

Personal documents and media are never put in `delete` unless they are hash-verified duplicates.
System folders are locked: the panel refuses to delete them and offers the right Windows tool instead.
The logic is in `scripts/rules.py`, explained in `references/rules.md`.

## Workflow

### 1. Pick the target
Which drive, folder or account? If the user just says "the disk", list the drives with used/free
space and ask which one, unless one is obviously full. On Windows:
`Get-PSDrive -PSProvider FileSystem`. On macOS/Linux: `df -h`.

### 2. Run the analysis
**Agent running on the same machine** (Claude Code, Codex, Gemini CLI, Antigravity, OpenCode,
Cursor, Copilot…): run it in the background, because big drives take minutes.
```
python SKILL_DIR/scripts/drive_analyzer.py analyze D:\ --title "تحليل D:"
```
- Output goes to `~/drive-analyzer-reports/<drive>-<date>/` (override with `--out`). It contains
  `report.html`, `summary.json`, `items.csv`, `inventory.sqlite`, `open-panel.cmd`/`.sh`, and
  `done.flag` when finished. `progress.json` shows the current step.
- The scan is resumable. With `--budget SEC` it stops between top-level folders and exits with
  code 3; run the same command again to continue. Use this when your shell has a hard timeout.
- Duplicate hashing reads file contents (capped by `--hash-gb 30`). Add `--no-hash` on cloud or virtual
  drives (Google Drive for Desktop, OneDrive on-demand), because hashing would download every file.
  Cloud-only placeholders are skipped automatically.

**Agent in the cloud, linked to the user's PC through a bridge** (e.g. the Claude desktop app):
follow `references/windows-bridge.md` (ship the scripts, launch detached, poll, stage results back).

**Google Drive:** `references/google-drive.md`.
**Uploaded folder or zip:** extract it into an empty folder and run `analyze` on it.

### 3. Review before presenting
Read `summary.json`, which is small. Check it like a reviewer would:
- Does the total roughly match the drive's used space? A big gap usually means unreadable folders
  (`errors`) or system files.
- Skim the top `delete` items. Anything personal-looking there is a rule bug: fix it with an override
  (step 5) before showing the user, and say so.
- The top `unknown` items become your questions to the user.

### 4. Deliver
1. **The live panel (main deliverable for local drives).** Start it in the background:
   `python SKILL_DIR/scripts/drive_analyzer.py serve --db <out>/inventory.sqlite --open`
   It opens `http://127.0.0.1:<port>/?t=<token>` in the user's browser (the URL is also saved in
   `<out>/panel_url.txt`). The panel lets the user:
   - browse grouped categories, "select group" and "select all";
   - use 🗑️ Recycle Bin, 📦 move to `_to_delete`, or ⛔ permanent delete (with confirmation);
   - use 📂 to open the item in Explorer;
   - use buttons for Windows tools (empty bin, Disk Cleanup, Apps).
   Free space updates live. Later the user can reopen it any time by double-clicking
   `open-panel.cmd` (Windows) or `open-panel.sh` in the output folder.
2. **A summary in the chat:**
   - the drive's size and free space;
   - the top 5 folders, with size and %;
   - how much data is older than 2 years;
   - a table of the four buckets (size and item count);
   - the 3–5 biggest wins in `delete`, with reasons;
   - the biggest `maybe` items;
   - the 3–8 biggest `unknown` items, asked as direct questions.
   Keep it scannable, because the panel holds the detail.
3. If your platform can publish pages (e.g. Claude artifacts), you can also publish `report.html`
   as a read-only shareable report. Its action buttons only work inside the local panel.

### 5. Answer the unknowns → reclassify
Write the user's decisions to `<out>/overrides.json`:
```json
[{"prefix": "Games/Foo", "bucket": "delete", "reason": "لعبة مش بلعبها — قرار المستخدم"}]
```
`prefix` is relative to the scanned root and uses `/`. Then re-run `classify --db … --overrides …` and
`report --db … --out …`. This takes seconds and needs no rescan. Reload the panel afterwards.

### 6. Acting on items
The user acts from the panel themselves. That is the point of the panel. If they ask *you* to do
the deletion, read `references/cleanup.md` first:
- get explicit approval per category;
- prefer the Recycle Bin, or `_to_delete` on drives without a bin;
- run permanent deletion only when the user explicitly says so;
- never touch locked/system items.
The CLI equivalents are `plan` → `apply` (dry run) → `apply --confirm`, and `undo <manifest>`.

### 7. Close
Show free space before and after. Remind the user that the Recycle Bin and `_to_delete` still hold
space until they are emptied.

## Gotchas
- **Recycle Bin limits.** USB/network drives have no bin, some bins are set to "delete immediately", and
  items bigger than the bin's capacity would be silently deleted for good by Windows. The panel detects
  all three cases and refuses to "recycle" those items, suggesting `_to_delete` or explicit permanent
  deletion instead.
- **Permission errors** while scanning `C:\` without admin are normal for a few system folders. The
  report counts them. Don't elevate just to scan.
- **Files in use** (open apps, running dev servers) are skipped and reported per item. Close the app
  and retry.
- **Dates.** The report uses last-modified. Windows sets "created" to the copy date, and last-access is
  usually not recorded, so neither is used for age.
- **Sync folders** (OneDrive/Google Drive/Dropbox): deleting there deletes in the cloud too. The
  report marks these items with ⚠️.

## Files
- `scripts/drive_analyzer.py`: CLI with `analyze`, `serve`, `scan`, `dupes`, `classify`, `report`,
  `import-gdrive`, `import-listing`, `plan`, `apply`, `undo` (`--help` on each)
- `scripts/panel.py`: the local action panel (127.0.0.1 only, token-protected)
- `scripts/rules.py`: classification rules, thresholds, category titles, locked rules
- `scripts/report_template.html`: the Arabic RTL report/panel UI
- `scripts/list_files.ps1`: fallback lister for a PC without Python
- `references/`: `windows-bridge.md` (remote agents), `google-drive.md`, `rules.md`, `cleanup.md`
