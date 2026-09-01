[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$AllowDirectNetwork,
    [switch]$Yes,
    [string]$PythonExecutable = "",
    [string]$Revision = ""
)

# This script intentionally works in Windows PowerShell 5.1 as well as pwsh.
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$VenvRoot = Join-Path $RepoRoot ".desktop-build-venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$DistRoot = Join-Path $RepoRoot "dist"
$RevisionFile = ".onionmind-source-revision"

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

# The revision marker is the bundle's update identity: the in-app updater
# compares it against the manifest published with a release, so every build
# must carry the exact commit it was compiled from (plus -dirty when local
# edits are in play - an honest marker beats a wrong one).
if (-not $Revision) { $Revision = $env:ONIONMIND_BUILD_REVISION }
$GitCommand = Get-Command "git.exe" -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $Revision) {
    if ($null -eq $GitCommand) {
        throw "No revision given. Pass -Revision <sha>, set ONIONMIND_BUILD_REVISION, or install git."
    }
    $Revision = (& $GitCommand.Path -C $RepoRoot rev-parse HEAD | Out-String).Trim()
}
if ($Revision -notmatch '^[0-9a-f]{7,40}(-dirty)?$') {
    throw "Revision '$Revision' does not look like a commit sha."
}
if ($null -ne $GitCommand -and $Revision -notmatch '-dirty$') {
    $DirtyStatus = (& $GitCommand.Path -C $RepoRoot status --porcelain 2>$null | Out-String)
    if ($DirtyStatus -and $DirtyStatus.Trim()) { $Revision = "$Revision-dirty" }
}
Write-Host "Building revision $Revision"

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

# The isolated environment is audited BEFORE pip is allowed to run: the
# constraints file is the single source of truth, so the audit parses it and
# compares every pin against what the venv actually has installed. Only a
# missing or out-of-range package can trigger the consent-gated repair below.
$DependencyAudit = @'
import importlib.metadata as metadata
import re
import sys

pins = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, _, specs = line.partition(">=")
    name = name.strip()
    if name:
        pins[name] = [spec.strip() for spec in specs.split(",") if spec.strip()]

def version_tuple(raw):
    match = re.match(r"^(\d+(?:\.\d+)*)", raw)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()

issues = []
for name, specs in pins.items():
    try:
        raw = metadata.version(name)
    except metadata.PackageNotFoundError:
        print(f"[MISSING] {name} -")
        issues.append(name)
        continue
    value = version_tuple(raw)
    accepted = True
    for spec in specs:
        if spec.startswith(">="):
            accepted = accepted and value >= version_tuple(spec[2:])
        elif spec.startswith("<"):
            accepted = accepted and value < version_tuple(spec[1:])
    print(f"[{'READY' if accepted else 'OUTDATED'}] {name} {raw}")
    if not accepted:
        issues.append(name)

try:
    import PySide6.QtWidgets
except Exception as error:
    print(f"[OUTDATED] Native desktop imports failed: {error}")
    issues.append("native desktop imports")

raise SystemExit(2 if issues else 0)
'@

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $VenvRoot) {
        throw "The existing .desktop-build-venv is incomplete. Move it aside and run this script again."
    }
    Write-Host "Creating isolated build environment at $VenvRoot"
    Invoke-CheckedCommand -FilePath $BasePythonPath -ArgumentList (
        $BasePythonPrefix + @("-m", "venv", $VenvRoot)
    )
}

Write-Host "Auditing the isolated build environment"
# powershell.exe 5.1 passes embedded double quotes to native commands unescaped,
# which corrupts an inline "-c" script (the audit source contains "), so run it
# from a file instead - the same detour AGENTS.md prescribes for git -m under 5.1.
$AuditScriptPath = Join-Path ([IO.Path]::GetTempPath()) "onionmind-audit-dependencies.py"
[IO.File]::WriteAllText($AuditScriptPath, $DependencyAudit)
$auditOutput = @(& $VenvPython $AuditScriptPath (Join-Path $RepoRoot "requirements-desktop.txt"))
if ($auditOutput) { $auditOutput | ForEach-Object { Write-Host "  $_" } }
$dependenciesReady = ($LASTEXITCODE -eq 0)

if ($dependenciesReady) {
    Write-Host "Desktop build dependencies already satisfy requirements-desktop.txt; pip will not run"
} else {
    Write-Host ""
    Write-Host "Direct-network dependency repair plan" -ForegroundColor Yellow
    Write-Host "  pip -m install --requirement requirements-desktop.txt will contact the"
    Write-Host "  package index configured for the isolated venv, and only for the"
    Write-Host "  missing or out-of-range packages listed above."
    Write-Host "No Tor or model service is started, and model weights are not changed."
    if (-not $AllowDirectNetwork) {
        [Console]::Error.WriteLine("Refusing direct network access. Re-run with -AllowDirectNetwork after reviewing the plan.")
        exit 4
    }
    if (-not $Yes) {
        $answer = Read-Host "Proceed with these direct-network requests? [y/N]"
        if ($answer -notmatch "^(?i:y|yes)$") {
            Write-Host "Canceled. No network request was started."
            exit 3
        }
    }
    Write-Host "Repairing constrained desktop build dependencies"
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", (Join-Path $RepoRoot "requirements-desktop.txt")
    )
    Write-Host "Re-auditing the isolated build environment"
    $reauditOutput = @(& $VenvPython $AuditScriptPath (Join-Path $RepoRoot "requirements-desktop.txt"))
    if ($reauditOutput) { $reauditOutput | ForEach-Object { Write-Host "  $_" } }
    if ($LASTEXITCODE -ne 0) {
        throw "The dependency repair did not bring the venv inside the constrained ranges."
    }
}

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

$RevisionMarker = Join-Path $ExpectedBundle $RevisionFile
[IO.File]::WriteAllText($RevisionMarker, $Revision + "`n", (New-Object System.Text.UTF8Encoding($false)))
$WrittenRevision = ([IO.File]::ReadAllText($RevisionMarker)).Trim()
if ($WrittenRevision -ne $Revision) {
    throw "The revision marker could not be written into the bundle."
}

Write-Host "Onionmind desktop bundle ready: $ExpectedBundle"
Write-Host "Executable: $ExpectedExecutable"
Write-Host "Update identity: $RevisionMarker ($Revision)"
