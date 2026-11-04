# Acting on the recommendations — safely

## Preferred: the user acts in the live panel
`serve --db <out>/inventory.sqlite --open` starts the panel. The user selects items (per row, per
group, or "select all") and picks one of:

| button | what happens | reversible? | frees space? |
|---|---|---|---|
| 🗑️ لسلة المحذوفات | Windows Recycle Bin (`SHFileOperation` + ALLOWUNDO) | yes, from the bin | only after emptying the bin |
| 📦 نقل لـ _to_delete | moved to `<root>\_to_delete\<stamp>\` + `manifest.csv` | yes, `undo <manifest>` | only after deleting the folder |
| ⛔ مسح نهائي | permanently removed (read-only files included) | **no** | yes, immediately |
| 📂 | opens the item in Explorer / Finder | — | — |

Guards the panel enforces server-side, whatever the page sends:
- Only items from this inventory can be acted on, never an arbitrary path. Every target must sit
  inside the scanned root.
- Locked rules (Windows, Program Files, AppData app data, WSL/Docker disks, pagefile, Recycle Bin
  itself, update caches…) are refused. Their rows show a Windows-tool button instead.
- Recycling is refused, with the reason shown, when the drive has no Recycle Bin (USB/network), when
  the bin is set to "delete immediately", or when an item is bigger than the bin's capacity. Windows
  would otherwise delete those items for good without saying so.
- Permanent delete needs a confirmation checkbox in the page.
- Every action is logged in the `actions` table of `inventory.sqlite`, with its result.
- The server listens on 127.0.0.1 only, needs a random token on every call, checks the Host header,
  and exits after 3 idle hours, or when the user presses "قفل اللوحة".

## When the user asks the agent to do it
Nothing moves without the user's explicit OK in this conversation. A "go ahead" for `delete` does
not cover `maybe`, and approving one category does not approve the others.

1. Offer choices by category, e.g. "الكاش والمؤقت (X GB)"، "ملفات التسطيب القديمة (Y GB)"، "التحميلات
   الناقصة"، "النسخ المكررة"… Use a multi-select question if your client has one.
2. Prefer: through the panel API, or the CLI with quarantine:
   ```
   python drive_analyzer.py plan --db <out>/inventory.sqlite --out <out>/plan.json --buckets delete
   python drive_analyzer.py plan --db … --out … --items approved.txt      # specific item ids
   python drive_analyzer.py apply <out>/plan.json             # dry run: count, size, first targets
   python drive_analyzer.py apply <out>/plan.json --confirm   # → <root>/_to_delete/<stamp>/
   python drive_analyzer.py undo <root>/_to_delete/<stamp>/manifest.csv --confirm
   ```
   `--buckets` takes only items whose action is `move`. Everything else goes under `manual` in the plan.
3. Before removing project caches (`node_modules`, venvs, build folders), make sure no dev server
   or IDE is using that project.
4. Close by re-checking free space and saying what is still parked in the bin or in `_to_delete`.

## Manual / system items: give the exact step
| item | how |
|---|---|
| Recycle Bin | panel button "فضّي سلة المحذوفات", or `Clear-RecycleBin -DriveLetter C` (permanent; ask first) |
| Windows.old, update leftovers, Windows Temp, update cache, thumbnails | panel button → Disk Cleanup → "Clean up system files", or Settings › System › Storage › Temporary files |
| WinSxS | `DISM /Online /Cleanup-Image /StartComponentCleanup` (admin) |
| hiberfil.sys | `powercfg -h off` (admin), only if Hibernate/Fast Startup isn't used |
| Installed apps, games | panel button → Settings › Apps › Installed apps → Uninstall; Steam/Epic from their launcher |
| AI models | `ollama rm <model>`, LM Studio › My Models |
| WSL/Docker disks | compact, don't delete: `wsl --shutdown`, then `Optimize-VHD` or diskpart `compact vdisk` |
