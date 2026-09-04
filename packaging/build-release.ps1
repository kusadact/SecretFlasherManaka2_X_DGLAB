param(
    [string]$GameDir = "",
    [ValidatePattern("^\d+\.\d+\.\d+$")]
    [string]$Version = "0.2.0",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($GameDir)) {
    $GameDir = $env:SECRET_FLASHER_MANAKA_GAME_DIR
}
if ([string]::IsNullOrWhiteSpace($GameDir)) {
    throw 'GameDir is required. Example: .\packaging\build-release.ps1 -GameDir "D:\SecretFlasherManaka v1.1.3"'
}

$GameDir = [System.IO.Path]::GetFullPath($GameDir)
if (!(Test-Path -LiteralPath $GameDir -PathType Container)) {
    throw "Game directory not found: $GameDir"
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "release"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

$PluginDll = Join-Path $RepoRoot "plugin\dist\SecretFlasherManakaCoyoteLink.dll"
$ClientExe = Join-Path $RepoRoot "client\dist\Secret Flasher Manaka Vibrator Coyote Client.exe"
$StagingDir = Join-Path $RepoRoot ".build\release-staging"
$ArchivePath = Join-Path $OutputDir "SecretFlasherManaka2_X_DGLAB v$Version.zip"

function Require-File([string]$PathValue) {
    if (!(Test-Path -LiteralPath $PathValue -PathType Leaf)) {
        throw "Required file not found: $PathValue"
    }
}

function Require-Directory([string]$PathValue) {
    if (!(Test-Path -LiteralPath $PathValue -PathType Container)) {
        throw "Required directory not found: $PathValue"
    }
}

function Copy-ReleaseFile([string]$Source, [string]$RelativeDestination) {
    $destination = Join-Path $StagingDir $RelativeDestination
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

function Copy-ReleaseDirectory([string]$Source, [string]$RelativeDestination) {
    $destination = Join-Path $StagingDir $RelativeDestination
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $parent -Recurse -Force
}

$gameFiles = @(
    (Join-Path $GameDir ".doorstop_version"),
    (Join-Path $GameDir "doorstop_config.ini"),
    (Join-Path $GameDir "winhttp.dll")
)
$gameDirectories = @(
    (Join-Path $GameDir "BepInEx\core"),
    (Join-Path $GameDir "dotnet")
)

Require-File $PluginDll
Require-File $ClientExe
Require-File (Join-Path $RepoRoot "README.md")
Require-File (Join-Path $RepoRoot "LICENSE.md")
Require-File (Join-Path $RepoRoot "extra\start-client.bat")
Require-File (Join-Path $RepoRoot "extra\start-game-and-client.bat")
foreach ($path in $gameFiles) { Require-File $path }
foreach ($path in $gameDirectories) { Require-Directory $path }

if (Test-Path -LiteralPath $StagingDir) {
    Remove-Item -LiteralPath $StagingDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingDir "BepInEx\patchers") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingDir "BepInEx\plugins") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StagingDir "client") | Out-Null

Copy-ReleaseFile (Join-Path $GameDir ".doorstop_version") ".doorstop_version"
Copy-ReleaseFile (Join-Path $GameDir "doorstop_config.ini") "doorstop_config.ini"
Copy-ReleaseFile (Join-Path $GameDir "winhttp.dll") "winhttp.dll"
Copy-ReleaseDirectory (Join-Path $GameDir "BepInEx\core") "BepInEx\core"
Copy-ReleaseDirectory (Join-Path $GameDir "dotnet") "dotnet"
Copy-ReleaseFile $PluginDll "BepInEx\plugins\SecretFlasherManakaCoyoteLink.dll"
Copy-ReleaseFile $ClientExe "client\Secret Flasher Manaka Vibrator Coyote Client.exe"
Copy-ReleaseFile (Join-Path $RepoRoot "README.md") "README.md"
Copy-ReleaseFile (Join-Path $RepoRoot "LICENSE.md") "LICENSE.md"
Copy-ReleaseFile (Join-Path $RepoRoot "extra\start-client.bat") "start-client.bat"
Copy-ReleaseFile (Join-Path $RepoRoot "extra\start-game-and-client.bat") "start-game-and-client.bat"

$changelog = Join-Path $GameDir "changelog.txt"
if (Test-Path -LiteralPath $changelog -PathType Leaf) {
    Copy-ReleaseFile $changelog "changelog.txt"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
if (Test-Path -LiteralPath $ArchivePath) {
    Remove-Item -LiteralPath $ArchivePath -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $StagingDir,
    $ArchivePath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false
)

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath
Write-Host "Release ZIP: $ArchivePath"
Write-Host "SHA256: $($hash.Hash)"
