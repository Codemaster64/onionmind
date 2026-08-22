@echo off
set "MS_DIR=%~dp0"
rem Matchstick, the easy way: double-click, pick an edition, plug a stick when asked.
rem This file is both the batch launcher and the whole PowerShell script (below the marker).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-Content -Raw ($env:MS_DIR + 'matchstick.cmd'); iex $c.Substring($c.IndexOf('#__MS'+'__PS__'))"
pause
exit /b
#__MS__PS__
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSStyle.OutputRendering = 'PlainText' }
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new() } catch {}

$Repo = $env:MS_DIR.TrimEnd('\')
$Dry  = $env:MATCHSTICK_DRY -eq '1'

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

# --- elevation: raw disk writes need admin; ask once, up front ----------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $Dry) {
  Say "Asking for administrator (needed to write the USB stick)"
  Start-Process powershell -Verb RunAs -WindowStyle Normal -Wait -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-Command',
    "`$env:MS_DIR='$Repo'; iex ((Get-Content -Raw '$Repo\matchstick.cmd').Substring((Get-Content -Raw '$Repo\matchstick.cmd').IndexOf('#__MS'+'__PS__')))")
  exit
}

Write-Host ""
Write-Host "   Onionmind Matchstick - build + burn, the easy way" -ForegroundColor Magenta
Write-Host "   The USB stick that forgets. It will be ERASED." -ForegroundColor DarkGray
Write-Host ""
Write-Host "   [D] Download the pre-built stick  (pocket/spark, 6.6GB - no Docker, no build)"
Write-Host "   [B] Build from source             (any edition, needs Docker, ~1 hour)"
Write-Host ""
$mode = (Read-Host "   Download or build? [D/B, Enter = D]").Trim().ToUpper()
$prebuilt = if ($mode -eq 'B') { $false } else { $true }

if ($prebuilt) {
  $edition = '4b'; $needGB = 16
  Say "Pre-built pocket stick (spark edition)"
  $iso = Join-Path $Repo "usb\out\onionmind-matchstick-4b-amd64.iso"
  if (-not (Test-Path $iso)) {
    if ($Dry) { Say "DRY: would download 4 parts + SHA256SUMS and rejoin into $iso"; exit }
    Say "Downloading pre-built pocket stick (4 parts, ~6.6GB)"
    $base = "https://github.com/Codemaster64/onionmind/releases/download/matchstick-pocket"
    $partsDir = Join-Path $Repo "usb\out\parts"
    New-Item -ItemType Directory -Force -Path $partsDir | Out-Null
    # gh first: it authenticates, which private repos require. curl works once
    # the repo is public.
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
      & gh release download matchstick-pocket --repo Codemaster64/onionmind `
        --dir $partsDir --clobber `
        --pattern 'onionmind-matchstick-4b-amd64.iso.part*' --pattern 'SHA256SUMS'
      if ($LASTEXITCODE -ne 0) { throw "gh download failed - check: gh auth status" }
    } else {
      foreach ($p in 'part00','part01','part02','part03') {
        $f = "onionmind-matchstick-4b-amd64.iso.$p"
        & curl.exe -L -C - --fail -o (Join-Path $partsDir $f) "$base/$f"
        if ($LASTEXITCODE -ne 0) { throw "download failed at $f - needs the repo public, or install GitHub CLI (gh) and run: gh auth login" }
      }
      & curl.exe -L -C - --fail -o (Join-Path $partsDir 'SHA256SUMS') "$base/SHA256SUMS"
    }
    Say "Rejoining parts"
    $out = [IO.File]::Create($iso)
    foreach ($p in 'part00','part01','part02','part03') {
      $bytes = [IO.File]::ReadAllBytes((Join-Path $partsDir "onionmind-matchstick-4b-amd64.iso.$p"))
      $out.Write($bytes, 0, $bytes.Length)
    }
    $out.Close()
    Say "Verifying checksum"
    $want = (Select-String -Path (Join-Path $partsDir 'SHA256SUMS') -Pattern '^([0-9a-f]{64})\s+\*?onionmind-matchstick-4b-amd64\.iso$').Matches[0].Groups[1].Value
    $got = (Get-FileHash $iso -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want) { throw "checksum mismatch (want $want got $got) - delete parts and retry" }
    Say "Checksum OK"
  }
} else {

Write-Host ""
Write-Host "   1) pocket     - 4B,  runs on anything            (16GB stick)"
Write-Host "   2) daily      - 9B,  fast small-GPU daily driver  (16GB stick)"
Write-Host "   3) standard   - 27B squeezed, 8GB GPUs            (32GB stick)"
Write-Host "   4) flagship   - full 27B + vision, 12GB GPUs      (32GB stick)  <- recommended"
Write-Host "   5) max        - full fat 27B, 17GB+ GPUs          (32GB stick)"
Write-Host ""
$pick = Read-Host "   Which edition? [1-5, Enter = 4]"
$edition = @{1='4b';2='9b';3='8gb';4='12gb';5='17gb'}[[int]($pick -replace '\D','')]
if (-not $edition) { $edition = '12gb' }
$needGB = if ($edition -in '4b','9b') { 16 } else { 32 }
Say "Edition: $edition (needs a ${needGB}GB+ USB-3 stick)"

$iso = Join-Path $Repo "usb\out\onionmind-matchstick-$edition-amd64.iso"

# --- build if the ISO isn't there yet ------------------------------------------
if (-not (Test-Path $iso)) {
  if ($Dry) { Say "DRY: would build $iso"; exit }
  else {
    Say "Checking Docker"
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
      Warn "Docker isn't running. Install/start Docker Desktop, then run this file again."
      Warn "Get it: https://www.docker.com/products/docker-desktop/"
      exit 1
    }
    Say "Building the stick (downloads are cached; the squashfs step takes ~1h first time)"
    docker build -f (Join-Path $Repo 'usb\Dockerfile') -t onionmind-usb $Repo
    if ($LASTEXITCODE -ne 0) { throw "image build failed" }
    docker run --rm --privileged `
      -v "${Repo}\usb\cache:/onionmind/usb/cache" `
      -v "${Repo}\usb\out:/onionmind/usb/out" `
      onionmind-usb $edition
    if ($LASTEXITCODE -ne 0) { throw "stick build failed" }
  }
}
} # end build-from-source branch
Say "ISO: $iso ($([math]::Round((Get-Item $iso).Length/1GB,1)) GB)"

# --- pick a stick ----------------------------------------------------------------
$sticks = Get-Disk | Where-Object BusType -eq 'USB'
if (-not $sticks) { Warn "No USB stick found - plug one in and run this again."; exit 1 }
Write-Host ""
$sticks | ForEach-Object { Write-Host ("   [{0}] {1}  {2} GB" -f $_.Number, $_.FriendlyName, [math]::Round($_.Size/1GB)) }
$choice = Read-Host "   Which one? [number]"
$disk = $sticks | Where-Object Number -eq ($choice -replace '\D','')
if (-not $disk) { throw "no such stick" }
if ($disk.Size/1GB -lt $needGB) { Warn "That stick is too small for the $edition edition ($needGB GB needed)."; exit 1 }
Write-Host ""
Write-Host ("   ABOUT TO ERASE: {0} ({1} GB) - ISO will be written raw" -f $disk.FriendlyName, [math]::Round($disk.Size/1GB)) -ForegroundColor Red
$confirm = Read-Host "   Type YES to continue"
if ($confirm -cne 'YES') { Say "aborted, nothing written"; exit }

if ($Dry) { Say "DRY: would raw-write $iso to PhysicalDrive$($disk.Number)"; exit }

# --- raw write with progress -------------------------------------------------------
Say "Writing (this is the slow part - USB speed is the limit)"
$src = [IO.File]::OpenRead($iso)
$dst = New-Object IO.FileStream("\\.\PhysicalDrive$($disk.Number)", [IO.FileMode]::Open,
                                [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
$buf = New-Object byte[] (8MB); $done = 0L; $total = $src.Length
while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
  $dst.Write($buf, 0, $n); $done += $n
  $pct = [math]::Round(100 * $done / $total)
  Write-Host ("`r   {0,3}% ({1}/{2} GB)" -f $pct, [math]::Round($done/1GB,1), [math]::Round($total/1GB,1)) -NoNewline
}
$dst.Flush($true); $dst.Close(); $src.Close()
Write-Host ""
Say "Done. Unplug the stick, then:"
Write-Host "   1. Plug it into the machine you want to boot"
Write-Host "   2. Turn Secure Boot OFF in the firmware (the NVIDIA driver is unsigned)"
Write-Host "   3. Boot menu: F12 / F8 / Esc / F9 (varies by maker) - pick USB"
Write-Host "   4. First boot takes a minute. Then: sudo onionmind-status"
