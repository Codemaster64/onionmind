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
  'onionmind_desktop.py'
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

# Keep the coding Adapter pinned so an update cannot silently change its
# permission model or command-line contract.
$QwenCodeVersion = '0.22.0'
$env:Path = "$env:ProgramFiles\nodejs;$env:APPDATA\npm;$env:Path"
$nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue |
  Select-Object -First 1
$nodeVersion = $null
if ($nodeCommand) {
  try { $nodeVersion = [version]((& $nodeCommand.Source --version).TrimStart([char]'v')) }
  catch { $nodeVersion = $null }
}
if (-not $nodeVersion -or $nodeVersion.Major -lt 22) {
  Write-Host 'Installing Node.js 22 or newer for Onionmind Agent' -ForegroundColor Cyan
  winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements `
                 --accept-source-agreements --disable-interactivity
  $env:Path = "$env:ProgramFiles\nodejs;$env:APPDATA\npm;$env:Path"
  $nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($nodeCommand) {
    try { $nodeVersion = [version]((& $nodeCommand.Source --version).TrimStart([char]'v')) }
    catch { $nodeVersion = $null }
  }
}
if (-not $nodeVersion -or $nodeVersion.Major -lt 22) {
  throw 'Onionmind Agent requires Node.js 22 or newer. Install it, then rerun Onionmind Update.'
}
$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $npmCommand) { throw 'npm is required to update Onionmind Agent.' }
& $npmCommand.Source install --global '@qwen-code/qwen-code@0.22.0' --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { throw 'Onionmind Agent runtime update failed.' }
$qwenCommand = Get-Command qwen.cmd -CommandType Application -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $qwenCommand) { throw 'Onionmind Agent runtime is unavailable after update.' }
# qwen --version is the readiness probe shared with the desktop Adapter.
$qwenVersion = (& $qwenCommand.Source --version).Trim()
if (-not $qwenVersion.StartsWith($QwenCodeVersion)) {
  throw "Onionmind Agent runtime version $qwenVersion was installed; expected $QwenCodeVersion."
}

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

# Existing installs may still have the smaller chat-only context. Re-register
# the same local model manifests with enough room for the Agent prompt and file
# tools; this changes configuration only and does not download or duplicate weights.
$agentContextLine = 'PARAMETER num_ctx 32768'
$modelBase = ($model -split ':', 2)[0]
$modelDefinitions = @(
  @{ Path = (Join-Path $InstallDir 'Modelfile'); Name = $model },
  @{ Path = (Join-Path $InstallDir 'Modelfile.vision'); Name = "$modelBase-vision" }
)
$localEngineCommand = Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue |
  Select-Object -First 1
$localEnginePath = if ($localEngineCommand) { $localEngineCommand.Source } else { $null }
if (-not $localEnginePath) {
  $bundledEngine = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
  if (Test-Path -LiteralPath $bundledEngine -PathType Leaf) {
    $localEnginePath = $bundledEngine
  }
}
foreach ($definition in $modelDefinitions) {
  if (-not (Test-Path -LiteralPath $definition.Path -PathType Leaf)) { continue }
  $modelText = [IO.File]::ReadAllText($definition.Path, [Text.Encoding]::UTF8)
  $contextPattern = New-Object Text.RegularExpressions.Regex('(?m)^PARAMETER\s+num_ctx\s+\d+\s*$')
  if ($contextPattern.IsMatch($modelText)) {
    $modelText = $contextPattern.Replace($modelText, $agentContextLine, 1)
  } else {
    $modelText = $modelText.TrimEnd() + "`n$agentContextLine`n"
  }
  [IO.File]::WriteAllText($definition.Path, $modelText, $Utf8NoBom)
  if ($localEnginePath) {
    & $localEnginePath create $definition.Name -f $definition.Path
    if ($LASTEXITCODE -ne 0) {
      throw "Could not prepare Onionmind model $($definition.Name) for Agent mode."
    }
  }
}

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
$nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
  Write-Error 'Onionmind Agent requires Node.js 22 or newer. Re-run Onionmind Update.'
  exit 1
}
try { $nodeVersion = [version]((& $nodeCommand.Source --version).TrimStart([char]'v')) }
catch { Write-Error 'Could not read the installed Node.js version.'; exit 1 }
if ($nodeVersion.Major -lt 22) {
  Write-Error "Onionmind Agent requires Node.js 22 or newer; found $nodeVersion."
  exit 1
}
$qwenCommand = Get-Command qwen.cmd -CommandType Application -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $qwenCommand) {
  Write-Error 'Onionmind Agent runtime is missing. Re-run Onionmind Update.'
  exit 1
}
# qwen --version confirms the pinned Adapter before every launch.
$qwenVersion = (& $qwenCommand.Source --version).Trim()
if (-not $qwenVersion.StartsWith('0.22.0')) {
  Write-Error "Onionmind Agent runtime is out of date ($qwenVersion). Re-run Onionmind Update."
  exit 1
}
$task = $Arguments -join ' '
$stateRoot = Join-Path $PSScriptRoot 'agent'
$runtimeRoot = Join-Path $stateRoot 'runtime'
[IO.Directory]::CreateDirectory($runtimeRoot) | Out-Null
$agentSettings = @{
  model = @{
    generationConfig = @{
      contextWindowSize = 32768
      samplingParams = @{ max_tokens = 2048 }
      reasoning = $false
      extra_body = @{ reasoning_effort = 'none' }
    }
  }
}
$agentSettings | ConvertTo-Json -Depth 5 |
  Set-Content (Join-Path $stateRoot 'settings.json') -Encoding UTF8
$env:QWEN_HOME = $stateRoot
$env:QWEN_RUNTIME_DIR = $runtimeRoot
$env:QWEN_USAGE_STATISTICS_ENABLED = 'false'
$env:QWEN_CODE_SKIP_UPDATE_CHECK_ONCE = '1'
$env:OPENAI_API_KEY = 'onionmind-local'
$env:OPENAI_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:OPENAI_MODEL = $Model
$env:NO_PROXY = '127.0.0.1,::1'
$env:no_proxy = '127.0.0.1,::1'
foreach ($name in 'ALL_PROXY','HTTPS_PROXY','HTTP_PROXY','all_proxy','https_proxy','http_proxy') {
  Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$excluded = 'run_shell_command,web_fetch,web_search,image_gen,save_memory,agent,skill,ask_user_question,cron_create,cron_list,cron_delete,loop_wakeup,create_sub_session,list_agents,task_create,task_update,task_stop,team_create,team_delete,send_message,monitor,tool_search,read_mcp_resource,enter_worktree,exit_worktree,workflow,computer_use__bring_to_front,computer_use__check_for_update,computer_use__check_permissions,computer_use__click,computer_use__double_click,computer_use__drag,computer_use__end_session,computer_use__get_accessibility_tree,computer_use__get_agent_cursor_state,computer_use__get_config,computer_use__get_cursor_position,computer_use__get_recording_state,computer_use__get_screen_size,computer_use__get_window_state,computer_use__hotkey,computer_use__kill_app,computer_use__launch_app,computer_use__list_apps,computer_use__list_windows,computer_use__move_cursor,computer_use__page,computer_use__press_key,computer_use__replay_trajectory,computer_use__right_click,computer_use__scroll,computer_use__set_agent_cursor_enabled,computer_use__set_agent_cursor_motion,computer_use__set_agent_cursor_style,computer_use__set_config,computer_use__set_value,computer_use__start_recording,computer_use__start_session,computer_use__stop_recording,computer_use__type_text,computer_use__zoom,get_goal,notebook_edit,record_artifact,todo_write,update_goal,zoom_image'
$systemPrompt = 'You are Onionmind Agent, a local coding agent. Work only in the current project. Complete the user task by inspecting and editing files with the available file tools. Never use shell, web, network, cloud, persistence, or subagents. Make minimal accurate changes. Do not merely describe an edit: call a file-edit tool. Stop when the task is complete.'
Write-Host 'Onionmind Agent can edit files in this project; shell and web tools are disabled.' -ForegroundColor DarkYellow
& $qwenCommand.Source --prompt $task --system-prompt $systemPrompt --output-format text `
  --approval-mode auto-edit --auth-type openai --model $Model `
  --openai-api-key onionmind-local --openai-base-url http://127.0.0.1:11434/v1 `
  --telemetry=false --chat-recording=false --safe-mode --exclude-tools $excluded `
  --max-wall-time 30m --max-tool-calls 200 --channel desktop
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
