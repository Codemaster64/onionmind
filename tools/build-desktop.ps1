[CmdletBinding()]
param(
    [switch]$Check,
    [string]$PythonExecutable = ""
)

# This script intentionally works in Windows PowerShell 5.1 as well as pwsh.
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$VenvRoot = Join-Path $RepoRoot ".desktop-build-venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$DistRoot = Join-Path $RepoRoot "dist"

$PythonSources = @(
    "onionmind.py",
    "onionmind_desktop.py",
    "onionmind_desktop_core.py"
)
$RuntimeAssets = @(
    "dsh-onionmind-tor-search.js",
    "dsh-onionmind-tor.patch.yml",
    "onionmind.ico",
    "logo.svg",
    "logo-small.svg",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md"
)
$RequiredFiles = @(
    "pyproject.toml",
    "requirements-desktop.txt"
) + $PythonSources + $RuntimeAssets

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
    }
}

function Resolve-PythonLauncher {
    if ($PythonExecutable) {
        $Command = Get-Command $PythonExecutable -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
        return @($Command.Path)
    }

    $PyLauncher = Get-Command "py.exe" -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $PyLauncher) {
        return @($PyLauncher.Path, "-3")
    }

    $Python = Get-Command "python.exe" -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $Python) {
        return @($Python.Path)
    }

    throw "Python 3.10 or newer was not found. Install Python, or pass -PythonExecutable <path>."
}

foreach ($RelativePath in $RequiredFiles) {
    $AbsolutePath = Join-Path $RepoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $AbsolutePath -PathType Leaf)) {
        throw "Required desktop-build input is missing: $RelativePath"
    }
    if ((Get-Item -LiteralPath $AbsolutePath).Length -eq 0) {
        throw "Required desktop-build input is empty: $RelativePath"
    }
}

$BasePythonCommand = @(Resolve-PythonLauncher)
$BasePythonPath = $BasePythonCommand[0]
$BasePythonPrefix = @()
if ($BasePythonCommand.Count -gt 1) {
    $BasePythonPrefix = @($BasePythonCommand[1..($BasePythonCommand.Count - 1)])
}

$VersionCheck = @'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('Onionmind desktop requires Python 3.10 or newer')
print('Python ' + sys.version.split()[0])
'@
Invoke-CheckedCommand -FilePath $BasePythonPath -ArgumentList ($BasePythonPrefix + @("-c", $VersionCheck))

# Parse source without importing PySide6 and without creating __pycache__. This
# makes -Check safe to run on a clean checkout before dependencies are installed.
$SyntaxCheck = @'
import ast, pathlib, sys
for value in sys.argv[1:]:
    path = pathlib.Path(value)
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('Python syntax ok (' + str(len(sys.argv) - 1) + ' files)')
'@
$AbsolutePythonSources = @($PythonSources | ForEach-Object { Join-Path $RepoRoot $_ })
Invoke-CheckedCommand -FilePath $BasePythonPath -ArgumentList (
    $BasePythonPrefix + @("-c", $SyntaxCheck) + $AbsolutePythonSources
)

if ($Check) {
    Write-Host "Desktop build inputs are complete and syntactically valid."
    exit 0
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $VenvRoot) {
        throw "The existing .desktop-build-venv is incomplete. Move it aside and run this script again."
    }
    Write-Host "Creating isolated build environment at $VenvRoot"
    Invoke-CheckedCommand -FilePath $BasePythonPath -ArgumentList (
        $BasePythonPrefix + @("-m", "venv", $VenvRoot)
    )
}

Write-Host "Installing constrained desktop build dependencies"
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"
)
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "install", "--disable-pip-version-check",
    "--requirement", (Join-Path $RepoRoot "requirements-desktop.txt")
)
Write-Host "Resolved desktop compiler versions"
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "show", "PySide6-Essentials", "Nuitka"
)

if (-not (Test-Path -LiteralPath $DistRoot)) {
    $null = New-Item -ItemType Directory -Path $DistRoot
}

$EntryPoint = Join-Path $RepoRoot "onionmind_desktop.py"
$NuitkaArguments = @(
    "-m", "nuitka",
    "--mode=standalone",
    "--enable-plugin=pyside6",
    "--assume-yes-for-downloads",
    "--deployment",
    "--output-dir=$DistRoot",
    "--output-filename=Onionmind.exe",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$(Join-Path $RepoRoot 'onionmind.ico')",
    "--company-name=Onionmind",
    "--product-name=Onionmind",
    "--file-description=Onionmind local coding workbench",
    "--file-version=1.0.0",
    "--product-version=1.0.0",
    "--include-module=onionmind",
    "--include-module=onionmind_desktop_core",
    "--include-data-files=$(Join-Path $RepoRoot 'dsh-onionmind-tor-search.js')=dsh-onionmind-tor-search.js",
    "--include-data-files=$(Join-Path $RepoRoot 'dsh-onionmind-tor.patch.yml')=dsh-onionmind-tor.patch.yml",
    "--include-data-files=$(Join-Path $RepoRoot 'onionmind.ico')=onionmind.ico",
    "--include-data-files=$(Join-Path $RepoRoot 'logo.svg')=logo.svg",
    "--include-data-files=$(Join-Path $RepoRoot 'logo-small.svg')=logo-small.svg",
    "--include-data-files=$(Join-Path $RepoRoot 'LICENSE')=LICENSE",
    "--include-data-files=$(Join-Path $RepoRoot 'THIRD_PARTY_NOTICES.md')=THIRD_PARTY_NOTICES.md",
    $EntryPoint
)

Write-Host "Building the standalone Onionmind desktop bundle"
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList $NuitkaArguments

$ExpectedBundle = Join-Path $DistRoot "onionmind_desktop.dist"
$ExpectedExecutable = Join-Path $ExpectedBundle "Onionmind.exe"
if (-not (Test-Path -LiteralPath $ExpectedExecutable -PathType Leaf)) {
    $ExecutableCandidates = @(Get-ChildItem -LiteralPath $DistRoot -Filter "Onionmind.exe" -File -Recurse)
    if ($ExecutableCandidates.Count -ne 1) {
        throw "Nuitka completed, but exactly one dist/**/Onionmind.exe was not found."
    }
    $ExpectedExecutable = $ExecutableCandidates[0].FullName
    $ExpectedBundle = $ExecutableCandidates[0].Directory.FullName
}

foreach ($Asset in $RuntimeAssets) {
    $BundledAsset = Join-Path $ExpectedBundle $Asset
    if (-not (Test-Path -LiteralPath $BundledAsset -PathType Leaf)) {
        throw "The standalone bundle is missing runtime asset: $Asset"
    }
}

Write-Host "Onionmind desktop bundle ready: $ExpectedBundle"
Write-Host "Executable: $ExpectedExecutable"
