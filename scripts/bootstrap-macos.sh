#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
UV_VERSION="0.12.23"
UV_ARCHIVE="uv-aarch64-apple-darwin.tar.gz"
UV_SHA256="50487ae565ccd96e499056b4674d438f4c53170202617b4c759defe0c6a1b544"
UV_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ARCHIVE"
DOWNLOAD_DIR="$RUNTIME_DIR/downloads"
EXTRACT_DIR="$RUNTIME_DIR/extracted"
ARCHIVE_PATH="$DOWNLOAD_DIR/$UV_ARCHIVE"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This bootstrap is pinned for Apple Silicon macOS only." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR/bin" "$DOWNLOAD_DIR" "$EXTRACT_DIR" "$RUNTIME_DIR/temp"
export HOME="$RUNTIME_DIR/home"
export TMPDIR="$RUNTIME_DIR/temp"
export UV_NO_MODIFY_PATH=1

if [[ ! -x "$RUNTIME_DIR/bin/uv" ]]; then
  curl -q --fail --location --silent --show-error "$UV_URL" --output "$ARCHIVE_PATH"
  printf '%s  %s\n' "$UV_SHA256" "$ARCHIVE_PATH" | shasum -a 256 --check
  tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"
  install -m 0755 "$EXTRACT_DIR/uv-aarch64-apple-darwin/uv" "$RUNTIME_DIR/bin/uv"
fi

"$PROJECT_ROOT/scripts/uv-project.sh" python install 3.12

if [[ -d "$PROJECT_ROOT/.venv" && ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "Existing .venv is not a macOS environment. Delete it explicitly, then rerun this script." >&2
  exit 1
fi
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  "$PROJECT_ROOT/scripts/uv-project.sh" venv --python 3.12 "$PROJECT_ROOT/.venv"
fi

"$PROJECT_ROOT/scripts/uv-project.sh" sync --frozen

if [[ ! -e "$PROJECT_ROOT/.env" ]]; then
  "$PROJECT_ROOT/scripts/python-project.sh" "$PROJECT_ROOT/scripts/init-local-env.py"
fi

echo "Project-local runtime is ready."
"$PROJECT_ROOT/scripts/python-project.sh" --version
