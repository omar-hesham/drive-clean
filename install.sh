#!/usr/bin/env bash
# drive-analyzer — macOS / Linux installer.
# Installs once into ~/.agents/skills/drive-analyzer and symlinks it into every installed agent's
# skills folder (Claude Code, Codex, Gemini CLI, Antigravity, OpenCode, Cursor, Copilot, Kimi).
#   ./install.sh               install / update
#   ./install.sh --uninstall   remove
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash
set -euo pipefail
NAME=drive-analyzer
REPO="${DRIVE_ANALYZER_REPO:-https://github.com/omar-hesham/drive-clean.git}"
CANON="$HOME/.agents/skills/$NAME"
TARGETS=(".claude/skills" ".codex/skills" ".gemini/skills" ".gemini/config/skills" ".gemini/antigravity/skills"
         ".config/opencode/skills" ".cursor/skills" ".copilot/skills" ".kimi/skills")

agent_root() { case "$1" in .config/*) echo "$HOME/.config/opencode";; *) echo "$HOME/${1%%/*}";; esac; }

if [[ "${1:-}" == "--uninstall" ]]; then
  for t in "${TARGETS[@]}"; do [[ -L "$HOME/$t/$NAME" ]] && rm "$HOME/$t/$NAME" && echo "removed link  $HOME/$t/$NAME"; done
  rm -rf "$CANON" && echo "removed       $CANON"; exit 0
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -z "$SRC" || ! -f "$SRC/SKILL.md" ]]; then
  SRC="$(mktemp -d)/$NAME"; git clone --depth 1 "$REPO" "$SRC" >/dev/null
fi
mkdir -p "$(dirname "$CANON")"
if [[ "$(cd "$SRC" && pwd -P)" != "$(mkdir -p "$CANON" && cd "$CANON" && pwd -P)" ]]; then
  rm -rf "$CANON"; mkdir -p "$CANON"
  (cd "$SRC" && tar --exclude=.git --exclude=.github --exclude=tests --exclude=__pycache__ -cf - .) | (cd "$CANON" && tar xf -)
fi
echo "installed     $CANON"
for t in "${TARGETS[@]}"; do
  [[ -d "$(agent_root "$t")" ]] || continue
  mkdir -p "$HOME/$t"; dst="$HOME/$t/$NAME"
  if [[ -e "$dst" && ! -L "$dst" ]]; then mv "$dst" "$dst.bak-$(date +%Y%m%d%H%M%S)"; fi
  ln -sfn "$CANON" "$dst"; echo "linked        $dst"
done
command -v python3 >/dev/null && echo "python        $(python3 --version)" || echo "WARNING: python3 not found — install Python 3.8+"
echo; echo "Done. Restart your agents. Without an agent:  python3 \"$CANON/scripts/drive_analyzer.py\" analyze ~/Downloads --serve"
