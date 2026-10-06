$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$PythonBin = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -PathType Leaf $PythonBin)) {
    throw "Project virtual environment is not installed. Run scripts\bootstrap-windows.ps1 first."
}

$RuntimePaths = @(
    (Join-Path $RuntimeDir "cache"),
    (Join-Path $RuntimeDir "config"),
    (Join-Path $RuntimeDir "data"),
    (Join-Path $RuntimeDir "home"),
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
$env:PYTHONPYCACHEPREFIX = Join-Path $RuntimeDir "pycache"

Push-Location $ProjectRoot
try {
    & $PythonBin @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
