$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDir = Join-Path $ProjectRoot ".runtime"

$RuntimePaths = @(
    (Join-Path $RuntimeDir "bin"),
    (Join-Path $RuntimeDir "cache\python"),
    (Join-Path $RuntimeDir "config"),
    (Join-Path $RuntimeDir "data"),
    (Join-Path $RuntimeDir "home"),
    (Join-Path $RuntimeDir "python"),
    (Join-Path $RuntimeDir "python-bin"),
    (Join-Path $RuntimeDir "pycache"),
    (Join-Path $RuntimeDir "temp")
)
foreach ($Path in $RuntimePaths) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

$env:HOME = Join-Path $RuntimeDir "home"
$env:TEMP = Join-Path $RuntimeDir "temp"
$env:TMP = Join-Path $RuntimeDir "temp"
$env:TMPDIR = Join-Path $RuntimeDir "temp"
$env:XDG_CACHE_HOME = Join-Path $RuntimeDir "cache"
$env:XDG_CONFIG_HOME = Join-Path $RuntimeDir "config"
$env:XDG_DATA_HOME = Join-Path $RuntimeDir "data"
$env:PIP_CACHE_DIR = Join-Path $RuntimeDir "cache\pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $RuntimeDir "pycache"

$env:UV_INSTALL_DIR = Join-Path $RuntimeDir "bin"
$env:UV_CACHE_DIR = Join-Path $RuntimeDir "cache\uv"
$env:UV_PYTHON_CACHE_DIR = Join-Path $RuntimeDir "cache\python"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir "python"
$env:UV_PYTHON_BIN_DIR = Join-Path $RuntimeDir "python-bin"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $ProjectRoot ".venv"
$env:UV_CONFIG_FILE = Join-Path $ProjectRoot "uv.toml"
$env:UV_NO_MODIFY_PATH = "1"
$env:UV_MANAGED_PYTHON = "1"

$UvBin = Join-Path $RuntimeDir "bin\uv.exe"
if (-not (Test-Path -PathType Leaf $UvBin)) {
    throw "Project-local uv is not installed. Run scripts\bootstrap-windows.ps1 first."
}

Push-Location $ProjectRoot
try {
    & $UvBin @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
