#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Project virtual environment is not installed. Run scripts/bootstrap-macos.sh first." >&2
  exit 1
fi

mkdir -p \
  "$RUNTIME_DIR/cache" \
  "$RUNTIME_DIR/config" \
  "$RUNTIME_DIR/data" \
  "$RUNTIME_DIR/home" \
  "$RUNTIME_DIR/pycache" \
  "$RUNTIME_DIR/temp"

export HOME="$RUNTIME_DIR/home"
export TMPDIR="$RUNTIME_DIR/temp"
export XDG_CACHE_HOME="$RUNTIME_DIR/cache"
export XDG_CONFIG_HOME="$RUNTIME_DIR/config"
export XDG_DATA_HOME="$RUNTIME_DIR/data"
export PYTHONPYCACHEPREFIX="$RUNTIME_DIR/pycache"

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" "$@"
