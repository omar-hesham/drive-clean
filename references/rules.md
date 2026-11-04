# How classification decides (and how to change it)

## Order of decisions for each file
1. **Folder verdicts, top-down.** Walking from the root, the first folder that matches a *terminal*
   rule (e.g. `node_modules`, `AppData\Local\Temp`, `$Recycle.Bin`, a cracked-software folder, a
   portable app) decides for its whole subtree, and the report shows that folder as one item.
   *Zone* rules (Windows, Program Files, AppData, steamapps) set a default that deeper rules can refine.
   *Protected* zones (Windows, Program Files, ProgramData, AppData\Local\Packages) block file-level
   rules entirely, so nothing inside them is ever marked delete, except crash dumps.
2. **Google Drive rules** (Trash, not-owned, native docs, stale big files).
3. **File rules** by name/extension/age/location: OS junk, Office lock files, temp, partial downloads
   (browser and yt-dlp), crash dumps, old logs, `.bak`, cracked installers, installers (old ones in a
   download folder → delete), foreign installers, disk images (WSL/Docker disks → keep), AI models,
   archives (extracted next to themselves → maybe), YouTube rips, downloaded movies/episodes, old
   items in Downloads, big old videos.
4. **Zone default** if any.
5. **Type default**: documents/images/video/audio/design/code/fonts → keep; an unknown type → unknown.
6. **Duplicates** override keep/maybe/unknown: hash-verified copies → delete (the "original" kept is the one
   *not* in Downloads/Temp or named "copy"/"(1)", then the oldest, then the shortest path). Name+size
   matches → maybe.
7. **Sync folders** (OneDrive/Google Drive/Dropbox…): the reason gets a ⚠️ cloud-deletion warning.
8. **Overrides** (`overrides.json`) win over everything.

"Download folder" means any `Downloads` folder, plus any folder that holds browser partial
downloads (`.crdownload`, `Unconfirmed *`). That flag applies to that folder's own files, not its
whole subtree.

## Thresholds (top of `rules.py`)
| constant | default | effect |
|---|---|---|
| INACTIVE_PROJECT | 30 days | node_modules/venv of a project untouched this long → delete, else maybe |
| OLD_INSTALLER | 30 days | installers in a download folder older than this → delete |
| OLD_LOG | 30 days | `.log` older → delete |
| OLD_DOWNLOAD | 1 year | anything in a download folder older → maybe |
| OLD_ARCHIVE / OLD_BIG_MEDIA | 2 years | big archives/videos untouched → maybe |
| BIG_FILE_ITEM | 100 MB | files above this get their own row in the report; smaller ones are grouped per folder |

## Principles behind the rules
- **Delete only regenerable or redundant things.** Caches, temp files, build outputs, partial downloads,
  re-downloadable installers, verified copies. Each delete reason says how the thing comes back.
- **Personal content is never auto-deleted.** Photos, documents and personal videos stay keep. A rule may
  lift them to maybe only on strong evidence: a movie-rip name pattern, a YouTube id in the name, or 2+
  years untouched and ≥ 1 GB.
- **System pieces go through Windows tools.** `$Recycle.Bin`, `Windows.old`, `SoftwareDistribution`,
  `Windows\Temp`, `hiberfil.sys`, `WinSxS`/`Installer`: the action is `system` and the reason names the tool.
- **Installed software goes through Uninstall.** Program Files, Steam games and portable apps get
  `uninstall`/`unknown`, never a file move. Moving them by hand breaks registrations.
- **Tool-owned modules stay.** `node_modules`/venvs inside hidden tool folders (`.vscode`, `.codex`…),
  AppData, or without a `package.json` belong to installed tools → keep.
- **Cracked software / activators → maybe, with a security note.** They're a common malware vector and
  usually outdated. State it factually, without moralizing.

## Adding a rule
Add a pattern to `dir_rules` (folders) or `file_rules` (files) in `scripts/rules.py`, returning
`V(bucket, rule_id, reason_ar, action)`. Then re-run `classify` + `report` on an existing inventory to
see the effect; no rescan is needed. When the user corrects a classification and the cause is general
(not just their own folder names), fix the rule and tell them. For one-off decisions about their own
folders, use `overrides.json`.
