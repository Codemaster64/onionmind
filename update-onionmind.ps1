param([string]$InstallDir = "$env:LOCALAPPDATA\qwen")

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$revision = [string](Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/Codemaster64/onionmind/commits/main').sha
if ($revision -notmatch '^[0-9a-fA-F]{40}$') { throw 'Could not resolve a fixed update revision.' }
$base = "https://raw.githubusercontent.com/Codemaster64/onionmind/$revision"
$files = @(
  'onionmind.py',
  'onionmind_desktop_core.py',
  'onionmind_desktop.py',
  'dsh-onionmind-tor-search.js',
  'dsh-onionmind-tor.patch.yml'
)

[IO.Directory]::CreateDirectory($InstallDir) | Out-Null
$old = Join-Path $InstallDir 'onionmind.py'
$model = 'inferno'
if (Test-Path -LiteralPath $old -PathType Leaf) {
  $match = Select-String -LiteralPath $old -Pattern '^MODEL\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($match) { $model = $match.Matches[0].Groups[1].Value }
}

$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command py -CommandType Application -ErrorAction SilentlyContinue }
$py = if ($pythonCommand) { $pythonCommand.Source } else { $null }
if (-not $py) { throw 'Python 3 is required to validate and update Onionmind.' }

$stage = Join-Path ([IO.Path]::GetTempPath()) ("onionmind-update-" + [guid]::NewGuid().ToString('N'))
$prepared = Join-Path $InstallDir (".onionmind-update-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($stage) | Out-Null
[IO.Directory]::CreateDirectory($prepared) | Out-Null

function Remove-VerifiedUpdateDirectory {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Parent,
    [Parameter(Mandatory=$true)][string]$Prefix
  )
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $full = [IO.Path]::GetFullPath($Path)
  $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  $leaf = Split-Path -Leaf $full
  if (-not $full.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase) -or
      -not $leaf.StartsWith($Prefix, [StringComparison]::Ordinal)) {
    throw "Refusing to remove unexpected update directory: $full"
  }
  Remove-Item -LiteralPath $full -Recurse -Force
}

$swapped = $false
try {
  foreach ($name in $files) {
    Invoke-WebRequest -UseBasicParsing "$base/$name" -OutFile (Join-Path $stage $name)
  }

  $mainPath = Join-Path $stage 'onionmind.py'
  $new = [IO.File]::ReadAllText($mainPath, [Text.Encoding]::UTF8)
  $new = $new.Replace(
    'PORTS  = (9050, 9150)                            # 9050 = tor daemon, 9150 = Tor Browser',
    'PORTS  = (9150, 9050)                            # 9150 = Tor Browser, 9050 = tor daemon'
  )
  $new = $new.Replace(
    'Needs a tor daemon on 9050 (systemctl start tor) or Tor Browser on 9150.',
    'Needs Tor Browser open (it owns SOCKS on 9150) or a tor daemon on 9050.'
  )
  $new = $new.Replace(
    'sys.exit("No Tor proxy on 9050/9150. Try: sudo systemctl start tor")',
    'sys.exit("No Tor proxy on 9150/9050. Open Tor Browser and leave it running.")'
  )
  $modelPattern = New-Object Text.RegularExpressions.Regex('(?m)^MODEL\s*=.*$')
  if ($modelPattern.Matches($new).Count -ne 1) {
    throw 'Downloaded onionmind.py does not contain exactly one MODEL assignment.'
  }
  $modelLine = 'MODEL  = ' + (ConvertTo-Json -Compress -InputObject $model)
  $modelEvaluator = [Text.RegularExpressions.MatchEvaluator]{ param($unusedMatch) $modelLine }
  $new = $modelPattern.Replace($new, $modelEvaluator, 1)
  [IO.File]::WriteAllText($mainPath, $new, $Utf8NoBom)

  $patchPath = Join-Path $stage 'dsh-onionmind-tor.patch.yml'
  $patchText = [IO.File]::ReadAllText($patchPath, [Text.Encoding]::UTF8)
  if (-not $patchText.Contains('@ONIONMIND_DSH_PLUGIN@')) {
    throw 'Downloaded DSH patch placeholder is missing.'
  }
  $pluginPath = ((Join-Path $InstallDir 'dsh-onionmind-tor-search.js') -replace '\\', '/').Replace("'", "''")
  [IO.File]::WriteAllText(
    $patchPath,
    $patchText.Replace('@ONIONMIND_DSH_PLUGIN@', $pluginPath),
    $Utf8NoBom
  )

  & $py -m py_compile (Join-Path $stage 'onionmind.py')
  if ($LASTEXITCODE -ne 0) { throw 'Downloaded Onionmind core failed syntax validation.' }
  & $py -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
  $nativePythonSupported = ($LASTEXITCODE -eq 0)
  if ($nativePythonSupported) {
    & $py -m py_compile `
      (Join-Path $stage 'onionmind_desktop_core.py') `
      (Join-Path $stage 'onionmind_desktop.py')
    if ($LASTEXITCODE -ne 0) { throw 'Downloaded native desktop sources failed syntax validation.' }
  } else {
    Write-Warning 'Python 3.10+ is required for the native workbench; the classic UI will remain active.'
  }

  foreach ($name in $files) {
    Copy-Item -LiteralPath (Join-Path $stage $name) -Destination (Join-Path $prepared $name)
  }

  $replaced = New-Object 'System.Collections.Generic.List[string]'
  try {
    foreach ($name in $files) {
      $source = Join-Path $prepared $name
      $destination = Join-Path $InstallDir $name
      $backup = Join-Path $prepared ("backup-" + $name)
      if (Test-Path -LiteralPath $destination -PathType Leaf) {
        [IO.File]::Replace($source, $destination, $backup, $true)
      } else {
        [IO.File]::Move($source, $destination)
      }
      $replaced.Add($name)
    }
    $swapped = $true
  } catch {
    for ($index = $replaced.Count - 1; $index -ge 0; $index--) {
      $name = $replaced[$index]
      $destination = Join-Path $InstallDir $name
      $backup = Join-Path $prepared ("backup-" + $name)
      if (Test-Path -LiteralPath $backup -PathType Leaf) {
        [IO.File]::Copy($backup, $destination, $true)
      } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
        Remove-Item -LiteralPath $destination -Force
      }
    }
    throw
  }
} finally {
  Remove-VerifiedUpdateDirectory -Path $stage -Parent ([IO.Path]::GetTempPath()) -Prefix 'onionmind-update-'
  Remove-VerifiedUpdateDirectory -Path $prepared -Parent $InstallDir -Prefix '.onionmind-update-'
}

if (-not $swapped) { throw 'Onionmind update did not install the prepared source set.' }

$desktopEnv = Join-Path $InstallDir 'desktop-env'
& $py -m venv $desktopEnv
if ($LASTEXITCODE -eq 0) {
  $desktopPython = Join-Path $desktopEnv 'Scripts\python.exe'
  $desktopMarker = Join-Path $desktopEnv '.onionmind-desktop-ready'
  Remove-Item -LiteralPath $desktopMarker -Force -ErrorAction SilentlyContinue
  & $desktopPython -m pip install --quiet --disable-pip-version-check requests PySocks PySide6-Essentials==6.11.1
  $desktopPipSucceeded = ($LASTEXITCODE -eq 0)
  if (-not $desktopPipSucceeded) {
    Write-Warning 'Native desktop dependency download failed; checking the existing environment.'
  }
  $desktopImportCheck = 'import requests, socks, PySide6.QtWidgets'
  & $desktopPython -c $desktopImportCheck
  if ($LASTEXITCODE -eq 0) {
    [IO.File]::WriteAllText($desktopMarker, "ready`n", $Utf8NoBom)
  } else {
    Write-Warning 'Native desktop dependencies could not be imported; the classic UI remains available.'
  }
} else {
  Write-Warning 'Could not create the native desktop environment; the classic UI remains available.'
}

@'
param([switch]$UI)
$Host.UI.RawUI.WindowTitle = 'Onionmind'
$tor = Get-Process firefox -ErrorAction SilentlyContinue |
       Where-Object { $_.Path -like '*Tor Browser*' } | Select-Object -First 1
if (-not $tor) {
  foreach ($c in @("$([Environment]::GetFolderPath('Desktop'))\Tor Browser\Browser\firefox.exe",
                   "$env:USERPROFILE\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:USERPROFILE\OneDrive\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Programs\Tor Browser\Browser\firefox.exe",
                   "$env:PROGRAMFILES\Tor Browser\Browser\firefox.exe",
                   "${env:ProgramFiles(x86)}\Tor Browser\Browser\firefox.exe")) {
    if (Test-Path $c) { Start-Process $c; break }
  }
}
for ($i = 0; $i -lt 45; $i++) {
  if (Get-NetTCPConnection -LocalPort 9150 -State Listen -ErrorAction SilentlyContinue) { break }
  if (Get-NetTCPConnection -LocalPort 9050 -State Listen -ErrorAction SilentlyContinue) { break }
  Start-Sleep 1
}
Set-Location ~
$desktopPython = Join-Path $PSScriptRoot 'desktop-env\Scripts\python.exe'
$desktopPythonw = Join-Path $PSScriptRoot 'desktop-env\Scripts\pythonw.exe'
$desktopReady = Test-Path (Join-Path $PSScriptRoot 'desktop-env\.onionmind-desktop-ready')
if ($UI -or ($args.Count -eq 0)) {
  if ($desktopReady -and (Test-Path $desktopPythonw)) { & $desktopPythonw "$PSScriptRoot\onionmind.py" --ui }
  else { & pythonw "$PSScriptRoot\onionmind.py" --ui }
} else {
  if ($desktopReady -and (Test-Path $desktopPython)) { & $desktopPython "$PSScriptRoot\onionmind.py" @args }
  else { & python "$PSScriptRoot\onionmind.py" @args }
}
'@ | Set-Content (Join-Path $InstallDir 'onionmind-launch.ps1') -Encoding UTF8

@'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$Model = '@ONIONMIND_MODEL@'
if (-not $Arguments -or $Arguments.Count -eq 0) {
  Write-Host 'Usage: onionmind-code "task for the coding agent"' -ForegroundColor Yellow
  exit 2
}
$nodeCommand = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
  Write-Error 'DeepSeek Harness requires Node.js ^22.19 or 24+. Install a supported Node.js release first.'
  exit 1
}
try { $nodeVersion = [version]((& $nodeCommand.Source --version).TrimStart([char]'v')) }
catch { Write-Error 'Could not read the installed Node.js version.'; exit 1 }
if (-not ($nodeVersion.Major -ge 24 -or ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -ge 19))) {
  Write-Error "DeepSeek Harness requires Node.js ^22.19 or 24+; found $nodeVersion."
  exit 1
}
$task = $Arguments -join ' '
Write-Host 'Agent network note: Harness traffic is direct; only Onionmind chat search uses Tor.' -ForegroundColor DarkYellow
& ollama launch dsh --model $Model -- --profile headless $task
exit $LASTEXITCODE
'@.Replace('@ONIONMIND_MODEL@', $model) |
  Set-Content (Join-Path $InstallDir 'onionmind-code-launch.ps1') -Encoding UTF8

@"
@echo off
title Onionmind Code
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind-code.cmd') -Encoding ASCII
@"
@echo off
title Onionmind
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind.cmd') -Encoding ASCII
@"
@echo off
title Onionmind Chat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind-chat.cmd') -Encoding ASCII
@"
@echo off
title Onionmind Update
set "ONIONMIND_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "`$u=Join-Path `$env:TEMP 'onionmind-update.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.ps1' -OutFile `$u; & `$u -InstallDir `$env:ONIONMIND_DIR; exit `$LASTEXITCODE"
"@ | Set-Content (Join-Path $InstallDir 'onionmind-update.cmd') -Encoding ASCII

Write-Host "Updated Onionmind native workbench and coding agent ($model). Model weights were not changed."
