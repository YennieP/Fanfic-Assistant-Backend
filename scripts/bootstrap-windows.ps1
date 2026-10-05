$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$UvVersion = "0.12.23"
$UvArchive = "uv-x86_64-pc-windows-msvc.zip"
$UvSha256 = "75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90"
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvArchive"
$DownloadDir = Join-Path $RuntimeDir "downloads"
$ExtractDir = Join-Path $RuntimeDir "extracted\uv-x86_64-pc-windows-msvc"
$ArchivePath = Join-Path $DownloadDir $UvArchive
$UvBin = Join-Path $RuntimeDir "bin\uv.exe"

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "This bootstrap is pinned for 64-bit x86 Windows only."
}

New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "bin"), $DownloadDir, $ExtractDir, (Join-Path $RuntimeDir "home"), (Join-Path $RuntimeDir "temp") | Out-Null
$env:HOME = Join-Path $RuntimeDir "home"
$env:TEMP = Join-Path $RuntimeDir "temp"
$env:TMP = Join-Path $RuntimeDir "temp"
$env:UV_NO_MODIFY_PATH = "1"

if (-not (Test-Path -PathType Leaf $UvBin)) {
    Invoke-WebRequest -UseBasicParsing -Uri $UvUrl -OutFile $ArchivePath
    $ActualSha256 = (Get-FileHash -Algorithm SHA256 -Path $ArchivePath).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $UvSha256) {
        throw "uv archive checksum mismatch: expected $UvSha256, got $ActualSha256"
    }

    Expand-Archive -Path $ArchivePath -DestinationPath $ExtractDir -Force
    Copy-Item -Force -Path (Join-Path $ExtractDir "uv.exe") -Destination $UvBin
}

& (Join-Path $PSScriptRoot "uv-project.ps1") python install 3.12
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if ((Test-Path -PathType Container $VenvDir) -and -not (Test-Path -PathType Leaf $VenvPython)) {
    throw "Existing .venv is not a Windows environment. Delete it explicitly, then rerun this script."
}
if (-not (Test-Path -PathType Leaf $VenvPython)) {
    & (Join-Path $PSScriptRoot "uv-project.ps1") venv --python 3.12 $VenvDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& (Join-Path $PSScriptRoot "uv-project.ps1") sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$EnvPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvPath)) {
    & (Join-Path $PSScriptRoot "python-project.ps1") (Join-Path $PSScriptRoot "init-local-env.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Project-local runtime is ready."
& (Join-Path $PSScriptRoot "python-project.ps1") --version
exit $LASTEXITCODE
