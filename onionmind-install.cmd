:<<'BATCH'
@echo off
rem Onionmind universal installer - one file for Windows, macOS, Linux and Android.
rem   Windows:                     double-click onionmind-install.cmd
rem   macOS, Linux, Android/Termux: sh onionmind-install.cmd
rem It looks at this device, recommends what to install, lets you choose, then
rem hands over to the per-platform installer that does the actual work.
rem
rem This file is three scripts at once. cmd.exe reads it as a label line plus the
rem batch lines down to "exit /b". sh reads the same lines as a heredoc fed to
rem the no-op command and skips them. The PowerShell half sits below the marker
rem at the end, after the sh half has already exited. Keep it ASCII and LF-only:
rem a CR breaks sh, and LF-only is fine for cmd as long as nothing here uses goto.
setlocal
set "ONIONMIND_INSTALLER=%~f0"
set "ONIONMIND_INSTALLER_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = [IO.File]::ReadAllText($env:ONIONMIND_INSTALLER, [Text.Encoding]::UTF8); iex $c.Substring($c.IndexOf('#__ONIONMIND_'+'INSTALL_PS__'))"
exit /b %ERRORLEVEL%
BATCH
# ============================================================================
# POSIX sh half: macOS, Linux, Android (Termux). No bashisms - Termux's and
# Debian's sh is dash, macOS's is bash 3.2 in POSIX mode.
# ============================================================================
set -u
BASE="https://github.com/Codemaster64/onionmind/releases/download/desktop-latest"
RELEASES="https://github.com/Codemaster64/onionmind/releases"
OLLAMA_TOR_SRC="https://github.com/Codemaster64/onionmind/archive/refs/heads/main.tar.gz#subdirectory=ollama-tor"
HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || HERE=$(pwd)

say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
gb()   { awk -v m="$1" 'BEGIN { printf "%.1f", m / 1000 }'; }   # MB -> "12.6"

DRY=0; YES=0
for a in "$@"; do
  case "$a" in
    -n|--dry-run) DRY=1 ;;
    -y|--yes)     YES=1 ;;
    -h|--help)
      echo "usage: sh onionmind-install.cmd [--dry-run] [--yes]"
      echo "  --dry-run  show this device, the recommendation and the plan; change nothing"
      echo "  --yes      install the recommended choices without asking"
      echo "  ONIONMIND_HOST=user@server  the server for the remote-models option"
      exit 0 ;;
    *) die "unknown option: $a (try --help)" ;;
  esac
done

# --- 1. What is this device? ------------------------------------------------
KERNEL=$(uname -s); ARCH=$(uname -m)
case "$KERNEL" in
  Darwin) OS=mac ;;
  Linux)
    if [ -n "${TERMUX_VERSION:-}" ] || [ "$(uname -o 2>/dev/null)" = Android ]; then OS=android
    else OS=linux; fi ;;
  MINGW*|MSYS*|CYGWIN*)
    die "this is a Unix shell on Windows - double-click onionmind-install.cmd in Explorer instead" ;;
  *) die "no Onionmind build for $KERNEL" ;;
esac

RAM=0; VRAM=0; GPU=none; DISK=0; DISTRO=""; NOTES=""
note() { NOTES="$NOTES
    - $*"; }
free_mb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 { print int($4 / 1024) }'; }

case $OS in
  mac)
    RAM=$(( $(sysctl -n hw.memsize) / 1048576 ))
    # Same rule as install-onionmind.sh: unified memory minus ~5 GB for macOS.
    GPU="Apple $( [ "$ARCH" = arm64 ] && echo 'Silicon (Metal)' || echo 'Intel Mac' )"
    [ "$RAM" -gt 5000 ] && VRAM=$(( RAM - 5000 ))
    [ "$ARCH" = arm64 ] || note "Intel Mac: models run far slower than on Apple Silicon - prefer the fast model."
    DISK=$(free_mb "$HOME"); DISTRO=macOS ;;
  linux|android)
    RAM=$(( $(awk '/MemTotal/ { print $2 }' /proc/meminfo) / 1024 ))
    if [ $OS = linux ]; then
      if have nvidia-smi; then
        GPU=NVIDIA
        VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
      elif [ -d /sys/module/amdgpu ]; then
        GPU=AMD
        have rocm-smi && VRAM=$(rocm-smi --showmeminfo vram --csv 2>/dev/null | awk -F, 'NR==2 { print int($2 / 1048576) }')
        [ "${VRAM:-0}" -gt 0 ] 2>/dev/null || note "AMD GPU without rocm-smi: VRAM unknown, so the recommendation assumes CPU."
      fi
      VRAM=${VRAM:-0}
      case "$VRAM" in *[!0-9]*|'') VRAM=0 ;; esac
      if have pacman; then DISTRO=arch; elif have apt-get; then DISTRO=debian; fi
      [ -d /var/lib ] && DISK=$(free_mb /var/lib) || DISK=$(free_mb "$HOME")
      grep -qi microsoft /proc/version 2>/dev/null &&
        note "WSL detected: running onionmind-install.cmd from Windows gives you the native Windows app instead."
    else
      DISK=$(free_mb "$HOME"); DISTRO=Android
    fi ;;
esac
DISK=${DISK:-0}

NODE=""; have node && NODE=$(node -v 2>/dev/null | tr -d v)
node_ok() {  # the coding agent needs Node ^22.19 or 24+
  [ -n "$NODE" ] || return 1
  maj=${NODE%%.*}; rest=${NODE#*.}; min=${rest%%.*}
  [ "$maj" -ge 24 ] 2>/dev/null || { [ "$maj" -eq 22 ] && [ "$min" -ge 19 ]; } 2>/dev/null
}
py_ok() { have python3 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }

# --- 2. Which model fits ----------------------------------------------------
# Thresholds and files mirror install-onionmind.sh and install-onionmind-android.sh,
# which make the final pick; this only explains it up front. Sizes are the
# Hugging Face file sizes (Content-Length), checked 2026-10-12.
model_desc() {  # want -> "NAME, download MB"
  case "$1" in
    27b)
      if   [ "$VRAM" -ge 17000 ]; then MD="INFERNO 27B Q4_K_M + vision"; MB=17700
      elif [ "$VRAM" -ge 12000 ]; then MD="INFERNO 27B 3.69bpw + vision"; MB=13500
      else                             MD="INFERNO 27B IQ2_M + vision";   MB=10900; fi ;;
    fast)
      if [ "$VRAM" -ge 6000 ]; then MD="BLAZE 9B (Qwen3.5)"; MB=5600
      else                          MD="EMBER 4B (Qwen3.5)"; MB=2700; fi ;;
    9b)  MD="Qwen3.5 9B";  MB=5600 ;;
    4b)  MD="Qwen3.5 4B";  MB=2700 ;;
    lfm) MD="LFM2.5 2.6B"; MB=1700 ;;
  esac
}
if [ $OS = android ]; then
  if   [ "$RAM" -ge 11000 ]; then REC_MODEL=9b
  elif [ "$RAM" -ge 7000 ];  then REC_MODEL=4b
  else                            REC_MODEL=lfm; fi
  MODELS="lfm 4b 9b"
else
  [ "$VRAM" -ge 8000 ] && REC_MODEL=27b || REC_MODEL=fast
  MODELS="fast 27b"
fi
model_line() {  # want -> one human line
  model_desc "$1"
  line="$1 - $MD, $(gb $MB) GB download"
  case "$1" in
    27b)  if [ "$VRAM" -lt 8000 ]; then line="$line; runs mostly on CPU here (~1-2 tok/s)"
          else line="$line; fits your $(gb "$VRAM") GB of GPU memory"; fi ;;
    fast) line="$line; quick replies, no image input" ;;
    9b)   line="$line; for 12 GB phones" ;;
    4b)   line="$line; for 8 GB phones" ;;
    lfm)  line="$line; for phones under 8 GB" ;;
  esac
  [ "$1" = "$REC_MODEL" ] && line="$line  <- recommended"
  echo "$line"
}

# --- 3. What can be installed here, and what we recommend ------------------
N=0
add() {  # id, recommended (yes|no|na), title, reason
  N=$((N + 1)); eval "ID_$N=\$1; REC_$N=\$2; TITLE_$N=\$3; WHY_$N=\$4"
}
INSTALLED=0; have onionmind && INSTALLED=1

if [ $OS = android ]; then
  if [ "$ARCH" = aarch64 ]; then
    add apk yes "Onionmind Android app (APK)" "the full app with its own engine and Tor; opens the releases page to download it"
  else
    add apk na "Onionmind Android app (APK)" "needs an arm64 phone; this one is $ARCH"
  fi
  if [ -n "${TERMUX_VERSION:-}" ]; then
    r=no; [ "$ARCH" = aarch64 ] || r=yes
    add termux $r "Onionmind in Termux (the onionmind command)" "terminal version; builds llama.cpp once (~15-20 min, plug in a charger)"
  else
    add termux na "Onionmind in Termux (the onionmind command)" "run this file inside Termux from F-Droid"
  fi
else
  # Desktop: install-onionmind.sh. Its own hard requirements, checked here first
  # so the menu can say why instead of the handover dying halfway.
  why=""
  [ "$(id -u)" -eq 0 ] && why="run as your normal user, not root - it uses sudo itself"
  if [ $OS = mac ]; then
    have brew || why="${why:-needs Homebrew first - install it from https://brew.sh, then rerun}"
  else
    have systemctl || why="${why:-needs systemd}"
    [ -n "$DISTRO" ] || why="${why:-needs Arch (pacman) or Ubuntu/Debian (apt-get)}"
  fi
  if [ -n "$why" ]; then
    add desktop na "Onionmind desktop app + local model" "$why"
  elif [ $INSTALLED = 1 ]; then
    add desktop no "Onionmind desktop app + local model" "already installed - run onionmind-update to update, or pick this to repair"
  else
    add desktop yes "Onionmind desktop app + local model" "native workbench, Tor search, the onionmind command and a desktop icon"
  fi

  # Remote compute: the app here, the models on a Linux box you can SSH into.
  # Never preselected - only the user knows whether they have such a server.
  if [ -n "$why" ]; then
    add client na "Onionmind desktop app, models on your own server (SSH)" "$why"
  elif have ssh; then
    r="a GPU box you can SSH into runs the models; files, agent and Tor stay here. No model download here"
    [ "$VRAM" -lt 6000 ] && r="$r - worth it on this machine's $(gb "$VRAM") GB of GPU memory"
    add client no "Onionmind desktop app, models on your own server (SSH)" "$r"
  else
    add client na "Onionmind desktop app, models on your own server (SSH)" "needs the ssh command (OpenSSH client)"
  fi

  # Matchstick: the launcher needs lsblk and bash 4 (mapfile) - Linux only.
  if [ $OS = linux ]; then
    r=no; [ -n "$why" ] && [ $INSTALLED = 0 ] && r=yes   # can't install here? boot it instead
    add usb $r "Matchstick USB stick (amnesic live system)" "boots Onionmind from a stick and forgets everything; ERASES the stick"
  else
    add usb na "Matchstick USB stick (amnesic live system)" "the stick writer needs Linux or Windows"
  fi

  if py_ok; then
    r=no; have ollama && [ $INSTALLED = 0 ] && r=yes
    add ollamator $r "ollama-tor (Tor routing for your own Ollama tools)" "fail-closed Tor proxy for 'ollama launch' integrations, no Onionmind needed"
  else
    add ollamator na "ollama-tor (Tor routing for your own Ollama tools)" "needs Python 3.10+"
  fi
fi

# --- 4. Show it ------------------------------------------------------------
yn() { have "$1" && echo yes || echo no; }
echo
printf '\033[35m   Onionmind installer\033[0m - local AI with Tor search\n\n'
echo "   This device"
case $OS in
  mac)     echo "     System   macOS $(sw_vers -productVersion 2>/dev/null), $ARCH" ;;
  android) echo "     System   Android, $ARCH$( [ -n "${TERMUX_VERSION:-}" ] && echo ", Termux $TERMUX_VERSION")" ;;
  linux)   echo "     System   Linux ($(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")), $ARCH" ;;
esac
echo "     Memory   $(gb "$RAM") GB RAM"
[ $OS = android ] || echo "     GPU      $GPU$( [ "$VRAM" -gt 0 ] && echo ", $(gb "$VRAM") GB usable for models")"
echo "     Disk     $(gb "$DISK") GB free"
if [ $OS != android ]; then
  echo "     Tools    python3 $(py_ok && echo yes || echo no), ollama $(yn ollama), tor $(yn tor), node ${NODE:-no}$(node_ok && echo ' (agent ready)')"
fi
[ -n "$NOTES" ] && printf '   Notes%s\n' "$NOTES"
echo
echo "   What can be installed"
i=1; DEFAULT=""
while [ $i -le $N ]; do
  eval "rec=\$REC_$i; title=\$TITLE_$i; why=\$WHY_$i; id=\$ID_$i"
  case $rec in
    yes) printf '    [%d] * %s   \033[32mrecommended\033[0m\n         %s\n' $i "$title" "$why"; DEFAULT="$DEFAULT $i" ;;
    no)  printf '    [%d]   %s\n         %s\n' $i "$title" "$why" ;;
    na)  printf '    [%d]   %s\n         \033[33mnot available here: %s\033[0m\n' $i "$title" "$why" ;;
  esac
  case "$id" in desktop|termux)
    [ $rec != na ] && printf '         model: %s\n' "$(model_line "$REC_MODEL")" ;;
  esac
  i=$((i + 1))
done
echo
[ -n "$DEFAULT" ] || [ $N -gt 0 ] || die "nothing installs on this device"

# --- 5. Choose -------------------------------------------------------------
ask() {  # prompt -> REPLY (empty in --yes mode)
  REPLY=""
  [ $YES = 1 ] && return 0
  printf '   %s ' "$1"
  read -r REPLY || REPLY=q
}
pick() {  # REPLY -> SEL, or a warning and status 1
  SEL=""
  for p in $(echo "${REPLY:-$DEFAULT}" | tr ',' ' '); do
    case "$p" in *[!0-9]*) warn "'$p' is not a number from the list"; return 1 ;; esac
    [ "$p" -ge 1 ] && [ "$p" -le $N ] || { warn "no option numbered $p"; return 1; }
    eval "rec=\$REC_$p; id=\$ID_$p"
    [ $rec = na ] && { warn "option $p is not available on this device"; return 1; }
    case " $SEL " in *" $id "*) ;; *) SEL="$SEL $id" ;; esac
  done
}
while :; do
  ask "Install which? [numbers like 1 3, Enter =${DEFAULT:- nothing}, q = quit]"
  case "$REPLY" in q|Q) say "nothing installed"; exit 0 ;; esac
  pick && break
  [ $YES = 1 ] && exit 1
done
[ -n "$SEL" ] || { say "nothing selected"; exit 0; }
case " $SEL " in *" desktop "*) case " $SEL " in *" client "*)
  die "pick either the local-model desktop app or the server one, not both" ;; esac ;; esac

HOST="${ONIONMIND_HOST:-}"
case " $SEL " in *" client "*)
  while [ -z "$HOST" ] && [ $YES = 0 ]; do
    ask "Server? [user@address or an ~/.ssh/config name, Enter = set it later in the app]"
    [ -n "$REPLY" ] || break
    case "$REPLY" in *[!A-Za-z0-9@._:%+-]*) warn "'$REPLY' does not look like user@address"; continue ;; esac
    HOST=$REPLY
  done
;; esac

MODEL=$REC_MODEL
case " $SEL " in *" desktop "*|*" termux "*)
  echo; echo "   Which model?"
  j=1
  for m in $MODELS; do printf '    [%d] %s\n' $j "$(model_line "$m")"; j=$((j + 1)); done
  while :; do
    ask "Model? [number, Enter = recommended]"
    [ -n "$REPLY" ] || break
    j=1; MODEL=""
    for m in $MODELS; do [ "$REPLY" = $j ] || [ "$REPLY" = "$m" ] && MODEL=$m; j=$((j + 1)); done
    [ -n "$MODEL" ] && break
    warn "no model '$REPLY' - pick a number from the list"; MODEL=$REC_MODEL
  done
  model_desc "$MODEL"
  # The weights stay on disk and Ollama keeps its own copy, so budget ~2x.
  [ $OS = android ] && need=$((MB + 1000)) || need=$((MB * 2 + 3000))
  [ "$DISK" -ge "$need" ] ||
    warn "only $(gb "$DISK") GB free; $MD wants about $(gb $need) GB - free space or pick a smaller model"
;; esac

# --- 6. Plan, then confirm --------------------------------------------------
script_for() {
  case "$1" in desktop|client) echo install-onionmind.sh ;; termux) echo install-onionmind-android.sh ;;
               usb) echo matchstick.sh ;; *) echo "" ;; esac
}
echo; echo "   Plan"
for id in $SEL; do
  s=$(script_for "$id")
  src="$BASE/$s"; [ -n "$s" ] && [ -f "$HERE/$s" ] && src="$HERE/$s (local copy)"
  case $id in
    desktop) echo "     - desktop app + $MD, via $src" ;;
    client)  echo "     - desktop app, no local model, server ${HOST:-(set later in Settings -> Compute)}, via $src --client"
             [ -n "$HOST" ] && echo "       if the server has no models yet, the installer sets it up over SSH (direct, not Tor)" ;;
    termux)  echo "     - Termux install + $MD, via $src" ;;
    usb)     echo "     - Matchstick USB writer, via $src (it asks before erasing anything)" ;;
    ollamator) echo "     - ollama-tor, from $OLLAMA_TOR_SRC" ;;
    apk)     echo "     - open $RELEASES to download the APK" ;;
  esac
done
echo "   Direct (non-Tor) downloads: github.com for the installers; the installers then"
echo "   fetch model weights from huggingface.co and packages from your package manager."
echo
if [ $DRY = 1 ]; then say "dry run - nothing downloaded or changed"; exit 0; fi
if [ $YES = 0 ]; then
  printf '   Go ahead? [Y/n] '; read -r ok || ok=n
  case "$ok" in ''|y|Y|yes) ;; *) say "nothing installed"; exit 0 ;; esac
fi

# --- 7. Hand over -----------------------------------------------------------
fetch() {  # script name, target dir -> path on stdout
  if [ -f "$HERE/$1" ]; then echo "$HERE/$1"; return 0; fi
  mkdir -p "$2" || return 1
  if have curl; then curl -fL --progress-bar -o "$2/$1" "$BASE/$1" >&2 || return 1
  elif have wget; then wget -q -O "$2/$1" "$BASE/$1" || return 1
  else warn "needs curl or wget to download $1"; return 1; fi
  echo "$2/$1"
}
TMP=$(mktemp -d 2>/dev/null || mktemp -d -t onionmind) || die "could not create a temp directory"
trap 'rm -rf "$TMP"' EXIT
DONE=""; FAILED=""
run() {  # id -> status
  case $1 in
    desktop)
      f=$(fetch install-onionmind.sh "$TMP") || return 1
      ONIONMIND_MODEL=$MODEL bash "$f" ;;
    client)
      f=$(fetch install-onionmind.sh "$TMP") || return 1
      if [ -n "$HOST" ]; then bash "$f" --client "--host=$HOST"
      else bash "$f" --client; fi ;;
    termux)
      f=$(fetch install-onionmind-android.sh "$TMP") || return 1
      ONIONMIND_MODEL=$MODEL bash "$f" ;;
    usb)
      # Not the temp dir: the writer keeps its downloaded image in usb/out next
      # to itself, so a rerun resumes instead of fetching 6.6 GB again.
      f=$(fetch matchstick.sh "$HOME/onionmind-matchstick") || return 1
      bash "$f" ;;
    ollamator)
      spec="ollama-tor @ $OLLAMA_TOR_SRC"
      [ -f "$HERE/ollama-tor/pyproject.toml" ] && spec="$HERE/ollama-tor"
      if have pipx; then pipx install --force "$spec"
      else
        # No pip --user: Arch, Debian and Homebrew Python refuse it (PEP 668).
        v="$HOME/.local/share/onionmind/ollama-tor"
        python3 -m venv "$v" && "$v/bin/pip" install -q "$spec" || return 1
        mkdir -p "$HOME/.local/bin" && ln -sf "$v/bin/ollama-tor" "$HOME/.local/bin/ollama-tor"
        say "ollama-tor installed to $HOME/.local/bin/ollama-tor"
      fi ;;
    apk)
      if have termux-open-url; then termux-open-url "$RELEASES"
      else echo "     Open this on the phone: $RELEASES"; fi
      echo "     Download Onionmind-<version>.apk from the newest release and open it to install." ;;
  esac
}
for id in $SEL; do
  echo; say "Installing: $id"
  if run "$id"; then DONE="$DONE $id"; else FAILED="$FAILED $id"; warn "$id did not finish"; fi
done
echo
[ -n "$DONE" ]   && say "Done:$DONE"
[ -n "$FAILED" ] && { warn "Failed:$FAILED - rerun this installer; finished steps are skipped"; exit 1; }
exit 0
# sh never runs past the exit above; the heredoc only keeps `sh -n` (which parses
# the whole file) from choking on PowerShell. Its terminator is a PS comment.
:<<'#__END__'
#__ONIONMIND_INSTALL_PS__
# ============================================================================
# PowerShell half: Windows. Runs under Windows PowerShell 5.1 and pwsh 7.
# ============================================================================
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSStyle.OutputRendering = 'PlainText' }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$Base = 'https://github.com/Codemaster64/onionmind/releases/download/desktop-latest'
$OllamaTorSrc = 'https://github.com/Codemaster64/onionmind/archive/refs/heads/main.tar.gz#subdirectory=ollama-tor'
$Here = if ($env:ONIONMIND_INSTALLER) { Split-Path -Parent $env:ONIONMIND_INSTALLER } else { (Get-Location).Path }
function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function GB($mb)  { '{0:0.0}' -f ($mb / 1000) }

$Dry = $false; $Yes = $false
foreach ($a in (("$env:ONIONMIND_INSTALLER_ARGS" -split '\s+') | Where-Object { $_ })) {
  switch ($a) {
    { $_ -in '-n','--dry-run' } { $Dry = $true; continue }
    { $_ -in '-y','--yes' }     { $Yes = $true; continue }
    { $_ -in '-h','--help','/?' } {
      Write-Host 'usage: onionmind-install.cmd [--dry-run] [--yes]'
      Write-Host '  --dry-run  show this device, the recommendation and the plan; change nothing'
      Write-Host '  --yes      install the recommended choices without asking'
      exit 0 }
    default { throw "unknown option: $a (try --help)" }
  }
}
function Finish($code) {
  if (-not $Yes) { Read-Host '   Press Enter to close' | Out-Null }
  exit $code
}

# --- 1. What is this device? ------------------------------------------------
# PROCESSOR_ARCHITEW6432 carries the real architecture under 32-bit PowerShell.
$Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { "$env:PROCESSOR_ARCHITECTURE" }
$Is64 = $Arch -in 'AMD64','ARM64'
$Ram = 0; $Gpus = @(); $Vram = 0; $Disk = 0; $OsName = 'Windows'
try { $Ram = [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB) } catch {}
try { $OsName = (Get-CimInstance Win32_OperatingSystem).Caption } catch {}
try { $Gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch {}
# Same probe as onionmind-setup.cmd, which makes the final pick: NVIDIA only.
try { $Vram = [int](@(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits)[0]) } catch {}
try {
  $root = [IO.Path]::GetPathRoot($(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $HOME }))
  $Disk = [int]((New-Object IO.DriveInfo $root).AvailableFreeSpace / 1MB)
} catch {}
$Notes = @()
if ($Vram -eq 0 -and ($Gpus -join ' ') -match 'AMD|Radeon|Intel') {
  $Notes += 'No NVIDIA GPU: the Windows engine setup uses NVIDIA VRAM only, so models run on the CPU here.'
}
if ($Arch -eq 'ARM64') { $Notes += 'Windows on ARM: the engine runs native, the desktop app under x64 emulation (slower).' }
if ($Vram -lt 6000) { $Notes += 'Small GPU: the app can run the models on your own SSH server instead - after installing, open Settings > Compute.' }

$Py = $null
foreach ($cand in @(@('py','-3'), @('python'))) {
  if (Have $cand[0]) {
    try {
      $pyArgs = @($cand | Select-Object -Skip 1) + @('-c', 'import sys; print(sys.version_info >= (3, 10))')
      if ((& $cand[0] @pyArgs 2>$null) -eq 'True') { $Py = $cand; break }
    } catch {}
  }
}
$Node = ''; try { if (Have node) { $Node = (& node -v).TrimStart('v') } } catch {}
$NodeOk = $false
try { $nv = [version]$Node; $NodeOk = $nv.Major -ge 24 -or ($nv.Major -eq 22 -and $nv.Minor -ge 19) } catch {}
$InstallDir = if ($env:ONIONMIND_DIR) { $env:ONIONMIND_DIR } else { "$env:LOCALAPPDATA\qwen" }
$Installed = (Have onionmind) -or (Test-Path (Join-Path $InstallDir 'onionmind.cmd'))

# --- 2. Which model fits ----------------------------------------------------
# Thresholds and files mirror onionmind-setup.cmd; sizes are the Hugging Face
# file sizes, checked 2026-10-12.
function Model-Info($want) {
  if ($want -eq '27b') {
    if     ($Vram -ge 17000) { return @{ Name = 'INFERNO 27B Q4_K_M + vision'; MB = 17700 } }
    elseif ($Vram -ge 12000) { return @{ Name = 'INFERNO 27B 3.69bpw + vision'; MB = 13500 } }
    else                     { return @{ Name = 'INFERNO 27B IQ2_M + vision'; MB = 10900 } }
  }
  return @{ Name = 'SPARK 2.6B (LFM2.5)'; MB = 1700 }
}
$RecModel = if ($Vram -ge 8000) { '27b' } else { 'fast' }
function Model-Line($want) {
  $m = Model-Info $want
  $line = "$want - $($m.Name), $(GB $m.MB) GB download"
  if ($want -eq '27b') {
    if ($Vram -lt 8000) { $line += '; runs mostly on CPU here (~1-2 tok/s)' }
    else { $line += "; fits your $(GB $Vram) GB of GPU memory" }
  } else { $line += '; quick replies, no image input' }
  if ($want -eq $RecModel) { $line += '  <- recommended' }
  $line
}

# --- 3. What can be installed here, and what we recommend ------------------
$Opts = New-Object System.Collections.ArrayList
function Add-Opt($id, $rec, $title, $why) { [void]$Opts.Add([pscustomobject]@{ Id = $id; Rec = $rec; Title = $title; Why = $why }) }

$why = ''
if (-not $Is64) { $why = "needs 64-bit Windows; this is $Arch" }
elseif (-not (Have winget)) { $why = 'needs winget - install "App Installer" from the Microsoft Store, then rerun' }
if ($why) { Add-Opt desktop na 'Onionmind desktop app + local model' $why }
elseif ($Installed) { Add-Opt desktop no 'Onionmind desktop app + local model' 'already installed - use Settings > Check for updates, or pick this to repair' }
else { Add-Opt desktop yes 'Onionmind desktop app + local model' 'native workbench, Tor search, the onionmind command and a desktop icon' }

if ($Is64) { Add-Opt portable no 'Portable app only (zip, no model)' 'Onionmind.exe in a folder of your choice; you manage Ollama and models yourself' }
else { Add-Opt portable na 'Portable app only (zip, no model)' "needs 64-bit Windows; this is $Arch" }

$r = if (-not $Is64 -and -not $Installed) { 'yes' } else { 'no' }   # can't install here? boot it instead
Add-Opt usb $r 'Matchstick USB stick (amnesic live system)' 'boots Onionmind from a stick and forgets everything; ERASES the stick'

if ($Py) {
  $r = if ((Have ollama) -and -not $Installed) { 'yes' } else { 'no' }
  Add-Opt ollamator $r 'ollama-tor (Tor routing for your own Ollama tools)' "fail-closed Tor proxy for 'ollama launch' integrations, no Onionmind needed"
} else { Add-Opt ollamator na 'ollama-tor (Tor routing for your own Ollama tools)' 'needs Python 3.10+' }

# --- 4. Show it ------------------------------------------------------------
function YN($c) { if (Have $c) { 'yes' } else { 'no' } }
Write-Host ''
Write-Host '   Onionmind installer - local AI with Tor search' -ForegroundColor Magenta
Write-Host ''
Write-Host '   This device'
Write-Host "     System   $OsName, $Arch"
Write-Host "     Memory   $(GB $Ram) GB RAM"
$gpuText = if ($Gpus) { $Gpus -join ', ' } else { 'none found' }
if ($Vram -gt 0) { $gpuText += ", $(GB $Vram) GB VRAM" }
Write-Host "     GPU      $gpuText"
Write-Host "     Disk     $(GB $Disk) GB free"
$nodeText = if ($Node) { $Node + $(if ($NodeOk) { ' (agent ready)' } else { '' }) } else { 'no' }
Write-Host "     Tools    python $(if ($Py) { 'yes' } else { 'no' }), ollama $(YN ollama), winget $(YN winget), node $nodeText"
if ($Notes) { Write-Host '   Notes'; foreach ($n in $Notes) { Write-Host "    - $n" } }
Write-Host ''
Write-Host '   What can be installed'
$Default = @()
for ($i = 0; $i -lt $Opts.Count; $i++) {
  $o = $Opts[$i]; $n = $i + 1
  switch ($o.Rec) {
    'yes' { Write-Host "    [$n] * $($o.Title)   " -NoNewline; Write-Host 'recommended' -ForegroundColor Green
            Write-Host "         $($o.Why)"; $Default += $n }
    'no'  { Write-Host "    [$n]   $($o.Title)"; Write-Host "         $($o.Why)" }
    'na'  { Write-Host "    [$n]   $($o.Title)"; Write-Host "         not available here: $($o.Why)" -ForegroundColor Yellow }
  }
  if ($o.Id -eq 'desktop' -and $o.Rec -ne 'na') { Write-Host "         model: $(Model-Line $RecModel)" }
}
Write-Host ''

# --- 5. Choose -------------------------------------------------------------
function Ask($prompt) { if ($Yes) { return '' }; (Read-Host "   $prompt").Trim() }
$defText = if ($Default) { $Default -join ' ' } else { 'nothing' }
function Pick($reply) {  # -> selected ids, or $null after a warning
  $picks = if ($reply) { $reply -split '[\s,]+' | Where-Object { $_ } } else { $Default }
  $ids = @()
  foreach ($p in $picks) {
    $k = 0
    if (-not [int]::TryParse("$p", [ref]$k) -or $k -lt 1 -or $k -gt $Opts.Count) { Warn "no option numbered $p"; return $null }
    $o = $Opts[$k - 1]
    if ($o.Rec -eq 'na') { Warn "option $k is not available on this device"; return $null }
    if ($ids -notcontains $o.Id) { $ids += $o.Id }
  }
  ,$ids
}
while ($true) {
  $reply = Ask "Install which? [numbers like 1 3, Enter = $defText, q = quit]"
  if ($reply -in 'q','Q') { Say 'nothing installed'; Finish 0 }
  $Sel = Pick $reply
  if ($null -ne $Sel) { break }
  if ($Yes) { Finish 1 }
}
if (-not $Sel) { Say 'nothing selected'; Finish 0 }

$Model = $RecModel
if ($Sel -contains 'desktop') {
  Write-Host ''; Write-Host '   Which model?'
  Write-Host "    [1] $(Model-Line 'fast')"
  Write-Host "    [2] $(Model-Line '27b')"
  while ($true) {
    $m = Ask 'Model? [number, Enter = recommended]'
    if (-not $m) { break }
    if ($m -in '1','fast') { $Model = 'fast'; break }
    if ($m -in '2','27b') { $Model = '27b'; break }
    Warn "no model '$m' - pick a number from the list"
  }
  $mi = Model-Info $Model
  $need = $mi.MB * 2 + 3000      # the weights stay on disk and Ollama keeps its own copy
  if ($Disk -lt $need) { Warn "only $(GB $Disk) GB free; $($mi.Name) wants about $(GB $need) GB - free space or pick a smaller model" }
}

# --- 6. Plan, then confirm --------------------------------------------------
function Source-Of($name) { $l = Join-Path $Here $name; if (Test-Path $l) { "$l (local copy)" } else { "$Base/$name" } }
Write-Host ''; Write-Host '   Plan'
foreach ($id in $Sel) {
  switch ($id) {
    'desktop'   { Write-Host "     - desktop app + $((Model-Info $Model).Name), via $(Source-Of 'onionmind-setup.cmd')" }
    'portable'  { Write-Host "     - portable app into $HOME\Onionmind, from $Base/Onionmind-Windows-x64.zip" }
    'usb'       { Write-Host "     - Matchstick USB writer, via $(Source-Of 'matchstick.cmd') (it asks before erasing anything)" }
    'ollamator' { Write-Host "     - ollama-tor, from $OllamaTorSrc" }
  }
}
Write-Host '   Direct (non-Tor) downloads: github.com for the installers; the installers then'
Write-Host '   fetch model weights from huggingface.co and programs through winget.'
Write-Host ''
if ($Dry) { Say 'dry run - nothing downloaded or changed'; Finish 0 }
if (-not $Yes) {
  $ok = (Read-Host '   Go ahead? [Y/n]').Trim()
  if ($ok -and $ok -notin 'y','Y','yes') { Say 'nothing installed'; Finish 0 }
}

# --- 7. Hand over -----------------------------------------------------------
$Tmp = Join-Path ([IO.Path]::GetTempPath()) "onionmind-install-$PID"
function Fetch($name, $dir) {
  $local = Join-Path $Here $name
  if (Test-Path $local) { return $local }
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $out = Join-Path $dir $name
  Say "Downloading $name"
  $ProgressPreference = 'SilentlyContinue'   # 5.1's progress bar slows the download itself ~10x
  Invoke-WebRequest -UseBasicParsing -Uri "$Base/$name" -OutFile $out
  $out
}
$Done = @(); $Failed = @()
foreach ($id in $Sel) {
  Write-Host ''; Say "Installing: $id"
  try {
    switch ($id) {
      'desktop' {
        $f = Fetch 'onionmind-setup.cmd' $Tmp
        $env:ONIONMIND_MODEL = $Model
        & $f
        if ($LASTEXITCODE -ne 0) { throw "onionmind-setup.cmd exited with $LASTEXITCODE" }
      }
      'portable' {
        $dest = Join-Path $HOME 'Onionmind'
        $zip = Fetch 'Onionmind-Windows-x64.zip' $Tmp
        Say "Extracting to $dest"
        Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
        # The bootstrap's default run is a read-only inventory: it reports what
        # is missing and prints the exact install command, changing nothing.
        $boot = Get-ChildItem -LiteralPath $dest -Recurse -Filter 'onionmind-bootstrap.cmd' | Select-Object -First 1
        if ($boot) { & $boot.FullName }
        else { Say "Onionmind.exe is in $dest - it needs Ollama and a model, which the desktop option installs" }
      }
      'usb' {
        # Not the temp dir: the writer keeps its downloaded image in usb\out next
        # to itself, so a rerun resumes instead of fetching 6.6 GB again.
        $f = Fetch 'matchstick.cmd' (Join-Path $HOME 'Onionmind-Matchstick')
        & $f
      }
      'ollamator' {
        $spec = "ollama-tor @ $OllamaTorSrc"
        if (Test-Path (Join-Path $Here 'ollama-tor\pyproject.toml')) { $spec = Join-Path $Here 'ollama-tor' }
        $pyArgs = @($Py | Select-Object -Skip 1) + @('-m', 'pip', 'install', '--user', $spec)
        & $Py[0] @pyArgs
        if ($LASTEXITCODE -ne 0) { throw "pip exited with $LASTEXITCODE" }
        Say "ollama-tor installed - run it as: $($Py -join ' ') -m ollama_tor"
      }
    }
    $Done += $id
  } catch { $Failed += $id; Warn "$id did not finish: $($_.Exception.Message)" }
}
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
Write-Host ''
if ($Done)   { Say "Done: $($Done -join ' ')" }
if ($Failed) { Warn "Failed: $($Failed -join ' ') - rerun this installer; finished steps are skipped"; Finish 1 }
Finish 0
#__END__
