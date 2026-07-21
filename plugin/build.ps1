param(
    [Parameter(Mandatory = $false)]
    [string]$GameDir = "",
    [string]$BepInExDir = "",
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }
    return [System.IO.Path]::GetFullPath($PathValue)
}

if ([string]::IsNullOrWhiteSpace($GameDir)) {
    $GameDir = $env:SECRET_FLASHER_MANAKA_GAME_DIR
}

if ([string]::IsNullOrWhiteSpace($GameDir)) {
    throw 'GameDir is required. Example: .\build.ps1 -GameDir "D:\SecretFlasherManaka v1.1.3"'
}

$GameDir = Resolve-FullPath $GameDir
if (!(Test-Path -LiteralPath $GameDir -PathType Container)) {
    throw "Game directory not found: $GameDir"
}

if ([string]::IsNullOrWhiteSpace($BepInExDir)) {
    $BepInExDir = Join-Path $GameDir "BepInEx"
}
$BepInExDir = Resolve-FullPath $BepInExDir

if (!(Test-Path -LiteralPath $BepInExDir -PathType Container)) {
    throw "BepInEx directory not found: $BepInExDir"
}

$DotnetDir = Join-Path $GameDir "dotnet"
$CoreDir = Join-Path $BepInExDir "core"
$InteropDir = Join-Path $BepInExDir "interop"
$UnityLibDir = Join-Path $BepInExDir "unity-libs"

foreach ($path in @($DotnetDir, $CoreDir, $InteropDir, $UnityLibDir)) {
    if (!(Test-Path -LiteralPath $path -PathType Container)) {
        throw "Required game dependency directory not found: $path"
    }
}

$Project = Join-Path $PSScriptRoot "SecretFlasherManakaCoyoteLink.csproj"
if (!(Test-Path -LiteralPath $Project -PathType Leaf)) {
    throw "Project file not found: $Project"
}

if (!(Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "dotnet SDK was not found. Install the .NET SDK before building the plugin."
}

Write-Host "Building plugin against: $GameDir"

$arguments = @(
    "build",
    $Project,
    "--configuration", $Configuration,
    "--nologo",
    "-p:GameDir=$GameDir",
    "-p:BepInExDir=$BepInExDir",
    "-p:DotnetDir=$DotnetDir",
    "-p:CoreDir=$CoreDir",
    "-p:InteropDir=$InteropDir",
    "-p:UnityLibDir=$UnityLibDir"
)

& dotnet @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Plugin build failed with exit code $LASTEXITCODE"
}

$output = Join-Path $PSScriptRoot "dist\SecretFlasherManakaCoyoteLink.dll"
Write-Host "Built: $output"
