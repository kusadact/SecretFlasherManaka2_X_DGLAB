param(
    [string]$Python = "python",
    [string]$Proxy = ""
)

$ErrorActionPreference = "Stop"

$ClientDir = $PSScriptRoot
$VenvDir = Join-Path $ClientDir ".venv-build"
$BuildDir = Join-Path $ClientDir "build"
$DistDir = Join-Path $ClientDir "dist"
$EntryPoint = Join-Path $ClientDir "client_gui.py"
$Requirements = Join-Path $ClientDir "requirements.txt"
$BuildRequirements = Join-Path $ClientDir "requirements-build.txt"
$ExeName = "Secret Flasher Manaka Vibrator Coyote Client"

if (!(Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install 64-bit Python 3.10 or newer, or pass -Python with its command or path."
}

foreach ($path in @($EntryPoint, $Requirements, $BuildRequirements)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required build input not found: $path"
    }
}

if (!(Test-Path -LiteralPath $VenvDir -PathType Container)) {
    Write-Host "Creating build environment: $VenvDir"
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python build environment."
    }
}

$BuildPython = Join-Path $VenvDir "Scripts\python.exe"
if (!(Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "Build environment is incomplete: $BuildPython"
}

Write-Host "Installing locked client and build dependencies..."
$PipArguments = @(
    "-m", "pip", "install",
    "--disable-pip-version-check"
)
if (![string]::IsNullOrWhiteSpace($Proxy)) {
    if ($Proxy -notmatch "^[a-zA-Z][a-zA-Z0-9+.-]*://") {
        $Proxy = "http://$Proxy"
    }
    Write-Host "Using package proxy: $Proxy"
    $PipArguments += @("--proxy", $Proxy)
}
$PipArguments += @("-r", $Requirements, "-r", $BuildRequirements)
& $BuildPython @PipArguments
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install client build dependencies."
}

Write-Host "Building Windows client..."
& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $ExeName `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $BuildDir `
    $EntryPoint
if ($LASTEXITCODE -ne 0) {
    throw "Client build failed with exit code $LASTEXITCODE"
}

$Output = Join-Path $DistDir "$ExeName.exe"
if (!(Test-Path -LiteralPath $Output -PathType Leaf)) {
    throw "Client build completed but the EXE was not found: $Output"
}

Write-Host "Built: $Output"
