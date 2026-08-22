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
@"
@echo off
title Onionmind Code
set "ONIONMIND_PY=%~dp0onionmind.py"
set "ONIONMIND_PYTHON=python"
ollama launch dsh --model $model -- --patch "%~dp0dsh-onionmind-tor.patch.yml" %*
"@ | Set-Content (Join-Path $InstallDir 'onionmind-code.cmd') -Encoding ASCII
Write-Host "Updated Onionmind code and onionmind-code ($model). Model weights were not changed."
