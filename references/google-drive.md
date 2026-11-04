# Google Drive

The connector (`mcp__Google_Drive__search_files`) returns per file: `id, title, mimeType, fileSize,
fileExtension, parentId, createdTime, modifiedTime, viewedByMeTime, owner, trashed`. No checksum, so
duplicates are "probable" (same name + size), never hash-verified.

## Collect
Results come into context, so be economical: `excludeContentSnippets: true` and a large `pageSize`
(try 1000; the API caps it). Run these queries, page each with `pageToken` until empty, and
de-duplicate by `id` (pages can overlap; the importer also drops repeats):

1. `owner = 'me' and trashed = false` — everything that counts against quota
2. `trashed = true` — the Trash still uses quota until it's emptied
3. Folders, for paths: `mimeType = 'application/vnd.google-apps.folder' and owner = 'me'`
   → build `id → (title, parentId)` and resolve each file's path by walking up parents (stop at
   unknown ids; prefix those with `…/`).

If the account has tens of thousands of files, don't page everything: collect (2), then the heavy
types (`mimeType contains 'video/'`, `'application/zip'`, `'application/x-rar'`, `'application/octet-stream'`,
`'image/'`), and say in the report that small documents were sampled, not counted.

Write one JSON object per line to `gdrive.jsonl` in the workspace, appending one page at a time
with the fields exactly as returned:
```json
{"id":"18Kc…","path":"Thesis/Defense/Thesis Defense DBA.mp4","size":1989879496,"mtime":"2026-09-07T21:29:41.668Z",
 "ctime":"2026-09-07T21:27:52.559Z","viewed":"2026-09-07T21:37:30.437Z","mime":"video/mp4","owned":true,"trashed":false,"ext":"mp4"}
```
`owned` = (`owner` equals the user's email). Google-native files (`application/vnd.google-apps.*`) have no
`fileSize`; write `0`.

## Analyze
```
python drive_analyzer.py import-gdrive gdrive.jsonl --db g.sqlite
python drive_analyzer.py dupes --db g.sqlite        # name+size, probable only
python drive_analyzer.py classify --db g.sqlite
python drive_analyzer.py report --db g.sqlite --out gout --title "تحليل Google Drive"
```
Drive-specific rules: Trash → delete (system: empty Trash); not owned → keep (doesn't use your quota);
Google Docs/Sheets → keep (≈0 bytes); big files not opened for 2 years → maybe. Everything else
(installers, ISOs, archives, movie rips, cracked software…) uses the same rules as a disk.

The connector doesn't report total quota. If the user wants "used vs. free", ask them to read it from
drive.google.com/settings/storage. Remember that Gmail and Photos share the same quota.

## Execute (after approval)
`plan --db g.sqlite --out gplan.json --buckets delete` → each target carries `xid` = Drive file id.
Call `trash_file(fileId)` per id, a few at a time, and report progress. Files stay recoverable from
Trash for 30 days. Quota is freed only when the Trash is emptied, which the user does in the Drive UI.
Never trash files the user doesn't own.
