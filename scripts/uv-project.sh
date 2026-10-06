#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"

mkdir -p \
  "$RUNTIME_DIR/bin" \
  "$RUNTIME_DIR/cache/python" \
  "$RUNTIME_DIR/config" \
  "$RUNTIME_DIR/data" \
  "$RUNTIME_DIR/home" \
  "$RUNTIME_DIR/python" \
  "$RUNTIME_DIR/python-bin" \
  "$RUNTIME_DIR/pycache" \
  "$RUNTIME_DIR/temp"

export HOME="$RUNTIME_DIR/home"
export TMPDIR="$RUNTIME_DIR/temp"
export XDG_CACHE_HOME="$RUNTIME_DIR/cache"
export XDG_CONFIG_HOME="$RUNTIME_DIR/config"
export XDG_DATA_HOME="$RUNTIME_DIR/data"
export PIP_CACHE_DIR="$RUNTIME_DIR/cache/pip"
export PYTHONPYCACHEPREFIX="$RUNTIME_DIR/pycache"

export UV_INSTALL_DIR="$RUNTIME_DIR/bin"
export UV_CACHE_DIR="$RUNTIME_DIR/cache/uv"
export UV_PYTHON_CACHE_DIR="$RUNTIME_DIR/cache/python"
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
export UV_PYTHON_BIN_DIR="$RUNTIME_DIR/python-bin"
export UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv"
export UV_CONFIG_FILE="$PROJECT_ROOT/uv.toml"
export UV_NO_MODIFY_PATH=1
export UV_MANAGED_PYTHON=1

UV_BIN="$RUNTIME_DIR/bin/uv"
if [[ ! -x "$UV_BIN" ]]; then
  echo "Project-local uv is not installed. Run scripts/bootstrap-macos.sh first." >&2
  exit 1
fi

cd "$PROJECT_ROOT"
exec "$UV_BIN" "$@"
