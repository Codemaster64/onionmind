param([string]$InstallDir = "$env:LOCALAPPDATA\qwen")
$ErrorActionPreference = 'Stop'
$raw = 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/onionmind.py'
$new = (Invoke-WebRequest -UseBasicParsing $raw).Content
$new = $new.Replace('PORTS  = (9050, 9150)                            # 9050 = tor daemon, 9150 = Tor Browser', 'PORTS  = (9150, 9050)                            # 9150 = Tor Browser, 9050 = tor daemon')
$new = $new.Replace('Needs a tor daemon on 9050 (systemctl start tor) or Tor Browser on 9150.', 'Needs Tor Browser open (it owns SOCKS on 9150) or a tor daemon on 9050.')
$new = $new.Replace('sys.exit("No Tor proxy on 9050/9150. Try: sudo systemctl start tor")', 'sys.exit("No Tor proxy on 9150/9050. Open Tor Browser and leave it running.")')
$old = Join-Path $InstallDir 'onionmind.py'
$model = 'inferno-27b'
if (Test-Path $old) {
  $match = Select-String -Path $old -Pattern '^MODEL\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($match) { $model = $match.Matches[0].Groups[1].Value }
}
$new = [regex]::Replace($new, 'MODEL\s*=\s*"[^"]+"', "MODEL  = `"$model`"", 1)
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Content -Path $old -Value $new -Encoding UTF8
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor-search.js' -OutFile (Join-Path $InstallDir 'dsh-onionmind-tor-search.js')
$dshPatch = (Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor.patch.yml').Content
$dshPatch.Replace('@ONIONMIND_DSH_PLUGIN@', ((Join-Path $InstallDir 'dsh-onionmind-tor-search.js') -replace '\\', '/')) |
  Set-Content (Join-Path $InstallDir 'dsh-onionmind-tor.patch.yml') -Encoding UTF8
# Keep the installed command in sync with the UI entry point too. Older
# installs may still have a terminal-only launcher, even after onionmind.py
# itself has been updated.
@'
param([switch]$UI)
$Host.UI.RawUI.WindowTitle = 'Onionmind'
$tor = Get-Process firefox -ErrorAction SilentlyContinue |
       Where-Object { $_.Path -like '*Tor Browser*' } | Select-Object -First 1
if (-not $tor) {
  foreach ($c in @("$env:USERPROFILE\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Tor Browser\Browser\firefox.exe",
                   "$env:PROGRAMFILES\Tor Browser\Browser\firefox.exe")) {
    if (Test-Path $c) { Start-Process $c; break }
  }
}
for ($i = 0; $i -lt 45; $i++) {
  if (Get-NetTCPConnection -LocalPort 9150 -State Listen -ErrorAction SilentlyContinue) { break }
  if (Get-NetTCPConnection -LocalPort 9050 -State Listen -ErrorAction SilentlyContinue) { break }
  Start-Sleep 1
}
Set-Location ~
if ($UI -or ($args.Count -eq 0)) {
  & pythonw "$PSScriptRoot\onionmind.py" --ui
} else {
  & python "$PSScriptRoot\onionmind.py" @args
}
'@ | Set-Content (Join-Path $InstallDir 'onionmind-launch.ps1') -Encoding UTF8
@"
@echo off
title Onionmind Code
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind-code.cmd') -Encoding ASCII
@"
@echo off
title Onionmind
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind.cmd') -Encoding ASCII
@"
@echo off
title Onionmind Chat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind-chat.cmd') -Encoding ASCII
@"
@echo off
title Onionmind Update
powershell -NoProfile -ExecutionPolicy Bypass -Command "`$u=Join-Path `$env:TEMP 'onionmind-update.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.ps1' -OutFile `$u; & `$u -InstallDir '%~dp0'; exit `$LASTEXITCODE"
"@ | Set-Content (Join-Path $InstallDir 'onionmind-update.cmd') -Encoding ASCII
@'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$Model = '@ONIONMIND_MODEL@'
$tor = Get-Process firefox -ErrorAction SilentlyContinue |
       Where-Object { $_.Path -like '*Tor Browser*' } | Select-Object -First 1
if (-not $tor) {
  foreach ($c in @("$env:USERPROFILE\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Tor Browser\Browser\firefox.exe",
                   "$env:PROGRAMFILES\Tor Browser\Browser\firefox.exe")) {
    if (Test-Path $c) { Start-Process $c; break }
  }
}
$torPort = $null
for ($i = 0; $i -lt 45; $i++) {
  foreach ($port in 9150, 9050) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
      $torPort = $port
      break
    }
  }
  if ($torPort) { break }
  Start-Sleep 1
}
if (-not $torPort) {
  Write-Host 'Tor: NOT READY - open Tor Browser and click Connect.' -ForegroundColor Red
  exit 1
}
Write-Host ("Tor: UP (SOCKS {0})" -f $torPort) -ForegroundColor Green
$env:ONIONMIND_PY = Join-Path $PSScriptRoot 'onionmind.py'
$env:ONIONMIND_PYTHON = 'python'
$patch = Join-Path $PSScriptRoot 'dsh-onionmind-tor.patch.yml'
& ollama launch dsh --model $Model -- --patch $patch @Arguments
exit $LASTEXITCODE
'@.Replace('@ONIONMIND_MODEL@', $model) |
  Set-Content (Join-Path $InstallDir 'onionmind-code-launch.ps1') -Encoding UTF8
Write-Host "Updated Onionmind UI launcher and coding launcher ($model). Model weights were not changed."
