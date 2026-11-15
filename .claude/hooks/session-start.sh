#!/bin/bash
# Claude Code on the web: install qasight[dev] into a project venv.
# The container's system pip (Debian-patched setuptools) can't build
# csscompressor, a source-only dependency of behavex, so never install there.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Commit as the maintainer, never as Claude: a Claude author or co-author puts
# Claude in GitHub's Contributors list (see CLAUDE.md, "Rules").
git config user.name "Ariel Even"
git config user.email "228741870+taltal-beep@users.noreply.github.com"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dev]"

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$CLAUDE_PROJECT_DIR/.venv\""
    echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi
