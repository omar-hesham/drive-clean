# drive-analyzer — instructions for AI agents

This repository is an Agent Skill. When the user asks to analyze a drive or folder, free up disk space,
find what to delete or keep, or clean up a disk (Arabic: «حلل الدرايف»، «الهارد اتملى»، «أمسح إيه»),
read and follow [`SKILL.md`](SKILL.md). It has the whole workflow.

Short version:
1. `python scripts/drive_analyzer.py analyze <ROOT> --title "<title>"` (run it in the background; output
   goes to `~/drive-analyzer-reports/<drive>-<date>/`; `done.flag` appears when it's finished).
2. Read `summary.json` and give the user a short summary: the four buckets, the biggest wins, and
   questions about `unknown` items.
3. `python scripts/drive_analyzer.py serve --db <out>/inventory.sqlite --open` opens the local action
   panel (Recycle Bin / permanent delete / open in Explorer).
4. Never delete anything yourself without explicit approval. See `references/cleanup.md`.

Development: Python 3.8+ standard library only. Run the tests with `python -m unittest discover tests`.
