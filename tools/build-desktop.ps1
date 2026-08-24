[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$AllowDirectNetwork,
    [switch]$Yes,
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
    "onionmind-bootstrap.ps1",
    "onionmind-bootstrap.cmd",
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

$DependencyAudit = @'
import importlib.metadata as metadata
import re

requirements = {
    'PySide6-Essentials': lambda value: (6, 11) <= value < (6, 12),
    'requests': lambda value: (2, 32) <= value < (3,),
    'PySocks': lambda value: (1, 7) <= value < (2,),
    'Nuitka': lambda value: (4, 1) <= value < (5,),
    'ordered-set': lambda value: (4, 1) <= value < (5,),
    'zstandard': lambda value: (0, 23) <= value < (1,),
}

issues = []
for name, accepted in requirements.items():
    try:
        raw = metadata.version(name)
    except metadata.PackageNotFoundError:
        issues.append((name, 'MISSING', '-'))
        continue
    match = re.match(r'^(\d+(?:\.\d+)*)', raw)
    value = tuple(int(part) for part in match.group(1).split('.')) if match else ()
    status = 'READY' if value and accepted(value) else 'OUTDATED'
    print(f'[{status}] {name} {raw}')
    if status != 'READY':
        issues.append((name, status, raw))
for name, status, raw in issues:
    if status == 'MISSING':
        print(f'[{status}] {name} {raw}')
raise SystemExit(2 if issues else 0)
'@

$DependenciesReady = $false
if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & $VenvPython -c $DependencyAudit
    $DependenciesReady = ($LASTEXITCODE -eq 0)
} else {
    Write-Host "[MISSING] isolated desktop build environment: $VenvRoot"
}

if (-not $DependenciesReady) {
    Write-Host ""
    Write-Host "Direct-network build dependency plan" -ForegroundColor Yellow
    Write-Host "  Install only packages missing or outside requirements-desktop.txt"
    Write-Host "  Destination: $VenvRoot"
    Write-Host "  pip may contact its configured package index directly"
    if (-not $AllowDirectNetwork) {
        throw "Build dependencies need repair. Re-run with -AllowDirectNetwork after reviewing the plan."
    }
    if (-not $Yes) {
        $Answer = Read-Host "Proceed with the disclosed build dependency operation? [y/N]"
        if ($Answer -notmatch '^(?i:y|yes)$') {
            throw "Canceled. No build dependency network operation was started."
        }
    }
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        Write-Host "Creating isolated build environment at $VenvRoot"
        Invoke-CheckedCommand -FilePath $BasePythonPath -ArgumentList (
            $BasePythonPrefix + @("-m", "venv", $VenvRoot)
        )
    }
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", (Join-Path $RepoRoot "requirements-desktop.txt")
    )
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @("-c", $DependencyAudit)
} else {
    Write-Host "Desktop build dependencies already satisfy the constrained ranges; pip was not run."
}
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
    "--deployment",
    "--output-dir=$DistRoot",
    "--output-filename=Onionmind.exe",
    # Python 3.13/Nuitka can crash before Qt starts when the standard handles
    # are removed entirely. "hide" keeps valid handles without a visible console.
    "--windows-console-mode=hide",
    "--windows-icon-from-ico=$(Join-Path $RepoRoot 'onionmind.ico')",
    "--company-name=Onionmind",
    "--product-name=Onionmind",
    "--file-description=Onionmind local coding workbench",
    "--file-version=1.4.0",
    "--product-version=1.4.0",
    "--include-module=onionmind",
    "--include-module=onionmind_desktop_core",
    "--include-data-files=$(Join-Path $RepoRoot 'dsh-onionmind-tor-search.js')=dsh-onionmind-tor-search.js",
    "--include-data-files=$(Join-Path $RepoRoot 'dsh-onionmind-tor.patch.yml')=dsh-onionmind-tor.patch.yml",
    "--include-data-files=$(Join-Path $RepoRoot 'onionmind-bootstrap.ps1')=onionmind-bootstrap.ps1",
    "--include-data-files=$(Join-Path $RepoRoot 'onionmind-bootstrap.cmd')=onionmind-bootstrap.cmd",
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

$ZipPath = Join-Path $DistRoot "Onionmind-Windows-x64.zip"
$ZipScript = @'
import os
import pathlib
import stat
import sys
import zipfile

source = pathlib.Path(sys.argv[1]).resolve()
destination = pathlib.Path(sys.argv[2]).resolve()
temporary = destination.with_suffix(destination.suffix + '.tmp')
prefix = 'Onionmind-Windows-x64'

if temporary.exists():
    temporary.unlink()

with zipfile.ZipFile(
    temporary,
    mode='w',
    compression=zipfile.ZIP_DEFLATED,
    compresslevel=9,
) as archive:
    for path in sorted((value for value in source.rglob('*') if value.is_file()), key=lambda value: value.relative_to(source).as_posix()):
        relative = path.relative_to(source).as_posix()
        info = zipfile.ZipInfo(f'{prefix}/{relative}', date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        with path.open('rb') as stream:
            archive.writestr(info, stream.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

os.replace(temporary, destination)
print(destination)
'@

Write-Host "Creating deterministic portable archive"
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-c", $ZipScript, $ExpectedBundle, $ZipPath
)
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Portable archive was not created: $ZipPath"
}

Write-Host "Onionmind desktop bundle ready: $ExpectedBundle"
Write-Host "Executable: $ExpectedExecutable"
Write-Host "Portable archive: $ZipPath"
