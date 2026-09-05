#!/usr/bin/env bash
# Qwen3.8-27B uncensored + Tor web search, on Ollama. Arch, Ubuntu/Debian, and
# macOS (Homebrew). One paste.
# Re-runnable: skips what is already done and resumes partial downloads.
# ponytail: the macOS branch is syntax-checked (bash -n) and its Tor logic is
# unit-tested offline in tests/test_privacy_contracts.py, but no installer run
# has been observed on Apple hardware yet.
set -euo pipefail

# Weights live outside $HOME deliberately. Ollama runs as User=ollama under systemd:
#   - Arch's unit sets ProtectHome=yes, making /home structurally invisible to the
#     service. A ~/ path fails with a bare "no such file" and no chmod fixes it.
#   - Ubuntu's vendor unit has no ProtectHome, but home dirs are often mode 750.
# /var/lib is writable and visible on both (Arch's ProtectSystem=full only locks
# /usr /boot /etc), so one path works everywhere. On macOS ollama runs as the
# logged-in user under brew services, so that reasoning does not apply and the
# install stays in $HOME.
OS_NAME=$(uname -s)
if [ "$OS_NAME" = Darwin ]; then
  DIR="${ONIONMIND_DIR:-$HOME/.local/share/onionmind}"
else
  DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
fi
say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root - it calls sudo where needed"
if [ "$OS_NAME" != Darwin ]; then
  command -v systemctl >/dev/null || die "needs systemd"
fi

if [ "$OS_NAME" = Darwin ]; then DISTRO=mac
elif command -v pacman  >/dev/null 2>&1; then DISTRO=arch
elif command -v apt-get >/dev/null 2>&1; then DISTRO=debian
else die "unsupported system - needs Homebrew (macOS), pacman, or apt-get"; fi
say "Distro family: $DISTRO"

# --- 1. GPU -----------------------------------------------------------------
VRAM=0
if [ "$DISTRO" = mac ]; then
  GPU=metal
  # Unified memory is shared with macOS itself; treat ~5 GB as the OS+apps
  # floor so model picks are honest about what is actually free.
  MEM_MB=$(( $(sysctl -n hw.memsize) / 1048576 ))
  [ "$MEM_MB" -gt 5000 ] && VRAM=$(( MEM_MB - 5000 )) || VRAM=0
elif command -v nvidia-smi >/dev/null 2>&1; then
  VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
  GPU=nvidia
elif [ -d /sys/module/amdgpu ]; then
  GPU=amd
  command -v rocm-smi >/dev/null 2>&1 &&
    VRAM=$(rocm-smi --showmeminfo vram --csv 2>/dev/null | awk -F, 'NR==2{print int($2/1048576)}' || echo 0)
else
  GPU=none
fi
VRAM=${VRAM:-0}
say "GPU: $GPU, ${VRAM} MiB VRAM"
[ "$VRAM" -eq 0 ] && warn "no GPU VRAM detected - will run on CPU (slow). Check your drivers."

# --- 2. Packages ------------------------------------------------------------
# Python deps come from the distro, NOT pip: both Arch and Ubuntu 23.04+ mark the
# system Python externally-managed (PEP 668) and `pip install` refuses outright.
if [ "$DISTRO" = mac ]; then
  command -v brew >/dev/null 2>&1 || die "Homebrew is required on macOS - install it from brew.sh, then rerun"
  brew list --formula tor >/dev/null 2>&1 || { say "Installing tor (brew)"; brew install tor; }
  command -v ollama >/dev/null 2>&1 || { say "Installing ollama (brew)"; brew install ollama; }
  # No distro python packages here: CLT's python3 takes plain --user, brew's
  # PEP 668 python needs the override flag. Both are user-local, no sudo.
  if ! python3 -c 'import requests, socks' >/dev/null 2>&1; then
    python3 -m pip install --user --quiet requests PySocks ||
      python3 -m pip install --user --quiet --break-system-packages requests PySocks ||
      warn "python deps failed - run 'python3 -m pip install --user requests PySocks' and rerun"
  fi
elif [ "$DISTRO" = arch ]; then
  case $GPU in nvidia) OLLAMA_PKG=ollama-cuda ;; amd) OLLAMA_PKG=ollama-rocm ;; *) OLLAMA_PKG=ollama ;; esac
  # tkinter on Arch comes from `tk`, which `python` lists as an optional
  # dependency ("tk: for tkinter"). There has never been a python-tk package
  # here - asking for one aborts the whole transaction with "target not found",
  # so nothing installed. Debian is the odd one out with python3-tk.
  PKGS=(tor python-requests python-pysocks tk curl "$OLLAMA_PKG")
  MISSING=()
  for p in "${PKGS[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
  if [ ${#MISSING[@]} -gt 0 ]; then
    say "Installing: ${MISSING[*]}"
    sudo pacman -S --needed --noconfirm "${MISSING[@]}"
  fi
else
  # tor and python3-socks live in universe, which is not guaranteed enabled.
  if ! grep -rqs "^deb.*universe" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null &&
     ! grep -rqs "universe" /etc/apt/sources.list.d/*.sources 2>/dev/null; then
    say "Enabling universe"
    sudo apt-get install -y software-properties-common
    sudo add-apt-repository -y universe
  fi
  say "Installing packages"
  sudo apt-get update
  sudo apt-get install -y tor python3-requests python3-socks python3-tk python3-venv curl ca-certificates \
    libegl1 libgl1 libxcb-cursor0 libxkbcommon-x11-0
  # Ollama is not in apt. This is upstream's documented installer; fetched to a file
  # first so it can be read before it runs, rather than piped blind into a shell.
  if ! command -v ollama >/dev/null 2>&1; then
    # mktemp, not a fixed /tmp name: a predictable path in a world-writable
    # directory can be pre-created as a symlink, and `curl -o` follows it, so
    # another local user could redirect this write. The file is fetched rather
    # than piped so it CAN be inspected - ONIONMIND_SHOW_OLLAMA_SCRIPT=1 prints
    # it and waits, instead of only claiming that is possible.
    oi=$(mktemp) || die "could not create a temp file"
    trap 'rm -f "$oi"' EXIT
    say "Installing Ollama (upstream script -> $oi)"
    curl -fsSL https://ollama.com/install.sh -o "$oi"
    if [ "${ONIONMIND_SHOW_OLLAMA_SCRIPT:-0}" = 1 ]; then
      echo "--- ollama install.sh ---"; cat "$oi"; echo "--- end ---"
      printf 'Run it? [y/N] '; read -r ok
      case "$ok" in y|Y) ;; *) die "aborted before running the ollama installer" ;; esac
    fi
    sh "$oi"
  fi
fi
command -v ollama >/dev/null || die "ollama not on PATH after install"

# Resolve the configured location once before creating or changing it. Generated
# launchers must never inherit a relative path, and broad existing directories
# must never have their ownership or mode changed by this installer.
DIR=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$DIR")
HOME_REAL=$(python3 -c 'from pathlib import Path; print(Path.home().resolve())')
case "$DIR" in
  /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/lib|"$HOME_REAL")
    die "refusing broad ONIONMIND_DIR: $DIR"
    ;;
esac

# --- 3. Tor daemon ----------------------------------------------------------
# The daemon (SOCKS on 9050) beats Tor Browser here: no GUI, no window to keep open,
# and a service manager restarts it.
if [ "$DISTRO" = mac ]; then
  brew services start tor >/dev/null 2>&1 || warn "could not start the tor service - run 'brew services start tor'"
else
  systemctl is-active --quiet tor || { say "Starting tor"; sudo systemctl enable --now tor; }
fi
tor_up() { (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null && { exec 3<&- 3>&-; return 0; }; return 1; }
for _ in $(seq 1 40); do tor_up && break; sleep 2; done
if tor_up; then say "Tor SOCKS up on 9050"
else warn "tor not listening - start it ('brew services start tor' on macOS, 'sudo systemctl start tor' on Linux); search will refuse until it is"; fi

# --- 4. Ollama tuning + service --------------------------------------------
if [ "$DISTRO" = mac ]; then
  # ponytail: brew services has no drop-in environment story, so the Linux
  # flash-attention/KV-cache tuning below is skipped; ollama's Metal defaults
  # apply. Revisit if brew grows per-service env config.
  say "Starting ollama under brew services"
  brew services start ollama >/dev/null 2>&1 || warn "could not start ollama - run 'brew services start ollama'"
else
  # Ollama runs as a systemd service under its own user, so exporting these in your shell
  # does nothing - the server never sees them. They have to be a unit drop-in.
  say "Applying ollama tuning (systemd drop-in)"
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/10-tuning.conf >/dev/null <<'UNIT'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now ollama
  sudo systemctl restart ollama          # pick up the drop-in if it was already running
fi

for _ in $(seq 1 40); do
  curl -sf --noproxy '*' -m 3 http://127.0.0.1:11434/api/version >/dev/null && break
  sleep 2
done
curl -sf --noproxy '*' -m 3 http://127.0.0.1:11434/api/version >/dev/null \
  || die "ollama did not come up on 11434 ('brew services log ollama' on macOS, 'journalctl -u ollama -n50' on Linux)"

# --- 5. Pick the build that fits -------------------------------------------
# ONIONMIND_MODEL picks what gets installed:
#   auto (default) - the 27B where it fits in VRAM, the fast small model where it does not
#   fast           - always the small model, whatever the hardware
#   27b            - always Qwen3.8-27B, even if it runs on CPU at 1-2 tok/s
# There is NO small Qwen3.8: as of Aug 2026 the family is 27B and a 2.4T MoE, nothing else,
# and no generation newer than 3.8 exists. Qwen3.5 is the newest line WITH small dense
# models, so that is what "fast" means here.
# MTP builds keep the multi-token-prediction head; ollama uses it for speculative decoding.
WANT="${ONIONMIND_MODEL:-auto}"
case "$WANT" in
  auto) [ "$VRAM" -ge 8000 ] && WANT=27b || WANT=fast ;;
  fast|27b) ;;
  *) die "ONIONMIND_MODEL must be auto, fast or 27b (got '$WANT')" ;;
esac

VISION=0
if [ "$WANT" = 27b ]; then
  VISION=1                                   # the mmproj is built for the 27B architecture
  if   [ "$VRAM" -ge 17000 ]; then
    REPO=hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF; FILE=Qwen3.8-27B-abliterated-mtp-Q4_K_M.gguf
  elif [ "$VRAM" -ge 12000 ]; then
    REPO=soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf
    FILE=qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf
  else
    REPO=hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF; FILE=Qwen3.8-27B-abliterated-IQ2_M.gguf
  fi
  [ "$VRAM" -lt 8000 ] && warn "${VRAM} MiB VRAM: the 27B will run mostly on CPU (~1-2 tok/s)."
  MODEL_NAME=inferno
  say "Model: INFERNO (27B)"
else
  if [ "$VRAM" -ge 6000 ]; then
    REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF; FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf; SZ=9B
  else
    REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF; FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf; SZ=4B
  fi
  [ "$SZ" = 4B ] && MODEL_NAME=ember || MODEL_NAME=blaze
  [ "$SZ" = 4B ] && LABEL=EMBER || LABEL=BLAZE
  say "Model: $LABEL (Qwen3.5-$SZ) - fits entirely in VRAM"
  [ "$VRAM" -lt 8000 ] && warn "No small Qwen3.8 exists, so this is one generation back but far faster."
  warn "Vision skipped (27B-only). Set ONIONMIND_MODEL=27b for Qwen3.8-27B instead."
fi

# --- 6. Weights -------------------------------------------------------------
if [ "$DISTRO" = mac ]; then
  mkdir -p "$DIR"                      # ollama runs as this user; no service-user dance
else
  sudo mkdir -p "$DIR"
  sudo chown "$(id -u):$(id -g)" "$DIR"
  sudo chmod 755 "$DIR"                # traversable by the ollama service user
fi

# Huggingface publishes each LFS object's sha256 in X-Linked-ETag, so a 16GB
# download can be checked against the digest the host itself serves - no hash
# table to maintain here. Integrity only, not supply-chain pinning: it catches
# the truncated or corrupted file that otherwise shows up much later as an
# inscrutable model-load error. ONIONMIND_SKIP_VERIFY=1 opts out.
verify() {  # file url
  [ "${ONIONMIND_SKIP_VERIFY:-0}" = 1 ] && return 0
  command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || return 0
  want=$(curl -fsSLI "$2" 2>/dev/null | tr -d '\r' | awk 'tolower($1) == "x-linked-etag:" {gsub(/"/, "", $2); print $2}' | tail -1)
  case "$want" in
    *[!0-9a-f]* | "") return 0 ;;      # nothing published; nothing to check
  esac
  if command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$1" | cut -d' ' -f1)
  else got=$(shasum -a 256 "$1" | cut -d' ' -f1); fi
  if [ "$got" != "$want" ]; then
    rm -f "$1"
    die "$(basename "$1") downloaded corrupt (sha256 $got, expected $want) - deleted it; rerun to try again"
  fi
  say "verified $(basename "$1")"
}

say "Downloading $FILE (resumable, ~10-16GB)"
# ponytail: curl -C - resumes a dropped download; no retry logic of our own
WEIGHTS_URL="https://huggingface.co/$REPO/resolve/main/$FILE"
curl -L -C - --fail --noproxy '*' -o "$DIR/$FILE" "$WEIGHTS_URL"
verify "$DIR/$FILE" "$WEIGHTS_URL"
chmod 644 "$DIR/$FILE"

# --- 7. Model ---------------------------------------------------------------
# num_gpu 99 = all layers on GPU; ollama's auto-split is too conservative and silently
# leaves VRAM unused. It needs desktop headroom too: if speed swings while a browser is
# open, the model is spilling to shared memory - drop this to ~56.
# TEMPLATE is required: a bare GGUF import gets `{{ .Prompt }}`, which makes the model
# echo prompts and leak system text instead of chatting.
cat > "$DIR/Modelfile" <<MF
FROM $DIR/$FILE
PARAMETER num_gpu 99
PARAMETER num_ctx 8192
PARAMETER temperature 0.7
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>
"""
MF
say "Registering model"
ollama create "$MODEL_NAME" -f "$DIR/Modelfile"

# --- 7b. Vision (27B only - the mmproj is built for that architecture) -------
if [ "$VISION" = 1 ]; then
# The mmproj is the vision tower in its own file - architecture-specific, not
# quant-specific, so this one projector binds to any Qwen3.8-27B build (verified
# against the 3.69bpw MTP model it is paired with here). Shares the base blob, so
# it costs ~900MB on top, not another full model.
VIS=Qwen3.8-27B-Uncensored-vision-f16.gguf
say "Downloading vision projector (885 MiB)"
VIS_URL="https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$VIS"
curl -L -C - --fail --noproxy '*' -o "$DIR/$VIS" "$VIS_URL"
verify "$DIR/$VIS" "$VIS_URL"
chmod 644 "$DIR/$VIS"
# ponytail: just write the second Modelfile; editing the first one with sed needs a
# literal newline in the replacement, which sed rejects.
cat > "$DIR/Modelfile.vision" <<MFV
FROM $DIR/$FILE
FROM $DIR/$VIS
PARAMETER num_gpu 99
PARAMETER num_ctx 8192
PARAMETER temperature 0.7
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>
"""
MFV
say "Registering vision model"
ollama create "$MODEL_NAME-vision" -f "$DIR/Modelfile.vision" ||
    warn "vision model failed to build - text model is fine"
fi

# --- 8. Tor search tool -----------------------------------------------------
cat > "$DIR/onionmind.py" <<'PYEOF'
#!/usr/bin/env python3
"""Onionmind - a local uncensored model with web search over Tor.

  onionmind.py "one-shot question"    # NOTE: lands in your shell history
  onionmind.py                        # interactive - queries stay out of history

Needs a tor daemon on 9050 (systemctl start tor) or Tor Browser on 9150.
"""
import sys, os, re, html, json, secrets, shutil, socket, socketserver, subprocess, threading, time, urllib.parse, requests

for _s in (sys.stdout, sys.stderr):              # Windows console defaults to cp1252,
    try: _s.reconfigure(encoding="utf-8")        # which mangles en-dashes and km2
    except Exception: pass

OLLAMA = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
OLLAMA_PULL = "http://127.0.0.1:11434/api/pull"
LLAMA  = "http://127.0.0.1:8080/v1/chat/completions"   # llama.cpp llama-server
BACKEND = None
MODEL  = "inferno"
# ollama is local - never via Tor. "all" is not padding: requests fills a MISSING
# key from $ALL_PROXY via setdefault, so listing it as None is what actually stops
# the whole conversation being routed to whatever proxy the user has exported.
NOPROXY = {"http": None, "https": None, "all": None}
PORTS  = (9050, 9150)                            # 9050 = tor daemon, 9150 = Tor Browser
# Tor Browser's own UA. A unique UA is a fingerprint; blending into the herd is the point.
UA = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
# DuckDuckGo's onion service keeps every query inside the Tor network, so no
# exit node sees it and a failed onion request can never become a direct request.
ENDPOINT = "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/"
_THINK_TAG_PATTERN = r"<\s*(/?)\s*think(?:\s[^>]*)?>"
_THINK_TAG = re.compile(_THINK_TAG_PATTERN, re.IGNORECASE)
# Reasoning models spend the budget thinking BEFORE answering. A 9B needed 5514 tokens
# to reach its first word; capped lower it returns an empty string, which reads as a
# refusal but is just truncation.
NUM_PREDICT = 16384
NUM_CTX = 16384
FINAL_NUM_PREDICT = 4096
FINALIZE_PROMPT = (
    "The previous response reached its generation limit. Using the work already present "
    "above, answer the user's request now. Give the most useful concise best-effort answer, "
    "include any partial result, and state what remains unfinished. Do not output analysis, "
    "do not start over, and do not call tools."
)
INCOMPLETE_NOTE = "[Incomplete: generation limit reached. Continue to resume from this checkpoint.]"

_port = None
_bridge_port = None
_managed_tor_process = None
_tor_enabled = True


def tor_enabled():
    """Return whether this process currently permits Onionmind to use Tor."""
    return _tor_enabled


def set_tor_enabled(enabled):
    """Enable or fail-close Onionmind's Tor paths for this process.

    Turning Tor off is intentionally session-local. It clears the pinned
    circuit but does not kill a listener owned by another application; the
    desktop separately stops only the hidden Tor process this session owns.
    """
    global _tor_enabled, _port
    _tor_enabled = bool(enabled)
    if not _tor_enabled:
        _port = None


def tor_data_dirs():
    """Directories a managed Tor keeps its state, caches and logs in.

    Onionmind's own DataDirectory first, then the Tor Browser data directory
    it reuses on Windows. Read-only: nothing here starts or stops Tor - it
    exists so the desktop wipe knows what to destroy.
    """
    dirs = [os.path.join(os.path.expanduser("~"), ".onionmind", "tor")]
    try:
        roots = _tor_browser_roots()
    except Exception:
        roots = []
    for root in roots:
        dirs.append(os.path.join(root, "Browser", "TorBrowser", "Data", "Tor"))
    return [path for path in dirs if os.path.isdir(path)]


def tor_proxy_port():
    """Return a locally listening SOCKS port without making an internet request."""
    for port in PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return port
        except OSError:
            continue
    return None


def _tor_browser_roots():
    """Return likely Tor Browser roots using local Windows paths only."""
    roots = []
    override = os.environ.get("ONIONMIND_TOR_BROWSER")
    if override:
        roots.append(override)
    if os.name == "nt":
        try:
            import ctypes
            desktop = ctypes.create_unicode_buffer(32768)
            # CSIDL_DESKTOPDIRECTORY resolves OneDrive Known Folder Move too.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop) == 0:
                roots.append(os.path.join(desktop.value, "Tor Browser"))
        except (AttributeError, OSError, ValueError):
            pass
    roots.extend([
        os.path.join(os.path.expanduser("~"), "Desktop", "Tor Browser"),
        os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Tor Browser"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tor Browser"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tor Browser"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Tor Browser"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Tor Browser"),
        os.path.join(os.environ.get("ProgramW6432", ""), "Tor Browser"),
    ])
    unique = []
    seen = set()
    for root in roots:
        if not root:
            continue
        root = os.path.abspath(os.path.expandvars(os.path.expanduser(root)))
        if os.path.basename(root).lower() == "browser":
            root = os.path.dirname(root)
        key = os.path.normcase(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _await_tor_ready(timeout, stop_event=None):
    """Block until our managed tor answers on a SOCKS port, or explain why not."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            stop_managed_tor()
            raise RuntimeError("Background Tor start was stopped.")
        port = tor_proxy_port()
        if port:
            return port
        if _managed_tor_process is not None and _managed_tor_process.poll() is not None:
            break
        time.sleep(0.25)
    stop_managed_tor()
    raise RuntimeError("The background Tor process did not become ready.")


def _darwin_tor_binary():
    """Return a tor binary Onionmind may launch and own on macOS, or None."""
    found = shutil.which("tor")
    if not found:
        found = next(
            (candidate for candidate in ("/opt/homebrew/bin/tor", "/usr/local/bin/tor")
             if os.path.isfile(candidate)),
            None,
        )
    return found


def _start_darwin_tor(stop_event=None, timeout=30):
    """Launch Homebrew's tor with a generated torrc this session owns (macOS).

    The Tor Browser bundle layout is deliberately not parsed here; `brew
    install tor` is the documented path, and the installer normally runs it as
    a brew service - this is the fallback when nothing is listening. The torrc
    mirrors Android's ProcessManager (SOCKS on 9050, a private data dir, no
    control cookie), and stop_managed_tor() still only ever touches the
    process started below.
    """
    global _managed_tor_process
    tor = _darwin_tor_binary()
    if tor is None:
        raise RuntimeError(
            "No tor binary found. Install it with 'brew install tor' (or start "
            "your Tor service), then enable Tor search again."
        )
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("Background Tor start was stopped.")
    data = os.path.join(os.path.expanduser("~"), ".onionmind", "tor")
    os.makedirs(data, exist_ok=True)
    torrc = os.path.join(data, "torrc")
    with open(torrc, "w", encoding="utf-8") as handle:
        handle.write(
            "SocksPort 9050\n"
            f"DataDirectory \"{data}\"\n"
            "CookieAuthentication 0\n"
            "AvoidDiskWrites 1\n"
        )
    _managed_tor_process = subprocess.Popen(
        [tor, "-f", torrc],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return _await_tor_ready(timeout, stop_event)


def start_tor_hidden(timeout=30, stop_event=None):
    """Start Tor Browser's Tor daemon without opening a browser or console window.

    Called only after the user opts into Tor search. Existing Tor proxies are
    reused and never adopted or stopped by Onionmind. On macOS there is no
    Tor Browser bundle to mine; Homebrew's tor is launched under a torrc we
    own instead.
    """
    global _managed_tor_process
    if not _tor_enabled:
        raise RuntimeError(
            "Tor proxy is turned off in Onionmind. Turn it on from the desktop toolbar first."
        )
    existing = tor_proxy_port()
    if existing:
        return existing
    if _managed_tor_process is not None:
        stop_managed_tor()
    if os.name != "nt":
        if sys.platform == "darwin":
            return _start_darwin_tor(stop_event=stop_event, timeout=timeout)
        raise RuntimeError("Start the local Tor service, then enable Tor search again.")

    for root in _tor_browser_roots():
        browser = os.path.join(root, "Browser")
        tor_exe = os.path.join(browser, "TorBrowser", "Tor", "tor.exe")
        data = os.path.join(browser, "TorBrowser", "Data", "Tor")
        defaults = os.path.join(data, "torrc-defaults")
        torrc = os.path.join(data, "torrc")
        if not all(os.path.isfile(path) for path in (tor_exe, defaults, torrc)):
            continue
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Background Tor start was stopped.")
        startupinfo = None
        if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
            # CREATE_NO_WINDOW covers console-subsystem executables. Explicitly
            # hiding the startup window also covers launchers that otherwise
            # briefly inherit or create a visible console on Windows.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        _managed_tor_process = subprocess.Popen(
            [tor_exe, "--defaults-torrc", defaults, "-f", torrc,
             "--DisableNetwork", "0", "--SocksPort", "9150 IsolateSOCKSAuth"],
            cwd=browser,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=startupinfo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _await_tor_ready(timeout, stop_event)
    raise RuntimeError("Tor Browser's background Tor process was not found. Install Tor Browser first.")


def stop_managed_tor():
    """Stop only the hidden Tor process that this Onionmind session started."""
    global _managed_tor_process, _port
    _port = None
    process, _managed_tor_process = _managed_tor_process, None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _proxies(port, isolate):
    # Distinct SOCKS credentials => Tor builds a SEPARATE circuit. Without this every
    # search shares one exit node and they can be trivially linked to each other.
    cred = f"{secrets.token_hex(8)}:x@" if isolate else ""
    return {s: f"socks5h://{cred}127.0.0.1:{port}" for s in ("http", "https")}
    # socks5h (not socks5) also resolves DNS through Tor; plain socks5 leaks every hostname.


def tor_check():
    """Pin the Tor port, or exit. Fails closed - never falls back to a direct connection."""
    global _port
    if not _tor_enabled:
        _port = None
        sys.exit("Tor proxy is turned off in Onionmind. Turn it on before using a protected feature.")
    for port in PORTS:
        try:
            r = requests.get("https://check.torproject.org/api/ip",
                             proxies=_proxies(port, False), timeout=30).json()
        except Exception:
            continue
        if r.get("IsTor"):
            # The desktop may turn Tor off while this request is in flight.
            # Check on both sides of the pin; set_tor_enabled(False) also clears
            # it, so every possible ordering ends fail-closed.
            if not _tor_enabled:
                _port = None
                sys.exit("Tor proxy was turned off before verification completed.")
            _port = port
            if not _tor_enabled:
                _port = None
                sys.exit("Tor proxy was turned off before verification completed.")
            print(f"[tor] active, exit {r.get('IP')} (port {port})", file=sys.stderr)
            return
        print(f"[tor] port {port} responded but is NOT Tor - refusing", file=sys.stderr)
    # A stale-but-set port must not survive a failed reverification: callers
    # would keep "verifying" against a proxy that just answered "not Tor".
    _port = None
    # Telling a Windows user to run systemctl is telling them nothing. Name the
    # thing that works on the platform without exposing implementation ports.
    if os.name == "nt":
        sys.exit("No working Tor connection found. Start Tor Browser, click "
                 "Connect, then try again.")
    sys.exit("No working Tor connection found. Start the Tor service, then try again.")


def strip_tag(name):
    """"inferno:latest" -> "inferno". ollama's /api/tags always reports a tag;
    MODEL never carries one, so raw comparisons between the two never matched."""
    return name[:-7] if name.endswith(":latest") else name


def _clean(x):
    # Collapse ALL whitespace, newlines included. web_search emits three lines
    # per result and dsh-onionmind-tor-search.js strides through them 3 at a
    # time; one wrapped snippet used to shift every later result onto the wrong
    # title - the same silent mis-citation parse_results exists to prevent,
    # reintroduced at the serialisation boundary.
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()


def parse_results(page, n=5):
    """Extract (title, snippet, url) from a DuckDuckGo HTML page.

    Parsed per result BLOCK, not by zipping two separate findall lists. A result
    with no snippet used to shift every later snippet onto the wrong title, which
    is silent and produces confidently mismatched citations.
    """
    blocks = re.split(r'<div[^>]*\bclass="[^"]*\bresult\b[^"]*"', page)[1:]
    out, seen = [], set()
    for b in blocks:
        m = re.search(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        url = urllib.parse.unquote(m.group(1))
        if "uddg=" in url:                       # DDG wraps results in a redirector
            url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("uddg", [url])[0]
        if not url.startswith("http") or url in seen:
            continue
        ms = re.search(r'result__snippet[^>]*>(.*?)</a>', b, re.S)
        seen.add(url)
        out.append((_clean(m.group(2)), _clean(ms.group(1)) if ms else "", url))
        if len(out) >= n:
            break
    return out


def web_search(query, n=5):
    """One attempt = one fresh Tor circuit. A 200 with zero results is a failure too."""
    err = None
    for _ in range(2):                           # each attempt gets a fresh circuit
        try:
            resp = requests.post(ENDPOINT, data={"q": query}, headers={"User-Agent": UA},
                                 proxies=_proxies(_port, True), timeout=90)
            resp.raise_for_status()
        except Exception as e:
            err = e
            continue
        hits = parse_results(resp.text, n)
        if hits:                                 # empty 200 == rate-limited or reshaped
            print(f"[tor] searched {query!r} -> {len(hits)} results", file=sys.stderr)
            return "\n".join(f"- {t}\n  {s}\n  {u}" for t, s, u in hits)
        err = "empty result page"
    print(f"[tor] search failed for {query!r}: {err}", file=sys.stderr)
    return f"(search failed on the onion service after fresh-circuit retries: {err})"


def _think_tag_candidate(candidate):
    """Classify text beginning with ``<`` against prefixes of _THINK_TAG."""
    if not candidate.startswith("<"):
        return "invalid", False
    index, size = 1, len(candidate)
    while index < size and candidate[index].isspace():
        index += 1
    if index == size:
        return "prefix", False

    closing = candidate[index] == "/"
    if closing:
        index += 1
        while index < size and candidate[index].isspace():
            index += 1
        if index == size:
            return "prefix", True

    for expected in "think":
        if index == size:
            return "prefix", closing
        if re.fullmatch(expected, candidate[index], re.IGNORECASE) is None:
            return "invalid", closing
        index += 1
    if index == size:
        return "prefix", closing
    if candidate[index] == ">":
        match = _THINK_TAG.fullmatch(candidate[:index + 1])
        return ("complete", bool(match.group(1))) if match else ("invalid", closing)
    if not candidate[index].isspace():
        return "invalid", closing
    index += 1
    while index < size:
        if candidate[index] == ">":
            match = _THINK_TAG.fullmatch(candidate[:index + 1])
            return ("complete", bool(match.group(1))) if match else ("invalid", closing)
        index += 1
    return "prefix", closing


def _partial_think_tag(text):
    """Return the first unfinished reasoning tag and whether it is closing."""
    start = text.find("<")
    while start >= 0:
        state, closing = _think_tag_candidate(text[start:])
        if state == "prefix":
            return start, closing
        start = text.find("<", start + 1)
    return None


def strip_thinking(text):
    """Remove every complete or truncated reasoning block, failing closed."""
    visible, cursor, depth = [], 0, 0
    for tag in _THINK_TAG.finditer(text):
        closing = bool(tag.group(1))
        if closing:
            if depth:
                depth -= 1
                if depth == 0:
                    cursor = tag.end()
            else:
                visible.clear()                 # implicit leading reasoning block
                cursor = tag.end()
            continue
        if depth == 0:
            visible.append(text[cursor:tag.start()])
        depth += 1

    if depth == 0:
        tail = text[cursor:]
        partial = _partial_think_tag(tail)
        if partial is None:
            visible.append(tail)
        elif partial[1]:
            visible.clear()                     # truncated implicit closing tag
        else:
            visible.append(tail[:partial[0]])
    return "".join(visible).strip()


TOOLS = [{"type": "function", "function": {
    "name": "web_search",
    "description": "Search the web for current information. Use for anything recent, factual, "
                   "or that you are unsure about. Returns titles, snippets and URLs. "
                   "Answer from the snippets rather than searching repeatedly.",
    "parameters": {"type": "object", "required": ["query"],
                   "properties": {"query": {"type": "string", "description": "search terms"}}}}}]


def _wire_messages(messages):
    """Remove client-only metadata before sending saved history to Ollama."""
    return [{key: value for key, value in message.items() if not key.startswith("_")}
            for message in messages]


def _ask_ollama(messages, num_predict=NUM_PREDICT, think=None, allow_search=False):
    body = {"model": MODEL, "messages": _wire_messages(messages), "stream": False,
            "options": {"num_predict": num_predict, "num_ctx": NUM_CTX}}
    if allow_search:
        body["tools"] = TOOLS
    if think is not None:
        body["think"] = think
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800,
                          json=body)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Ollama is not running on 127.0.0.1:11434. Start it, then retry.")
    if r.status_code == 404:
        sys.exit(f"Model {MODEL!r} is not installed. See what is: ollama list")
    if not r.ok:
        sys.exit(f"Ollama returned {r.status_code}: {r.text[:200]}")
    payload = r.json()
    message = dict(payload["message"])
    if payload.get("done_reason"):
        message["_done_reason"] = payload["done_reason"]
    return message


def _ask_ollama_stream(messages, on_text, stop_event=None, num_predict=NUM_PREDICT,
                       think=None, allow_search=False):
    """Stream one Ollama response while retaining tool-call compatibility."""
    body = {"model": MODEL, "messages": _wire_messages(messages), "stream": True,
            "options": {"num_predict": num_predict, "num_ctx": NUM_CTX}}
    if allow_search:
        body["tools"] = TOOLS
    if think is not None:
        body["think"] = think
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800, stream=True,
                          json=body)
    except requests.exceptions.ConnectionError:
        sys.exit("Ollama is not running on 127.0.0.1:11434. Start it, then retry.")
    if r.status_code == 404:
        sys.exit(f"Model {MODEL!r} is not installed. See what is: ollama list")
    if not r.ok:
        sys.exit(f"Ollama returned {r.status_code}: {r.text[:200]}")
    message = {"role": "assistant", "content": ""}
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if stop_event is not None and stop_event.is_set():
                return {"role": "assistant", "content": "", "stopped": True}
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue          # a partial or non-JSON line must not kill the stream
            chunk = event.get("message") or {}
            content = chunk.get("content") or ""
            if content:
                message["content"] += content
                on_text(content)
            thinking = chunk.get("thinking") or ""
            if thinking:
                message["thinking"] = message.get("thinking", "") + thinking
            if chunk.get("tool_calls"):
                message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
            if event.get("done"):
                if event.get("done_reason"):
                    message["_done_reason"] = event["done_reason"]
                break
    finally:
        r.close()
    return message


def detect_backend():
    """Prefer ollama; fall back to llama.cpp's llama-server. Ollama has no
    Android build, so phones run llama-server with the same GGUFs."""
    global BACKEND
    for url, name in (("http://127.0.0.1:11434/api/version", "ollama"),
                      ("http://127.0.0.1:8080/health", "llama-server")):
        try:
            if requests.get(url, proxies=NOPROXY, timeout=3).ok:
                BACKEND = name
                resolve_model()          # MODEL may name a tier this box never built
                return
        except Exception:
            pass
    sys.exit("No model server on 11434 (ollama) or 8080 (llama-server). Start one.")


def resolve_model():
    """Point MODEL at something that is actually installed.

    MODEL defaults to the tier the installer WOULD have built ("inferno"), and
    the installer names whatever it built after the GPU it found - so a machine
    that got a different tier, or a user who pulled a model by hand, ends up
    asking ollama for a name it has never heard of. Every entry point then dies
    on the same opaque 404 from deep inside a stream. Pick an installed model
    and say so instead.

    ponytail: first installed model wins, derivatives last. Ranking them by
    size or capability needs a catalogue this does not have; if picking wrong
    becomes a real complaint, sort by the tier list in run_ui's picker.
    """
    global MODEL
    if BACKEND != "ollama":
        return MODEL                             # llama-server serves whatever it loaded
    names = [strip_tag(n) for n in installed_models()]
    if not names or MODEL in names:
        return MODEL                             # nothing to go on, or already right
    # -code and -vision are built FROM another model; as a default they are a
    # worse answer than the model they came from.
    plain = [n for n in names if not n.endswith(("-code", "-vision"))]
    missing, MODEL = MODEL, (plain or names)[0]
    print(f"[model] {missing} is not installed - using {MODEL}", file=sys.stderr)
    return MODEL


def installed_models():
    """Return locally installed Ollama model names for the model picker."""
    try:
        r = requests.get(OLLAMA_TAGS, proxies=NOPROXY, timeout=3)
        if not r.ok:
            return []
        return [m["name"] for m in r.json().get("models", []) if m.get("name")]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return []


def pull_model(name, on_progress=None, stop_event=None):
    """Pull a model through the local model service, reporting byte progress."""
    r = requests.post(OLLAMA_PULL, proxies=NOPROXY, timeout=1800, stream=True,
                      json={"name": name, "stream": True})
    if not r.ok:
        raise RuntimeError(r.text[:200] or f"model download failed ({r.status_code})")
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if stop_event is not None and stop_event.is_set():
                return False
            if not raw:
                continue
            data = json.loads(raw)
            total, completed = data.get("total"), data.get("completed")
            if on_progress and total:
                on_progress((completed or 0) / total, data.get("status", "downloading"))
            if data.get("error"):
                raise RuntimeError(data["error"])
    finally:
        r.close()
    return True


def user_error(exc):
    """Keep the local runtime's name out of the product-facing desktop UI.

    Model names are left alone: the UI states which model is running and what it
    costs to run, so renaming it in an error message only hides the answer.
    """
    return (str(exc).replace("Ollama", "model service")
            .replace("ollama", "model service"))


def _to_openai(messages):
    """Translate our ollama-shaped history into OpenAI shape for llama-server,
    where each tool reply must reference the assistant's call by id. Ids are
    positional: each tool message binds to the next unread call of the
    assistant message preceding it."""
    out, slot = [], 0
    for m in messages:
        if m.get("role") == "tool":
            out.append({"role": "tool", "tool_call_id": f"tc{slot}", "content": m["content"]})
            slot += 1
            continue
        calls = m.get("tool_calls")
        if calls:
            slot = 0
            translated = {"role": "assistant", "content": m.get("content") or None,
                          "tool_calls": [{"id": f"tc{i}", "type": "function",
                                          "function": {"name": f["function"]["name"],
                                                       "arguments": json.dumps(f["function"].get("arguments") or {})}}
                                         for i, f in enumerate(calls)]}
            if m.get("reasoning_content"):
                translated["reasoning_content"] = m["reasoning_content"]
            out.append(translated)
        else:
            translated = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("reasoning_content"):
                translated["reasoning_content"] = m["reasoning_content"]
            out.append(translated)
    return out


def _ask_llama(messages, num_predict=NUM_PREDICT, think=None, allow_search=False):
    body = {"messages": _to_openai(messages), "stream": False,
            "max_tokens": num_predict}
    if allow_search:
        body["tools"] = TOOLS
    if think is False:
        body["chat_template_kwargs"] = {"enable_thinking": False}
        body["reasoning_effort"] = "none"
    try:
        r = requests.post(LLAMA, proxies=NOPROXY, timeout=1800,
                          json=body)
    except requests.exceptions.ConnectionError:
        sys.exit("llama-server is not running on 127.0.0.1:8080. Start it, then retry.")
    if not r.ok:
        sys.exit(f"llama-server returned {r.status_code}: {r.text[:200]}")
    choice = r.json()["choices"][0]
    m = choice["message"]
    msg = {"role": "assistant", "content": m.get("content") or ""}
    if m.get("reasoning_content"):
        msg["reasoning_content"] = m["reasoning_content"]
    if choice.get("finish_reason"):
        msg["_done_reason"] = choice["finish_reason"]
    calls = []
    for c in m.get("tool_calls") or []:
        args = c["function"].get("arguments")
        if isinstance(args, str):                    # OpenAI ships arguments as a JSON string
            try:
                args = json.loads(args)
            except ValueError:
                args = {"query": args}
        calls.append({"function": {"name": c["function"]["name"], "arguments": args or {}}})
    if calls:
        msg["tool_calls"] = calls
    return msg


def _limited(msg):
    return str(msg.get("_done_reason") or "").lower() in ("length", "max_tokens")


def _mark_incomplete(answer):
    answer = (answer or "").strip()
    return f"{answer}\n\n{INCOMPLETE_NOTE}" if answer else INCOMPLETE_NOTE


def _checkpoint_reasoning(msg):
    reasoning = msg.get("thinking") or msg.get("reasoning_content") or ""
    if reasoning:
        return reasoning
    raw = msg.get("content") or ""
    if "<think>" in raw and "</think>" not in raw:
        return raw.split("<think>", 1)[1]
    return ""


def _compact_answer(messages, answer):
    messages[-1] = {"role": "assistant", "content": answer}


def _recover_answer(messages, first_answer, stop_event=None, on_text=None):
    """Use the exhausted response once, then persist only a compact checkpoint."""
    if stop_event is not None and stop_event.is_set():
        return "(stopped)"
    recovery_history = [*messages, {"role": "user", "content": FINALIZE_PROMPT}]
    if BACKEND == "llama-server":
        recovered = _ask_llama(recovery_history, num_predict=FINAL_NUM_PREDICT,
                               think=False, allow_search=False)
    elif on_text is not None:
        recovered = _ask_ollama_stream(
            recovery_history, on_text, stop_event, num_predict=FINAL_NUM_PREDICT,
            think=False, allow_search=False)
    else:
        recovered = _ask_ollama(recovery_history, num_predict=FINAL_NUM_PREDICT,
                                think=False, allow_search=False)

    if recovered.get("stopped"):
        return "(stopped)"
    answer = strip_thinking(recovered.get("content") or "")
    if answer:
        if _limited(recovered):
            answer = _mark_incomplete(answer)
        _compact_answer(messages, answer)
        return answer

    if first_answer:
        answer = _mark_incomplete(first_answer)
        _compact_answer(messages, answer)
        return answer

    answer = ("[Incomplete: both local generation passes ended before a final answer. "
              "The unfinished state is saved; send 'continue' to resume.]")
    first = messages[-1]
    checkpoint = {"role": "assistant", "content": answer}
    reasoning = _checkpoint_reasoning(first)
    if reasoning:
        key = "reasoning_content" if BACKEND == "llama-server" else "thinking"
        checkpoint[key] = reasoning
    messages[-1] = checkpoint
    return answer


def turn(messages, stop_event=None, allow_search=False):
    """Run one user turn; external search happens only after explicit opt-in."""
    for _ in range(6):                            # ponytail: hard cap, not a retry policy
        if stop_event is not None and stop_event.is_set():
            return "(stopped)"
        msg = (_ask_llama(messages, allow_search=allow_search) if BACKEND == "llama-server"
               else _ask_ollama(messages, allow_search=allow_search))
        messages.append(msg)
        calls = msg.get("tool_calls")
        if not calls:
            answer = strip_thinking(msg.get("content") or "")
            if not answer or _limited(msg):
                return _recover_answer(messages, answer, stop_event)
            _compact_answer(messages, answer)
            return answer
        for c in calls:
            if stop_event is not None and stop_event.is_set():
                return "(stopped)"
            fn = c["function"]
            args = fn.get("arguments") or {}
            if fn["name"] == "web_search":
                if not allow_search:
                    result = "(web search was not allowed for this turn)"
                else:
                    tor_check()
                    result = web_search(args.get("query", ""))
            else:
                result = f"(unknown tool {fn['name']})"
            messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
    return "(gave up after 6 tool rounds)"


def turn_stream(messages, on_text, stop_event=None, on_event=None, allow_search=False):
    """Run a turn with live text and optional structured tool activity.

    The extra callback is deliberately optional so existing CLI, installer, and
    Android callers keep the same interface.  Native desktop clients can use it
    to render real tool state without scraping transcript text.
    """
    if BACKEND != "ollama":
        return turn(messages, stop_event, allow_search=allow_search)
    for _ in range(6):
        if stop_event is not None and stop_event.is_set():
            return "(stopped)"
        msg = _ask_ollama_stream(messages, on_text, stop_event, allow_search=allow_search)
        if msg.get("stopped"):
            return "(stopped)"
        messages.append(msg)
        calls = msg.get("tool_calls")
        if not calls:
            answer = strip_thinking(msg.get("content") or "")
            if not answer or _limited(msg):
                return _recover_answer(messages, answer, stop_event, on_text)
            _compact_answer(messages, answer)
            return answer
        for c in calls:
            if stop_event is not None and stop_event.is_set():
                return "(stopped)"
            fn = c["function"]
            args = fn.get("arguments") or {}
            denied = fn["name"] == "web_search" and not allow_search
            if denied and on_event:
                on_event({"kind": "tool_refused", "name": fn.get("name", "unknown"),
                          "arguments": args})
            elif on_event:
                on_event({"kind": "tool_started", "name": fn.get("name", "unknown"),
                          "arguments": args})
            if fn["name"] == "web_search":
                if denied:
                    result = "(web search was not allowed for this turn)"
                else:
                    tor_check()
                    if on_event:
                        on_event({"kind": "tor_verified", "port": _port})
                    result = web_search(args.get("query", ""))
            else:
                result = f"(unknown tool {fn['name']})"
            if on_event and not denied:
                on_event({"kind": "tool_finished", "name": fn.get("name", "unknown"),
                          "result": result})
            messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
    return "(gave up after 6 tool rounds)"


def _save(history, path):
    """Write the conversation so far to a file - the print workflow's front end.
    The file lives wherever the user put it; power-off deletes it with the rest."""
    lines = []
    for m in history:
        if m.get("role") == "user":
            content = m.get("content") if isinstance(m.get("content"), str) else "[image]"
            lines.append("you> " + content)
        elif m.get("role") == "assistant":
            c = strip_thinking(m.get("content") or "")
            if c:
                lines.append("onion> " + c)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")
    print(f"[saved] {path} ({len(lines)} entries)")


# --- coding agent -----------------------------------------------------------
# Qwen Code is the harness; everything that makes it Onionmind's is here, so there
# is one program to install and one place Tor is verified. Three things have to be
# true and none of them are its defaults: the model is the local Ollama one, the
# only way off this machine is Tor, and one instance gets a budget big enough to
# finish a job instead of the stock 32k that dead-ends a session mid-task.
# The installed chat model is num_ctx 8192 (onionmind-setup.cmd) - fine for a
# conversation, useless for an agent whose system prompt and tool schemas eat most
# of it before the task even starts. THIS is the number that decides whether a
# complex job is possible; the session limit set from it is only a guard rail on
# top. Bigger costs KV-cache memory, so it is one knob: turn it down if the model
# starts spilling to CPU.
CODE_CTX = 32768

# The stops the slider offers. Powers of two because the KV cache is sized from
# this, so the odd values in between buy nothing and just make the control fiddly.
CODE_STEPS = (8192, 16384, 32768, 65536, 131072)

# One file, so the CLI, the Tk window and the workbench cannot disagree about
# what the budget currently is. Lives beside the net log for the same reason: it
# is agent state, not app state, and survives reinstalling either UI.
CODE_CTX_FILE = os.path.join(os.path.expanduser("~"), ".onionmind", "code-ctx")


def code_ctx():
    """The budget in force right now: the env override, else the saved one, else
    the default. Clamped to the slider's range - a hand-edited 2000000 would
    otherwise build a model that cannot load, and the failure surfaces much
    later as an inscrutable ollama error."""
    raw = os.environ.get("ONIONMIND_CODE_CTX")
    if not raw:
        try:
            with open(CODE_CTX_FILE, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return CODE_CTX
    try:
        return min(max(int(str(raw).strip()), CODE_STEPS[0]), CODE_STEPS[-1])
    except ValueError:
        return CODE_CTX


def set_code_ctx(value):
    """Persist the budget and hand back what was actually stored."""
    value = min(max(int(value), CODE_STEPS[0]), CODE_STEPS[-1])
    os.makedirs(os.path.dirname(CODE_CTX_FILE), exist_ok=True)
    with open(CODE_CTX_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(value))
    return value

# The agent owns the console it runs in - a full-screen TUI - so network activity
# goes to a file rather than stderr, where it would draw straight over the UI.
NET_LOG = os.path.join(os.path.expanduser("~"), ".onionmind", "agent-net.log")


def _net_log(line):
    """One line per thing that leaves this machine. Never raises: a full disk
    must not take the agent's network down with it."""
    try:
        os.makedirs(os.path.dirname(NET_LOG), exist_ok=True)
        with open(NET_LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def _dial(host, port):
    """Socket to host:port - direct for loopback, a fresh Tor circuit otherwise."""
    if host in ("127.0.0.1", "localhost", "::1"):
        # Never left the machine, so never over Tor - but written down anyway: a
        # local port that forwards off this machine would ride exactly this path
        # and the log is the only place it would show up.
        _net_log(f"local    {host}:{port}")
        return socket.create_connection((host, port), 30)
    if not _port:
        # Without a pinned port PySocks quietly defaults to 1080, which is
        # whatever happens to be listening there. Refuse instead.
        raise OSError("Tor is not verified for this session - refusing to connect")
    import socks                                 # PySocks; requests already needs it
    s = socks.socksocket()
    # Distinct SOCKS credentials => a SEPARATE circuit, exactly as _proxies() does.
    s.set_proxy(socks.SOCKS5, "127.0.0.1", _port, rdns=True,
                username=secrets.token_hex(8), password="x")
    s.settimeout(120)
    s.connect((host, port))                      # rdns=True: the exit resolves, not us
    _net_log(f"tor      {host}:{port}")          # loopback is not logged: it never left
    return s


def _pipe(src, dst):
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


class _TorBridge(socketserver.BaseRequestHandler):
    """An HTTP proxy that only knows how to leave via Tor.

    Node cannot speak SOCKS - not qwen-code, not undici, nothing in that tree - so
    an HTTP proxy in front of it is the ONLY seam where the whole harness, its MCP
    children and every `curl` its shell tool runs can be forced onto Tor. A host it
    cannot tunnel gets a 502, never a direct connection: the fail-closed rule from
    tor_check(), applied to somebody else's process.
    """

    def handle(self):
        sock = self.request
        f = sock.makefile("rb", 0)               # unbuffered: readline must not
        line = f.readline(8192)                  # swallow the body behind it
        if not line:
            return
        try:
            method, target = (p.decode("latin1") for p in line.split()[:2])
        except ValueError:
            return
        try:
            if method == "CONNECT":              # https: an opaque tunnel
                host, _, port = target.rpartition(":")
                while f.readline(8192) not in (b"\r\n", b"\n", b""):
                    pass                         # drain the request headers
                up = _dial(host.strip("[]"), int(port))
                sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                first = b""
            else:                                # plain http: absolute-form request
                u = urllib.parse.urlsplit(target)
                up = _dial(u.hostname or "", u.port or 80)
                first = line                     # RFC 7230: origins MUST accept it as-is
        except Exception as exc:
            _net_log(f"REFUSED  {target}: {exc}")
            try:
                sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
            return
        if first:
            up.sendall(first)
        threading.Thread(target=_pipe, args=(up, sock), daemon=True).start()
        _pipe(sock, up)
        up.close()


def start_tor_bridge():
    """Serve the bridge on a random loopback port and return the port.

    Idempotent: the desktop UI stays open across many agent runs, and one
    listener per run would pile up for the life of the window."""
    global _bridge_port
    if _bridge_port is None:
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _TorBridge)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _bridge_port = srv.server_address[1]
    return _bridge_port


def tor_fetch(url, limit=200000):
    """One page on a fresh circuit, flattened to text."""
    r = requests.get(url, headers={"User-Agent": UA},
                     proxies=_proxies(_port, True), timeout=90)
    r.raise_for_status()
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", r.text)
    print(f"[tor] fetched {url}", file=sys.stderr)
    return _clean(body)[:limit]


MCP_TOOLS = [
    {"name": "web_search",
     "description": "Search the web over Tor. Returns titles, snippets and URLs. "
                    "Answer from the snippets rather than searching repeatedly.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "search terms"}}}},
    {"name": "web_fetch",
     "description": "Fetch one URL over Tor and return its text.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string", "description": "absolute http(s) URL"}}}},
]


def run_mcp():
    """One MCP stdio server, two tools, both over Tor.

    The web_search/web_fetch qwen-code ships are denied in the settings run_code
    writes - they reach for a provider we do not control. This is the only web the
    coding agent gets, and tor_check() refuses to serve without a verified circuit,
    so "it searched outside Tor by accident" is not a reachable state.
    """
    tor_check()
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue                             # a notification - nothing to answer
        if method == "initialize":
            reply = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                     "capabilities": {"tools": {}},
                     "serverInfo": {"name": "onionmind-tor", "version": "1"}}
        elif method == "tools/list":
            reply = {"tools": MCP_TOOLS}
        elif method == "tools/call":
            args = params.get("arguments") or {}
            try:
                text = (web_search(args["query"]) if params.get("name") == "web_search"
                        else tor_fetch(args["url"]))
            except Exception as exc:
                text = f"(failed over Tor: {user_error(exc)})"
            reply = {"content": [{"type": "text", "text": text}]}
        else:
            reply = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": reply}) + "\n")
        sys.stdout.flush()


def _proxy_env(proxy):
    """Every proxy variable a child might read, pointed at one place.

    Casing is not cosmetic: curl reads http_proxy in LOWERCASE ONLY for plain
    http, so uppercase alone leaves http requests unproxied on POSIX. NO_PROXY is
    blanked because an inherited one is a hole straight off Tor - undici,
    curl and requests all honour it, and qwen-code's dispatcher is an undici
    EnvHttpProxyAgent. Windows env names are case-insensitive, so one case there.
    ponytail: this covers every child that respects proxy env - curl, git, npm,
    pip, node. One that opens its own socket still bypasses it; closing THAT
    needs a firewall rule or a container, not an environment variable.
    """
    env = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        value = "" if name == "NO_PROXY" else proxy
        env[name] = value
        if os.name != "nt":
            env[name.lower()] = value
    # Node's fetch ignores proxy env entirely without this - a bare `node -e
    # "fetch(...)"` leaked the real IP on v24 with every variable above already
    # set. The agent lives in a node ecosystem, so this is not a corner case.
    env["NODE_USE_ENV_PROXY"] = "1" if proxy else "0"
    return env


def _settings_path(root):
    return os.path.join(root, ".qwen", "settings.json")


def _read_settings(path):
    try:
        with open(path, encoding="utf-8") as fh:
            conf = json.load(fh)
    except (OSError, ValueError):                # missing, or hand-edited to junk
        return {}
    return conf if isinstance(conf, dict) else {}


def _write_settings(path, conf):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(conf, fh, indent=2)


def _code_model(ctx):
    """The chat model, seen through a context worth coding in.

    `ollama create` layers on top of the blobs that are already there, so this
    costs a manifest rather than a second copy of the weights. Only ollama can do
    it: llama-server fixes its context with -c when it starts, so on Android the
    agent gets whatever that was, and says so instead of pretending.
    """
    import subprocess
    import tempfile

    if BACKEND != "ollama" or MODEL.endswith("-code"):
        return MODEL
    name = MODEL + "-code"
    fh = tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False,
                                     encoding="utf-8")
    with fh:
        fh.write(f"FROM {MODEL}\nPARAMETER num_ctx {ctx}\n")
    try:
        # ollama's progress output is UTF-8; decoding it as the Windows console
        # codepage throws inside communicate()'s reader thread and loses the error.
        done = subprocess.run(["ollama", "create", name, "-f", fh.name],
                              capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        done = None
        detail = str(exc)
    finally:
        try:
            os.unlink(fh.name)
        except OSError:
            pass
    if done is not None and not done.returncode:
        return name
    if done is not None:
        lines = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
        detail = lines[-1].strip() if lines else f"ollama create exited {done.returncode}"
    print(f"[onionmind] could not build a {ctx:,}-token view of {MODEL}: {detail}",
          file=sys.stderr)
    print(f"[onionmind] falling back to {MODEL} as installed", file=sys.stderr)
    return MODEL


# Shell commands that reach the network WITHOUT honouring a proxy. curl, wget,
# git-over-https, npm, pip and node are deliberately absent: they read the proxy
# variables, so they are already on Tor. These cannot be pointed anywhere, so the
# agent does not get to run them.
# ponytail: ssh could be routed with a ProxyCommand rather than refused. Denied
# because git-over-https covers the real use, and a ProxyCommand means a quoted
# nested command line on Windows - its own bug farm.
NO_PROXY_COMMANDS = ("ping", "ping6", "tracert", "traceroute", "nslookup", "dig",
                     "host", "nc", "ncat", "netcat", "telnet", "ftp", "tftp",
                     "ssh", "scp", "sftp", "rsync", "bitsadmin", "certutil",
                     "nmap", "socat")

# The hole no environment variable can close: code that opens its own socket.
# Everything with a reason to reach the network already has a proxy, and that
# proxy is on loopback - so a socket to anywhere else is something going around
# Tor, and it is refused rather than routed. Injected into the agent's python
# (sitecustomize, imported by site at startup) and node (--require) children.
PY_SHIM = '''# Onionmind: this interpreter may only open loopback sockets. Anything
# that should reach the network goes through the proxy in HTTPS_PROXY, which is
# on 127.0.0.1 and exits via Tor. A socket to any other address is bypassing
# that, so it fails here instead of leaving the machine.
import socket

_LOCAL = ("127.0.0.1", "::1", "localhost", "")
_OFF = "onionmind: direct network access is off - go through HTTPS_PROXY (Tor)"
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def _local(address):
    host = address[0] if isinstance(address, tuple) else None
    return host is None or str(host) in _LOCAL


def connect(self, address):
    if not _local(address):
        raise OSError(_OFF)
    return _connect(self, address)


def connect_ex(self, address):
    if not _local(address):
        raise OSError(_OFF)
    return _connect_ex(self, address)


def getaddrinfo(host, *args, **kwargs):
    # Resolving a name is already a packet to the ISP's resolver saying what the
    # agent is doing. Tor resolves at the exit instead, so names never get here.
    if host is not None and str(host) not in _LOCAL:
        raise socket.gaierror(_OFF)
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
'''

JS_SHIM = """// Onionmind: this node process may only open loopback sockets. Anything that
// should reach the network goes through the proxy in HTTPS_PROXY (127.0.0.1,
// exits via Tor); undici and every http library connect to it, so they still
// work. A socket to any other address is bypassing Tor and fails here.
const net = require('net');
const dns = require('dns');
const LOCAL = new Set(['127.0.0.1', '::1', 'localhost', '']);
const OFF = 'onionmind: direct network access is off - go through HTTPS_PROXY (Tor)';
const connect = net.Socket.prototype.connect;

net.Socket.prototype.connect = function (...args) {
  // net.connect() hands the prototype an ALREADY-NORMALIZED [options, cb] array,
  // and an array is typeof 'object' - reading .host off it gave undefined, which
  // read as "no host given" and let every net.connect(port, ip) straight out.
  const a = Array.isArray(args[0]) ? args[0] : args;
  const o = (a[0] && typeof a[0] === 'object') ? a[0]
          : { port: a[0], host: typeof a[1] === 'string' ? a[1] : undefined };
  // Node defaults a missing host to localhost, so undefined really is loopback.
  const host = o.host === undefined ? '127.0.0.1' : String(o.host);
  if (o.path === undefined && !LOCAL.has(host)) {
    throw new Error(OFF);
  }
  return connect.apply(this, args);
};

// Same reason as the python shim: a lookup is a packet that says what the agent
// is doing. Tor resolves at the exit, so nothing legitimate resolves here.
for (const name of ['lookup', 'resolve', 'resolve4', 'resolve6']) {
  const real = dns[name];
  if (!real) continue;
  dns[name] = function (host, ...rest) {
    if (!LOCAL.has(String(host))) {
      const cb = rest[rest.length - 1];
      const err = new Error(OFF);
      if (typeof cb === 'function') return cb(err);
      throw err;
    }
    return real.call(dns, host, ...rest);
  };
}
"""


# The ceiling on all of the above, printed wherever the agent starts rather than
# left in TECHNICAL.md. Every layer here is environment handed to a process
# running as the user, so anything that does not read that environment is not
# covered. Only the kernel covers it.
CONTAINMENT_CEILING = (
    "[onionmind] ceiling: this is enforced by the agent's ENVIRONMENT, not the OS.\n"
    "[onionmind]      A compiled binary, `python -S`, or a tool that ignores proxies\n"
    "[onionmind]      still reaches the network directly. Only an OS egress rule -\n"
    "[onionmind]      firewall by user, container, netns - or the Matchstick live USB\n"
    "[onionmind]      closes that for every runtime at once."
)


def _write_shims():
    """Drop the two shims next to the log and return (PYTHONPATH dir, node file).

    ponytail: covers python and node, the two runtimes a coding agent reaches
    for. A compiled binary, or `python -S`, still goes straight out; only an OS
    egress rule - firewall by user, container, network namespace - closes that.
    """
    shim_dir = os.path.join(os.path.dirname(NET_LOG), "shims")
    os.makedirs(shim_dir, exist_ok=True)
    node_shim = os.path.join(shim_dir, "no-direct-net.js")
    with open(os.path.join(shim_dir, "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write(PY_SHIM)
    with open(node_shim, "w", encoding="utf-8") as fh:
        fh.write(JS_SHIM)
    return shim_dir, node_shim


def _contain_env(env):
    """Point the agent's children at the shims, keeping what was already set."""
    shim_dir, node_shim = _write_shims()
    env["PYTHONPATH"] = os.pathsep.join(
        [shim_dir] + [p for p in [env.get("PYTHONPATH", "")] if p])
    # NODE_OPTIONS is parsed with shell escaping, so a Windows path arrives with
    # its backslashes eaten: C:Usersnaits... Node takes forward slashes anywhere.
    node_shim = node_shim.replace(os.sep, "/")
    node_options = env.get("NODE_OPTIONS", "")
    env["NODE_OPTIONS"] = (f'--require "{node_shim}" ' + node_options).strip()
    return env


# --- DeepSeek Harness, the shipped coding agent -------------------------------
# Three launchers used to start it - the Tk button, the desktop workbench and the
# installed `onionmind-code` script - and all three ran `ollama launch dsh`
# straight out of the user's environment: no Tor, no containment, only the search
# PLUGIN routed. Everything below is the one place that starts it, so there is a
# single place Tor is verified for the agent, exactly as run_code() is for qwen.


def agent_argv(model=None, task=None, executable=None):
    """The Agent-mode command: Qwen Code, non-interactive when given a task.

    This used to be `ollama launch dsh -- --profile headless <task>`. The
    shipped DSH no longer takes a task or a profile at all - it is a browser-UI
    server now, and that argv exits 1 with "unknown option '--profile'" - so
    Agent mode had no working agent behind it. Qwen Code is the agent this file
    already contains and tests, and its -p mode streams to stdout, which is what
    the desktop's process reader expects.

    ponytail: no --yolo flag exists on this qwen build; the approval mode is a
    settings key instead, written by qwen_setup(). Passing it here would be a
    second way to set the same thing, and the settings file is the one the
    running agent actually reads.
    """
    argv = list(executable) if executable else qwen_launcher()
    if model:
        argv += ["--model", model]
    if task:                                     # no task = interactive session
        argv += ["-p", task]
    return argv


def agent_env(env=None):
    """The environment the agent runs in: Tor is the way out, or there isn't one.

    tor_check() exits when no verified circuit exists, so "the agent started but
    Tor was down" is not a reachable state. What follows is the containment
    run_code() gives qwen-code, applied to the harness instead: every child that
    reads proxy variables lands on the Tor bridge, and its python and node may
    only open loopback sockets, so code that dials its own socket fails instead
    of leaving directly.

    ponytail: the harness's shell tool can still run `ping`/`nslookup`, which
    ignore proxies and are refused for qwen-code through its permissions file.
    Denying them here needs the harness's own config schema; an OS egress rule
    (firewall by user, container, netns) closes it for every runtime at once.
    """
    # Same contract as the chat's search paths: try the hidden tor.exe this file
    # owns first, then fail closed if no circuit can be verified. Nothing here
    # ever launches Tor Browser's firefox.exe.
    start_tor_hidden()
    tor_check()                                  # fails closed before anything starts
    env = dict(os.environ if env is None else env)
    # The search plugin shells back into this file; without these it silently
    # reports itself unavailable and the harness falls back to its own provider.
    env["ONIONMIND_PY"] = os.path.abspath(__file__)
    env["ONIONMIND_PYTHON"] = sys.executable or ("python" if os.name == "nt" else "python3")
    env.update(_proxy_env(f"http://127.0.0.1:{start_tor_bridge()}"))
    return _contain_env(env)


def run_agent(task=None, model=None, yolo=False, workdir=None):
    """Run the coding agent over Tor and return its exit code.

    qwen_setup() writes the settings the agent reads (deny list, approval mode,
    proxy) and agent_env() re-verifies Tor and applies the containment, so both
    halves of the boundary are composed before the process starts.
    """
    import subprocess

    global MODEL
    # _code_model() derives the coding variant from the module-level MODEL, so
    # the asked-for model has to land there BEFORE setup runs. Rebinding the
    # local instead silently ran whatever MODEL already was.
    if model:
        MODEL = model
    workdir = os.path.abspath(workdir or os.getcwd())
    env, model, _ctx, _proxy = qwen_setup(workdir, yolo=yolo)  # SystemExit if no Tor
    print("[onionmind] agent web: Tor only. Its search goes over Tor, everything it")
    print("[onionmind]      runs inherits the proxy, and its python and node may only")
    print("[onionmind]      open loopback sockets. Refuses to start when Tor is down.")
    if yolo:
        print("[onionmind] YOLO: file edits and shell commands run WITHOUT asking.")
        print("[onionmind]      The network boundary is unchanged - denied commands are")
        print("[onionmind]      still refused and everything still leaves through Tor.")
    else:
        print("[onionmind] approvals: on. Protected actions wait to be approved, and")
        print("[onionmind]      stop safely where there is nobody to ask.")
    print(f"[onionmind]      Everything it sends out is logged to {NET_LOG}")
    print(CONTAINMENT_CEILING)
    print()
    # Piped stdout is block-buffered, so without this the desktop's process
    # reader shows nothing until the agent has already produced output.
    sys.stdout.flush()
    return subprocess.call(agent_argv(model, task), env=env, cwd=workdir)


def spawn_code(workdir, model=None):
    """Start run_code() in a terminal of its own. It is a full-screen TUI, so
    without one it has nowhere to draw and the window closes instantly."""
    import shutil
    import subprocess

    cmd = [sys.executable, os.path.abspath(__file__), "--code", workdir]
    if model:
        cmd += ["--model", model]
    if os.name == "nt":
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        if shutil.which(term):
            return subprocess.Popen([term, "-e"] + cmd)
    raise OSError("No terminal emulator found. Run this instead:\n\n"
                  "  onionmind --code " + workdir)


def qwen_launcher():
    """The argv prefix that starts Qwen Code, or exit saying how to install it."""
    import shutil

    qwen = shutil.which("qwen")
    if not qwen:
        sys.exit("Qwen Code is missing. Install it with:"
                 + os.linesep + "  npm install -g @qwen-code/qwen-code")
    # CreateProcess cannot execute the .cmd shim npm writes on Windows.
    return ([os.environ.get("COMSPEC", "cmd.exe"), "/c", qwen]
            if qwen.lower().endswith((".cmd", ".bat")) else [qwen])


def qwen_setup(workdir, ctx=None, yolo=False):
    """Write Qwen Code's settings and build its environment.

    The one place the agent's boundary is composed, shared by the terminal
    session (run_code) and Agent mode (run_agent), so a change to the deny list
    or the proxy cannot reach one and miss the other.
    """
    ctx = ctx or code_ctx()
    detect_backend()
    # agent_env() is the funnel: it starts the hidden Tor, verifies a circuit and
    # exits if none, points every proxy variable at the bridge and installs the
    # socket shims. Composing it here rather than beside it means the terminal
    # session and Agent mode cannot drift apart, and run_code gains the hidden
    # Tor start it never had - it used to verify a circuit and give up.
    env = agent_env()
    proxy = f"http://127.0.0.1:{start_tor_bridge()}"   # idempotent, same port
    model = _code_model(ctx)

    # Both backends expose an OpenAI-compatible /v1; Android has no Ollama, so
    # pointing this at OLLAMA unconditionally sent the agent to a dead port there.
    base = (OLLAMA.rsplit("/api/", 1)[0] + "/v1" if BACKEND == "ollama"
            else LLAMA.rsplit("/chat/", 1)[0])

    settings = {
        # The key is never checked but qwen-code will not select the provider
        # without one.
        "security": {"auth": {"selectedType": "openai"}},
        # The guard rail, not the budget: qwen compares this against the CURRENT
        # prompt, so anything above the context window can never fire.
        "model": {"name": model, "sessionTokenLimit": ctx},
        # Compact at 85% of the context so a long job survives the window instead
        # of hitting the budget wall on turn twenty.
        "context": {"autoCompactThreshold": 0.85},
        # qwen's own web tools reach a provider we do not control; the shell
        # commands cannot be pointed at a proxy at all. Both are hard denials -
        # the tool call is refused, not queued for approval.
        "permissions": {"deny": ["web_search", "web_fetch"] +
                        [f"run_shell_command({name})" for name in NO_PROXY_COMMANDS]},
        # YOLO stops qwen asking before it edits a file or runs a command. It
        # does NOT reach the deny list above: those stay hard denials whichever
        # mode is set (checked against qwen-code - a denied tool is still
        # refused with permission_mode "yolo" in force), and it cannot touch the
        # proxy variables or the socket shims at all, because those are
        # environment rather than policy. YOLO widens what the agent may do to
        # this machine, never where it may reach off it.
        "tools": {"approvalMode": "yolo" if yolo else "default"},
        "privacy": {"usageStatisticsEnabled": False},
        "telemetry": {"enabled": False},
        "proxy": proxy,
    }
    mcp = {"onionmind": {
        "command": sys.executable,
        "args": [os.path.abspath(__file__), "--mcp"],
        "trust": True,
        # Blank the proxy for our own child: it dials Tor's SOCKS port itself,
        # and sending that through the HTTP bridge would be a loop.
        "env": _proxy_env(""),
    }}

    # The Tor server goes in the USER settings, not the project's. qwen gates
    # project- and workspace-scoped MCP servers behind an interactive approval
    # prompt no matter what "trust" says, and a session started from the GUI has
    # nobody to answer it - the agent would simply come up with no web at all.
    # It is install-level anyway: same script, same Tor, every project.
    user = _settings_path(os.path.expanduser("~"))
    conf = _read_settings(user)
    conf.setdefault("mcpServers", {}).update(mcp)   # keep the user's own servers
    _write_settings(user, conf)

    path = _settings_path(workdir)
    merged = _read_settings(path)                # keep whatever the project already set
    merged.update(settings)

    _write_settings(path, merged)
    env.update(OPENAI_API_KEY="onionmind", OPENAI_MODEL=model, OPENAI_BASE_URL=base)
    return env, model, ctx, proxy


def run_code(workdir, ctx=None, yolo=False):
    """Qwen Code on the local model, with Tor the only way out, in a real terminal."""
    import subprocess

    launch = qwen_launcher()
    env, model, ctx, proxy = qwen_setup(workdir, ctx, yolo)

    print(f"[onionmind] coding agent: {model} on {BACKEND}, editing files in {workdir}")
    if model.endswith("-code"):
        print(f"[onionmind] context {ctx:,} tokens - the Context slider changes it")
    else:                                        # derived model unavailable
        print(f"[onionmind] context: whatever {model} was installed with, which is"
              " small for coding")
    print(f"[onionmind] web: Tor only ({proxy}). Its own web tools are denied, its")
    print( "[onionmind]      search and fetch go through Tor, everything it runs inherits")
    print( "[onionmind]      the proxy, commands that cannot be proxied are refused, and")
    print( "[onionmind]      its python and node may only open loopback sockets.")
    print(CONTAINMENT_CEILING)
    print( "[onionmind] you can watch it work in this window; everything it sends")
    print(f"[onionmind]      out is logged to {NET_LOG}\n")
    resume = []
    while True:
        code = subprocess.call(launch + resume, cwd=workdir, env=env)
        # ponytail: this asks on every exit, not only when the session ran out of
        # room - qwen-code reports that inside its TUI and gives no exit code for
        # it. Parse the ~/.qwen session log here if it ever needs to be exact.
        try:
            answer = input(f"\n[onionmind] session ended (exit {code}).\n"
                           f"  [c] continue where it left off    [a] abandon > ")
        except (EOFError, KeyboardInterrupt):
            break
        if not answer.strip().lower().startswith("c"):
            break
        resume = ["--continue"]                  # same session, same context


def run_legacy_ui():
    """Run the Windows desktop chat without putting conversation in a console."""
    import base64
    import os
    import subprocess
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        DND_FILES, TkinterDnD = None, None

    global MODEL
    preference_dir = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    preference_path = os.path.join(preference_dir, "onionmind", "model.txt")
    try:
        with open(preference_path, encoding="utf-8") as preference:
            MODEL = preference.read().strip() or MODEL
    except OSError:
        pass

    root = (TkinterDnD.Tk if TkinterDnD else tk.Tk)()
    root.title("Onionmind")
    root.geometry("760x700")
    root.minsize(560, 480)
    root.configure(bg="#0e0b12")

    purple, dim, panel, line, text = "#7d4698", "#b9aec4", "#17121d", "#2a2133", "#e8e2ee"
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", background=purple, foreground="white", borderwidth=0,
                    padding=(14, 8), font=("Segoe UI", 10, "bold"))
    style.map("TButton", background=[("active", "#9255ad"), ("disabled", "#3b2d44")])

    header = tk.Frame(root, bg="#0e0b12", padx=22, pady=18)
    header.pack(fill="x")
    tk.Label(header, text="◉  Onionmind", bg="#0e0b12", fg=text,
             font=("Segoe UI", 18, "bold")).pack(side="left")
    status = tk.Label(header, text="starting…", bg="#0e0b12", fg=dim,
                      font=("Segoe UI", 10))
    status.pack(side="right", pady=4)
    model_var = tk.StringVar(value=MODEL)
    model_box = ttk.Combobox(header, textvariable=model_var, state="readonly", width=22)
    model_box.pack(side="right", padx=(10, 8), pady=2)
    model_box.configure(values=(MODEL,))
    model_use = ttk.Button(header, text="Use model", state="disabled")
    model_use.pack(side="right", pady=2)
    install_model_button = ttk.Button(header, text="Install model", state="disabled")
    install_model_button.pack(side="right", padx=(0, 8), pady=2)
    tk.Frame(root, bg=line, height=1).pack(fill="x")

    transcript = scrolledtext.ScrolledText(
        root, wrap="word", state="disabled", bg="#0e0b12", fg=text,
        insertbackground=text, relief="flat", borderwidth=0, padx=24, pady=22,
        font=("Segoe UI", 11), spacing3=8)
    transcript.pack(fill="both", expand=True)
    transcript.tag_configure("you", foreground="#e7c8f2", font=("Segoe UI", 11, "bold"))
    transcript.tag_configure("onion", foreground=text)
    transcript.tag_configure("meta", foreground=dim, font=("Segoe UI", 9))

    bottom = tk.Frame(root, bg=panel, padx=16, pady=14)
    bottom.pack(fill="x")
    image_path = [None]
    attach = ttk.Button(bottom, text="Attach image")
    attach.pack(side="left", padx=(0, 10))
    question = tk.Entry(bottom, bg="#211a29", fg=text, insertbackground=text,
                        relief="flat", font=("Segoe UI", 11),
                        highlightthickness=1, highlightbackground=line,
                        highlightcolor=purple)
    question.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 10))
    send = ttk.Button(bottom, text="Send")
    send.pack(side="right")
    stop = ttk.Button(bottom, text="Stop", state="disabled")
    stop.pack(side="right", padx=(0, 8))
    actions = tk.Frame(root, bg="#0e0b12", padx=22, pady=9)
    actions.pack(fill="x")
    save = ttk.Button(actions, text="Save conversation")
    save.pack(side="left")
    coding = ttk.Button(actions, text="Coding agent")
    coding.pack(side="left", padx=(10, 0))
    image_label = tk.Label(actions, text="", bg="#0e0b12", fg="#d8b8e5",
                           font=("Segoe UI", 9))
    image_label.pack(side="left", padx=12, pady=5)
    remove_image = ttk.Button(actions, text="Remove image", state="disabled")
    remove_image.pack(side="left", pady=2)
    allow_search_var = tk.BooleanVar(value=False)
    allow_search = tk.Checkbutton(
        actions,
        text="Allow Tor search this turn",
        variable=allow_search_var,
        bg="#0e0b12",
        fg=dim,
        activebackground="#0e0b12",
        activeforeground=text,
        selectcolor=panel,
        font=("Segoe UI", 9),
    )
    allow_search.pack(side="right", padx=(12, 0), pady=5)
    hint = tk.Label(actions, text="Local-only unless you opt in",
                    bg="#0e0b12", fg=dim, font=("Segoe UI", 9))
    hint.pack(side="right", pady=5)

    history = []
    busy = False
    stop_event = threading.Event()
    stream_raw = [""]
    stream_start = [None]

    def populate_models(models):
        # ollama reports "inferno:latest"; MODEL is plain "inferno". Compared raw,
        # an installed model never matched - the picker listed it twice and the
        # vision auto-switch below decided the vision model was not installed.
        choices = list(dict.fromkeys(strip_tag(m) for m in (models or [MODEL])))
        if MODEL not in choices:
            choices.insert(0, MODEL)
        model_box.configure(values=choices, state="readonly")
        model_var.set(MODEL)
        model_use.configure(state="normal" if BACKEND == "ollama" else "disabled")
        install_model_button.configure(state="normal" if BACKEND == "ollama" else "disabled")

    def choose_model(reset=True):
        """reset=False keeps the conversation: used by the automatic switch to the
        vision model, where discarding what the user was doing is not a choice
        they made. An explicit model change still starts fresh - a new model
        cannot make sense of another model's context."""
        global MODEL
        selected = strip_tag(model_var.get().strip())
        if not selected or selected == MODEL:
            return
        MODEL = selected
        try:
            os.makedirs(os.path.dirname(preference_path), exist_ok=True)
            with open(preference_path, "w", encoding="utf-8") as preference:
                preference.write(MODEL)
        except OSError:
            pass
        if reset:
            history.clear()
            append("onion", f"Now using {MODEL}. New conversation started.")
        else:
            append("onion", f"Switched to {MODEL} to read the image.")
        set_status(f"ready · {MODEL}", "#9ef0b0")

    def install_model():
        name = simpledialog.askstring("Install model", "Model name:", parent=root)
        if not name or BACKEND != "ollama" or busy:
            return
        stop_event.clear()
        install_model_button.configure(state="disabled")
        model_use.configure(state="disabled")
        set_status(f"downloading {name}…", dim)

        def progress(value, stage):
            root.after(0, lambda: set_status(f"{stage} · {value:.0%}", dim))

        def work():
            try:
                pull_model(name.strip(), progress, stop_event)
                models = installed_models()
                root.after(0, lambda: populate_models(models))
                root.after(0, lambda: set_status("model installed", "#9ef0b0"))
            except Exception as exc:
                root.after(0, lambda: set_status(user_error(exc), "#e39a9a"))
            finally:
                root.after(0, lambda: install_model_button.configure(
                    state="normal" if BACKEND == "ollama" else "disabled"))

        threading.Thread(target=work, daemon=True).start()

    def select_image(path):
        path = path.strip().strip("{}")
        if not os.path.isfile(path):
            return
        image_path[0] = path
        image_label.configure(text=os.path.basename(path))
        remove_image.configure(state="normal")
        choices = [strip_tag(c) for c in model_box["values"]]
        # Prefer THIS model's vision build if there is one, else the 27B's.
        wanted = [MODEL] if MODEL.endswith("-vision") else [MODEL + "-vision", "inferno-vision"]
        vision = next((v for v in wanted if v in choices or v == MODEL), None)
        if vision is None:
            append("onion", "Install a vision model to ask questions about images.")
            clear_image()
        elif vision != MODEL:
            model_var.set(vision)
            choose_model(reset=False)      # keep the conversation the image belongs to

    def attach_image():
        path = filedialog.askopenfilename(
            title="Choose an image", filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.gif"),
                ("All files", "*.*")])
        if not path:
            return
        select_image(path)

    def clear_image():
        image_path[0] = None
        image_label.configure(text="")
        remove_image.configure(state="disabled")

    def append(role, value):
        transcript.configure(state="normal")
        transcript.insert("end", ("You\n" if role == "you" else "Onionmind\n"), role)
        transcript.insert("end", value + "\n\n", "onion")
        transcript.configure(state="disabled")
        transcript.see("end")

    def stream_begin():
        stream_raw[0] = ""
        transcript.configure(state="normal")
        transcript.insert("end", "Onionmind\n", "onion")
        stream_start[0] = transcript.index("end-1c")
        transcript.insert("end", "\n", "onion")
        transcript.configure(state="disabled")
        transcript.see("end")

    def stream_update(chunk):
        stream_raw[0] += chunk
        visible = strip_thinking(stream_raw[0])
        if stream_start[0] is None:
            return
        transcript.configure(state="normal")
        transcript.delete(stream_start[0], "end")
        transcript.insert("end", visible + "\n", "onion")
        transcript.configure(state="disabled")
        transcript.see("end")

    def stream_finish(answer):
        if stream_start[0] is None:
            append("onion", answer)
            return
        transcript.configure(state="normal")
        transcript.delete(stream_start[0], "end")
        transcript.insert("end", answer + "\n\n", "onion")
        transcript.configure(state="disabled")
        transcript.see("end")
        stream_start[0] = None

    def set_status(value, color=dim):
        status.configure(text=value, fg=color)

    def start():
        try:
            detect_backend()
            tor_check()
            models = installed_models() if BACKEND == "ollama" else []
            root.after(0, lambda: populate_models(models))
            root.after(0, lambda: set_status(f"ready · {MODEL} · Tor connected", "#9ef0b0"))
            root.after(0, lambda: append("onion", f"Ready with {MODEL}. Ask anything."))
        except (Exception, SystemExit) as exc:
            root.after(0, lambda: set_status("not ready", "#e39a9a"))
            root.after(0, lambda: append("onion", user_error(exc)))

    def ask():
        nonlocal busy
        value = question.get().strip()
        if not value or busy:
            return
        question.delete(0, "end")
        append("you", value)
        message = {"role": "user", "content": value}
        if image_path[0]:
            try:
                with open(image_path[0], "rb") as image_file:
                    message["images"] = [base64.b64encode(image_file.read()).decode("ascii")]
            except OSError as exc:
                append("onion", "Could not read image: " + str(exc))
                return
            clear_image()
        history.append(message)
        search_allowed = bool(allow_search_var.get())
        allow_search_var.set(False)
        busy = True
        stop_event.clear()
        send.configure(state="disabled")
        stop.configure(state="normal")
        allow_search.configure(state="disabled")
        set_status("thinking…", dim)
        stream_begin()

        def work():
            nonlocal busy
            try:
                if search_allowed:
                    start_tor_hidden(stop_event=stop_event)
                    root.after(0, lambda: set_status("Checking Tor…", "#d6b879"))
                answer = turn_stream(
                    history,
                    lambda chunk: root.after(0, lambda chunk=chunk: stream_update(chunk)),
                    stop_event,
                    allow_search=search_allowed)
            except (Exception, SystemExit) as exc:
                answer = "Error: " + user_error(exc)
            root.after(0, lambda: stream_finish(answer))
            busy = False
            tor_available = tor_proxy_port() is not None
            ready_text = (f"ready · {MODEL} · Tor connected" if tor_available
                          else f"ready · {MODEL} · Tor off")
            root.after(0, lambda: send.configure(state="normal"))
            root.after(0, lambda: stop.configure(state="disabled"))
            root.after(0, lambda: allow_search.configure(state="normal"))
            root.after(0, lambda: set_status(ready_text, "#9ef0b0"))

        threading.Thread(target=work, daemon=True).start()

    def save_chat():
        path = filedialog.asksaveasfilename(
            title="Save conversation", defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            _save(history, path)
            messagebox.showinfo("Onionmind", "Conversation saved.")
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))

    def launch_coding_agent():
        """Open the coding agent on a folder: this model, editing real files, over Tor."""
        folder = filedialog.askdirectory(title="Folder for the coding agent to work in")
        if not folder:
            return
        try:
            spawn_code(folder, MODEL)
            set_status("coding agent launching…", "#9ef0b0")
        except OSError as exc:
            messagebox.showerror("Coding agent unavailable", str(exc))

    send.configure(command=ask)
    stop.configure(command=stop_event.set)
    attach.configure(command=attach_image)
    remove_image.configure(command=clear_image)
    if DND_FILES:
        bottom.drop_target_register(DND_FILES)
        bottom.dnd_bind("<<Drop>>", lambda event: select_image(root.tk.splitlist(event.data)[0]))
    save.configure(command=save_chat)
    coding.configure(command=launch_coding_agent)
    model_use.configure(command=choose_model)
    install_model_button.configure(command=install_model)
    question.bind("<Return>", lambda _event: ask())
    root.after(100, lambda: threading.Thread(target=start, daemon=True).start())
    question.focus_set()
    root.mainloop()


def run_ui():
    """Run the native workbench, falling back only when its runtime is absent."""
    if sys.version_info < (3, 10):
        return run_legacy_ui()
    try:
        import onionmind_desktop
    except ModuleNotFoundError as exc:
        if exc.name != "onionmind_desktop" and not (exc.name or "").startswith("PySide6"):
            raise
        return run_legacy_ui()
    return onionmind_desktop.run(core_module=sys.modules[__name__])


if __name__ == "__main__":
    if "--tor-search" in sys.argv:
        query = " ".join(a for a in sys.argv[1:] if a != "--tor-search").strip()
        if not query:
            raise SystemExit("usage: onionmind.py --tor-search <query>")
        try:
            start_tor_hidden()
            tor_check()
            print(web_search(query), end="")
        finally:
            stop_managed_tor()
        raise SystemExit
    if "--mcp" in sys.argv:
        run_mcp()
        raise SystemExit
    # --yolo is read the same way for both agents: approvals off, boundary intact.
    yolo = "--yolo" in sys.argv
    if "--code" in sys.argv:
        rest = [a for a in sys.argv[1:] if a not in ("--code", "--yolo")]
        if "--model" in rest:
            i = rest.index("--model")
            MODEL = rest.pop(i + 1)
            rest.pop(i)
        run_code(os.path.abspath(rest[0] if rest else os.getcwd()), yolo=yolo)
        raise SystemExit
    if "--agent" in sys.argv:
        args = [a for a in sys.argv[1:] if a not in ("--agent", "--yolo")]
        model = None
        workdir = None
        if len(args) >= 2 and args[0] == "--model":
            model, args = args[1], args[2:]      # task is everything after it
        if len(args) >= 2 and args[0] == "--cwd":
            workdir, args = args[1], args[2:]
        try:
            raise SystemExit(run_agent(" ".join(args).strip() or None, model,
                                       yolo=yolo, workdir=workdir))
        finally:
            stop_managed_tor()                   # only ours, never a reused proxy
    if "--ui" in sys.argv:
        run_ui()
        raise SystemExit
    detect_backend()
    history = []
    allow_search_once = "--allow-search" in sys.argv
    cli_args = [a for a in sys.argv[1:] if a != "--allow-search"]
    try:
        if cli_args:
            if allow_search_once:
                start_tor_hidden()
            history.append({"role": "user", "content": " ".join(cli_args)})
            print("\n" + turn(history, allow_search=allow_search_once))
        else:
            # AI Act Art. 50(1): the interface itself must say it is an AI.
            print("You are talking to an AI. It can be wrong; you are responsible for what you do with the output.")
            print("Chat is local-only by default. /search <question> grants Tor search for one turn.")
            print("/save <file> exports the conversation. Ctrl-C quits.\n")
            while True:
                try:
                    q = input("you> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if q.startswith("/save"):
                    parts = q.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        print("usage: /save <file>   e.g. /save notes.txt")
                    else:
                        try:
                            _save(history, parts[1].strip())
                        except OSError as e:
                            print(f"[error] {e}")
                    continue
                search_this_turn = q.startswith("/search ")
                if search_this_turn:
                    q = q[len("/search "):].strip()
                    if not q:
                        print("usage: /search <question>")
                        continue
                    start_tor_hidden()
                if q:
                    history.append({"role": "user", "content": q})
                    print("\n" + turn(history, allow_search=search_this_turn) + "\n")
    finally:
        stop_managed_tor()
PYEOF
# point the tool at whichever model was installed
sed -i "s|^MODEL  = .*|MODEL  = \"$MODEL_NAME\"|" "$DIR/onionmind.py"
chmod 755 "$DIR/onionmind.py"

# build.py injects the two focused native-desktop modules into these payloads.
cat > "$DIR/onionmind_desktop_core.py" <<'DESKTOPCOREEOF'
"""Pure desktop support for Onionmind.

This module deliberately contains no GUI imports.  It owns the filesystem and
process-shaped details that the native desktop interface needs, while exposing
small value-oriented interfaces that are straightforward to test.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from uuid import uuid4
import zipfile


__all__ = [
    "ONIONMIND_TIERS",
    "ModelDisplay",
    "describe_model",
    "model_displays",
    "SettingsStore",
    "PREFERENCE_DEFAULTS",
    "load_preferences",
    "text_scale_factor",
    "CONTEXT_WINDOW_PRESETS",
    "context_window_tokens",
    "parse_context_window",
    "context_window_warning",
    "resolve_startup_mode",
    "animations_enabled",
    "UNCENSORED_MARKERS",
    "uncensored_marker",
    "quant_from_filename",
    "normalize_model_reference",
    "CatalogModel",
    "parse_hf_catalog",
    "format_downloads",
    "HF_CATALOG_URL",
    "fetch_hf_catalog",
    "machine_memory_mb",
    "gpu_vram_mb",
    "parameter_billions",
    "catalog_fit",
    "catalog_description",
    "shred_file",
    "shred_tree",
    "ChatSession",
    "SessionStore",
    "strip_thinking",
    "sanitize_messages",
    "WorkspaceChange",
    "WorkspaceSnapshot",
    "WorkspaceInspector",
    "HARNESS_LIMITATION",
    "TOR_CONTAINMENT_CEILING",
    "HarnessAvailability",
    "HarnessCommand",
    "HarnessSpec",
    "parse_terminal_command",
    "UPDATE_REVISION_FILENAME",
    "UPDATE_FEED_URL",
    "UpdateManifest",
    "BundleUpdateError",
    "parse_update_manifest",
    "installed_revision",
    "short_revision",
    "update_state",
    "BundleUpdater",
    "pending_staging_dir",
    "prune_update_workdir",
]


PathInput = str | os.PathLike[str]


# The desktop app runs under pythonw.exe, which has no console; without this
# flag Windows gives every helper process (git, node, the engine) its own
# flashing cmd window.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


ONIONMIND_TIERS: tuple[str, ...] = (
    "SPARK",
    "EMBER",
    "BLAZE",
    "INFERNO",
    "CINDER",
    "WILDFIRE",
    "FLASHPOINT",
    "PHOENIX",
    "NOVA",
    "PYRE",
)

_TIER_ALIASES: dict[str, str] = {
    # Shipped model names and their installer/size aliases.
    "spark": "SPARK",
    "lfm": "SPARK",
    "lfm2": "SPARK",
    "lfm2.5": "SPARK",
    "2.6b": "SPARK",
    "ember": "EMBER",
    "4b": "EMBER",
    "blaze": "BLAZE",
    "9b": "BLAZE",
    "inferno": "INFERNO",
    "27b": "INFERNO",
    # Reserved names remain first-class display tiers.
    "cinder": "CINDER",
    "wildfire": "WILDFIRE",
    "flashpoint": "FLASHPOINT",
    "phoenix": "PHOENIX",
    "nova": "NOVA",
    "pyre": "PYRE",
}


# What each tier actually is, and what it costs to run. The tier names are
# branding; the picker states the real model plus its weight class so the cost
# of a switch is visible before it is made.
_TIER_MODELS: dict[str, tuple[str, str]] = {
    "SPARK": ("LFM2.5 2.6B", "very light - ~2 GB, fine on CPU"),
    "EMBER": ("Qwen3.5 4B", "light - ~3 GB VRAM"),
    "BLAZE": ("Qwen3.5 9B", "moderate - ~7 GB VRAM"),
    "INFERNO": ("Qwen3.8 27B", "heavy - ~12-16 GB VRAM"),
}


@dataclass(frozen=True)
class ModelDisplay:
    """Presentation data for one installed model without losing its identifier."""

    raw_id: str
    tier: str | None
    display_name: str
    tag: str | None


def _model_name_and_tag(raw_id: str) -> tuple[str, str | None]:
    last_slash = max(raw_id.rfind("/"), raw_id.rfind("\\"))
    last_colon = raw_id.rfind(":")
    if last_colon > last_slash:
        return raw_id[last_slash + 1 : last_colon], raw_id[last_colon + 1 :]
    return raw_id[last_slash + 1 :], None


def _tier_for_model(name: str, tag: str | None) -> str | None:
    lowered = name.casefold()
    if lowered in _TIER_ALIASES:
        return _TIER_ALIASES[lowered]

    # Vision and namespaced variants such as ``onionmind-inferno-vision`` keep
    # their Onionmind family name.  Dots stay intact so the ``2.6b`` alias can
    # still be recognized.
    for token in re.split(r"[-_\s]+", lowered):
        if token in _TIER_ALIASES:
            return _TIER_ALIASES[token]

    if tag:
        return _TIER_ALIASES.get(tag.casefold())
    return None


def _display_name_for(tier: str | None, name: str, raw_id: str) -> str:
    """The real model name and its weight class; the raw id when we know neither."""

    entry = _TIER_MODELS.get(tier or "")
    if entry is None:
        return raw_id
    model, weight = entry
    tokens = re.split(r"[-_\s]+", name.casefold())
    # A model already named after what it is keeps that name; only the branded
    # tier names are translated back.
    if not any(token.upper() in _TIER_MODELS for token in tokens):
        return f"{raw_id} · {weight}"
    for variant in ("vision", "code"):
        if variant in tokens:
            model = f"{model} {variant}"
            break
    return f"{model} · {weight}"


def describe_model(raw_id: str) -> ModelDisplay:
    """Describe an Ollama model while preserving its exact usable identifier.

    The returned ``raw_id`` is the string callers must pass to Ollama.  Friendly
    Onionmind tier aliases are presentation-only and never replace that value.
    """

    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("model identifier must be a non-empty string")
    if "\x00" in raw_id:
        raise ValueError("model identifier cannot contain NUL")

    name, tag = _model_name_and_tag(raw_id)
    tier = _tier_for_model(name, tag)
    return ModelDisplay(
        raw_id=raw_id,
        tier=tier,
        display_name=_display_name_for(tier, name, raw_id),
        tag=tag,
    )


def model_displays(raw_ids: Iterable[str]) -> tuple[ModelDisplay, ...]:
    """Return stable, de-duplicated model choices in input order."""

    seen: set[str] = set()
    choices: list[ModelDisplay] = []
    for raw_id in raw_ids:
        if raw_id in seen:
            continue
        seen.add(raw_id)
        choices.append(describe_model(raw_id))
    return tuple(choices)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON object next to its destination, then atomically replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
        temporary = None
        # fsync the containing directory as well on POSIX so the rename itself
        # survives a sudden power loss. Windows does not expose directory
        # handles through os.open, so the file flush above is the strongest
        # portable guarantee available there.
        if os.name != "nt":
            directory_fd: int | None = None
            try:
                directory_fd = os.open(
                    path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                os.fsync(directory_fd)
            except OSError:
                # Some filesystems do not support directory fsync. The atomic
                # replacement has still completed successfully in that case.
                pass
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if handle is not None:
            handle.close()
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _quarantine_corrupt(path: Path) -> Path | None:
    """Move unreadable local state aside so callers can recover immediately."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid4().hex[:8]}")
    try:
        os.replace(path, destination)
    except OSError:
        return None
    return destination


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (UnicodeError, json.JSONDecodeError):
        _quarantine_corrupt(path)
        return None

    if not isinstance(value, dict):
        _quarantine_corrupt(path)
        return None
    return value


class SettingsStore:
    """Atomic local JSON settings with default and corruption recovery."""

    def __init__(
        self,
        path: PathInput,
        defaults: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        if defaults is not None and not isinstance(defaults, Mapping):
            raise TypeError("settings defaults must be a mapping")
        self._defaults = copy.deepcopy(dict(defaults or {}))

    def load(self) -> dict[str, Any]:
        loaded = _read_json_object(self.path)
        result = copy.deepcopy(self._defaults)
        if loaded is not None:
            result.update(loaded)
        return result

    def save(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, Mapping):
            raise TypeError("settings must be a mapping")
        result = copy.deepcopy(self._defaults)
        result.update(copy.deepcopy(dict(settings)))
        _atomic_write_json(self.path, result)
        return copy.deepcopy(result)


# --- Workbench preferences -------------------------------------------
#
# Presentation-only knobs persisted in settings.json. Everything defaults to
# the shipped workbench, and the Tor boundary plus the updater permission are
# deliberately not part of this surface: privacy behavior is never a theme.

PREFERENCE_DEFAULTS: dict[str, Any] = {
    "text_scale": "system",
    "enter_sends": True,
    "show_terminal_on_launch": False,
    "startup_mode": "remember",
    "reduce_motion": "system",
    "save_history": True,
    "remember_drafts": True,
    "clear_on_exit": False,
    "context_window": 16384,
}

_PREFERENCE_CHOICES: dict[str, tuple[str, ...]] = {
    "text_scale": ("system", "compact", "comfortable"),
    "startup_mode": ("remember", "chat", "agent"),
    "reduce_motion": ("system", "reduced", "full"),
}

# Offered as presets; any token count is accepted, these are just the stops
# worth having one click away.
CONTEXT_WINDOW_PRESETS: tuple[int, ...] = (4096, 8192, 16384, 32768, 65536, 131072)

TEXT_SCALE_FACTORS: dict[str, float] = {
    "system": 1.0,
    "compact": 0.9,
    "comfortable": 1.15,
}


def load_preferences(settings: Mapping[str, Any]) -> dict[str, Any]:
    """A validated preference view over raw settings; junk falls back silently."""
    preferences = dict(PREFERENCE_DEFAULTS)
    for key, default in PREFERENCE_DEFAULTS.items():
        value = settings.get(key)
        if value is None:
            continue
        if isinstance(default, bool):
            preferences[key] = bool(value)
        elif isinstance(default, int):
            # ponytail: context_window is the only free-number preference, so
            # its parser is named here rather than behind a per-key table.
            tokens = parse_context_window(value)
            if tokens is not None:
                preferences[key] = tokens
        elif isinstance(value, str) and value in _PREFERENCE_CHOICES[key]:
            preferences[key] = value
    return preferences


def parse_context_window(value: Any) -> Optional[int]:
    """Tokens from whatever the field holds: 24000, "24000", "24k", "16 K".

    Returns None for anything that is not a positive count, so a typo falls
    back to the stored value instead of silently becoming a number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = _as_text_core(value).strip().lower().replace(",", "").replace(" ", "")
    if text.endswith("tokens"):
        text = text[: -len("tokens")]
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1024, text[:-1]
    try:
        tokens = int(float(text) * multiplier)
    except ValueError:
        return None
    return tokens if tokens > 0 else None


def context_window_warning(tokens: int) -> str:
    """What is worth saying about a hand-typed context window, or "".

    The real ceiling is the model's own trained window and this machine's KV
    cache, neither of which is known here - so this warns about the ranges
    that go wrong rather than pretending to a number it cannot compute.
    """
    if tokens < 1024:
        return (
            "Below 1k tokens the system prompt alone may not fit; replies get "
            "cut short."
        )
    if tokens > 131072:
        return (
            "Above 128k tokens is beyond what nearly any local model was trained "
            "for; the backend will clamp it or fail to load the model."
        )
    if tokens > 32768:
        return (
            "Above 32k tokens the KV cache grows fast - if it outgrows VRAM the "
            "model spills onto the CPU and slows down."
        )
    return ""


def context_window_tokens(context_window: Any) -> int:
    """Tokens the backend is asked to hold. A bigger window remembers more of
    the conversation and costs proportionally more memory on this machine."""
    return parse_context_window(context_window) or PREFERENCE_DEFAULTS["context_window"]


def text_scale_factor(text_scale: str) -> float:
    return TEXT_SCALE_FACTORS.get(text_scale, 1.0)


def resolve_startup_mode(startup_mode: str, last_mode: str) -> str:
    """The composer mode to open in: an explicit choice, else the last used."""
    if startup_mode in {"chat", "agent"}:
        return startup_mode
    return last_mode if last_mode in {"chat", "agent"} else "agent"


def animations_enabled(reduce_motion: str, system_enabled: bool) -> bool:
    """The preference decides when it has an opinion; the system decides otherwise."""
    if reduce_motion == "reduced":
        return False
    if reduce_motion == "full":
        return True
    return bool(system_enabled)


# --- Model discovery ---------------------------------------------------
#
# Making an unknown model one paste away: references are normalized to what
# the pull API understands, and names are screened for the vocabulary of
# refusal-removed models so the workbench can say which is which.

UNCENSORED_MARKERS: tuple[str, ...] = (
    "abliterated",
    "abliterate",
    "uncensored",
    "unfiltered",
    "unaligned",
    "unhinged",
    "brainwash",
    "never-resist",
    "no-refusal",
)

_QUANT_PATTERN = re.compile(r"(?:IQ|Q)\d+[KMSL]?_(?:[A-Z0-9]+_)*[A-Z0-9]+|(?:IQ|Q)\d+[KMSL]?")


def uncensored_marker(*texts: Any) -> Optional[str]:
    """The first refusal-removal marker found in the given texts, if any.

    This is a name screen, not a guarantee: a marker in a model's name or tags
    is strong evidence its refusals were removed, and an absent marker says
    nothing. The UI must present it exactly that way.
    """
    for marker in UNCENSORED_MARKERS:
        for text in texts:
            if marker in _as_text_core(text).casefold():
                return marker
    return None


def _as_text_core(value: Any) -> str:
    return "" if value is None else str(value)


def quant_from_filename(filename: str) -> Optional[str]:
    """The quantization token in a GGUF filename, e.g. Q4_K_M from
    Model-Q4_K_M.gguf."""
    match = _QUANT_PATTERN.search(filename or "")
    return match.group(0) if match else None


def normalize_model_reference(text: str) -> str:
    """Accept an Ollama name, an hf.co path, a Hugging Face URL, or a bare
    user/repo path, and return what the pull API understands."""
    value = _as_text_core(text).strip()
    if not value:
        return value
    lowered = value.casefold()
    if lowered.startswith(("http://", "https://")):
        parsed = urlparse(value)
        host = parsed.netloc.casefold()
        if not (host == "huggingface.co" or host.endswith(".huggingface.co")):
            return value  # unknown hosts pass through; the service answers
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return value
        user, repo = parts[0], parts[1].removesuffix(".gguf")
        reference = f"hf.co/{user}/{repo}"
        filename = parts[-1] if len(parts) > 2 else ""
        quant = quant_from_filename(filename)
        return f"{reference}:{quant}" if quant else reference
    if lowered.startswith("hf.co/"):
        return value
    if value.count("/") == 1 and " " not in value:
        user, repo = value.split("/", 1)
        repo = repo.removesuffix(".gguf")
        if user and repo and ":" not in repo:
            return f"hf.co/{user}/{repo}"
    return value


@dataclass(frozen=True)
class CatalogModel:
    """One popular model from the public Hugging Face catalog."""

    id: str
    downloads: int = 0
    likes: int = 0
    uncensored: Optional[str] = None
    gguf: bool = False
    task: str = ""


def parse_hf_catalog(payload: Any, limit: int = 24) -> list[CatalogModel]:
    """Parse the public huggingface.co models API response into catalog rows.

    Junk rows are skipped silently; ordering is the API's (most downloaded
    first) rather than ours.
    """
    if not isinstance(payload, list):
        return []
    entries: list[CatalogModel] = []
    rows = payload if limit is None else payload[: max(0, limit)]
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        model_id = _as_text_core(item.get("id")).strip()
        if not model_id:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        entries.append(
            CatalogModel(
                id=model_id,
                downloads=int(item.get("downloads") or 0),
                likes=int(item.get("likes") or 0),
                uncensored=uncensored_marker(model_id, *tags),
                gguf=any("gguf" in _as_text_core(tag).casefold() for tag in tags)
                or model_id.casefold().endswith("gguf"),
                task=_as_text_core(item.get("pipeline_tag")).strip(),
            )
        )
    return entries


def format_downloads(count: int) -> str:
    """A compact human form for catalog counts: 1.2M, 340k, 900."""
    value = int(count or 0)
    for divisor, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= divisor:
            trimmed = value / divisor
            return f"{trimmed:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


HF_CATALOG_URL = "https://huggingface.co/api/models"


def fetch_hf_catalog(port: int, limit: int = 30, session: Any = None) -> list[CatalogModel]:
    """The popular GGUF list through the verified Tor proxy, or not at all.

    socks5h resolves the hostname inside Tor, and there is no direct
    fallback: an unreachable circuit is an honest failure, not a reason to
    leak the machine's address to huggingface.co.
    """
    import requests  # deferred: the pure module stays importable without it

    requester = session if session is not None else requests
    proxies = {
        "http": f"socks5h://127.0.0.1:{port}",
        "https": f"socks5h://127.0.0.1:{port}",
    }
    response = requester.get(
        HF_CATALOG_URL,
        params={
            "filter": "gguf",
            "sort": "downloads",
            "direction": "-1",
            "limit": str(limit),
        },
        proxies=proxies,
        timeout=60,
    )
    status = getattr(response, "status_code", 0)
    if status != 200:
        raise RuntimeError(f"huggingface.co returned HTTP {status} over Tor.")
    return parse_hf_catalog(response.json())


def machine_memory_mb() -> Optional[int]:
    """Total system RAM in MB, or None when the platform won't say."""
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys // (1024 * 1024))
            return None
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(round(int(line.split()[1]) / 1024))
    except Exception:
        return None
    return None


def gpu_vram_mb() -> Optional[int]:
    """The first NVIDIA GPU's VRAM in MB, or None without one."""
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0 and output.stdout.strip():
            return int(output.stdout.strip().splitlines()[0].strip())
    except Exception:
        return None
    return None


_PARAM_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z0-9])")


def parameter_billions(text: str) -> Optional[float]:
    """The parameter count in a model name: 7 from Qwen2.5-7B-Instruct-GGUF."""
    match = _PARAM_PATTERN.search(text or "")
    return float(match.group(1)) if match else None


def catalog_description(entry: Any, fit_label: str = "") -> str:
    """One plain line describing a catalog row: what it does, how big it is,
    how popular it is. Hugging Face's list API carries no prose description,
    so this is composed from the fields it does return."""
    task = _as_text_core(getattr(entry, "task", "")).replace("-", " ").strip()
    params = parameter_billions(_as_text_core(getattr(entry, "id", "")))
    parts = [task.capitalize() if task else "Local model"]
    if params is not None:
        parts.append(f"{params:g}B parameters")
    if fit_label:
        parts.append(fit_label)
    parts.append(f"{format_downloads(getattr(entry, 'downloads', 0) or 0)} downloads")
    likes = int(getattr(entry, "likes", 0) or 0)
    if likes:
        parts.append(f"{format_downloads(likes)} likes")
    return " · ".join(parts)


def catalog_fit(id_or_name: str, vram_mb: Optional[int], ram_mb: Optional[int]) -> tuple[int, str]:
    """(rank, label) for a catalog row against this machine's memory.

    rank 0 fits this machine, 1 is an unknown size, 2 is beyond it. A Q4 GGUF
    runs roughly 0.6 GB per billion parameters; the estimate carries the usual
    quantization slack (1.15x headroom) rather than pretending precision.
    """
    params = parameter_billions(id_or_name)
    budget = vram_mb if vram_mb else int((ram_mb or 0) * 0.85)
    if params is None or budget <= 0:
        return 1, "size unknown"
    estimated_gb = params * 0.6
    kind = "VRAM" if vram_mb else "RAM"
    shown = f"{estimated_gb:.1f}" if estimated_gb < 10 else f"{estimated_gb:.0f}"
    fits = estimated_gb * 1.15 <= budget / 1024
    if fits:
        return 0, f"~{shown}GB at Q4, fits this machine's {kind}"
    return 2, f"~{shown}GB at Q4, beyond this machine's {kind}"


def shred_file(path: PathInput) -> bool:
    """Overwrite a file's bytes, then remove it. True when it is gone.

    A symlink is unlinked, never written through - the file it points at is
    not this program's to destroy.

    ponytail: overwrite-then-unlink is as far as user space reaches. On an SSD,
    a copy-on-write filesystem, or any volume with snapshots, the original
    blocks can survive untouched; full-disk encryption, or Matchstick's
    RAM-only image, is the real guarantee. TECHNICAL.md states the same limit.
    """
    target = Path(path)
    if target.is_symlink():
        try:
            target.unlink()
            return True
        except OSError:
            return False
    try:
        size = target.stat().st_size
        with open(target, "r+b", buffering=0) as handle:
            written = 0
            while written < size:
                chunk = min(1 << 20, size - written)
                handle.write(os.urandom(chunk))
                written += chunk
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass                      # unwritable is not a reason to keep the file
    for attempt in (0, 1):
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt:
                return False
            try:
                os.chmod(target, stat.S_IWRITE)    # Windows read-only flag
            except OSError:
                return False
        except OSError:
            return False
    return False


def shred_tree(root: PathInput) -> int:
    """Shred every file under root and remove the directories. Returns the
    number of files removed; a directory that will not go is left behind
    rather than failing the whole wipe."""
    base = Path(root)
    if not base.exists() and not base.is_symlink():
        return 0
    if base.is_symlink() or base.is_file():
        return 1 if shred_file(base) else 0
    removed = 0
    for parent, directories, files in os.walk(base, topdown=False):
        for name in files:
            if shred_file(Path(parent) / name):
                removed += 1
        for name in directories:
            entry = Path(parent) / name
            try:
                entry.unlink() if entry.is_symlink() else entry.rmdir()
            except OSError:
                pass
    try:
        base.rmdir()
    except OSError:
        pass
    return removed


@dataclass
class ChatSession:
    """A persisted local conversation; messages remain ordinary JSON dicts."""

    id: str
    title: str
    model: str
    workspace: str | None
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    archived_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "workspace": self.workspace,
            "messages": sanitize_messages(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_THINK_TAG_PATTERN = r"<\s*(/?)\s*think(?:\s[^>]*)?>"
_THINK_TAG = re.compile(_THINK_TAG_PATTERN, re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


def _think_tag_candidate(candidate: str) -> tuple[str, bool]:
    """Classify text beginning with ``<`` against prefixes of _THINK_TAG."""
    if not candidate.startswith("<"):
        return "invalid", False
    index, size = 1, len(candidate)
    while index < size and candidate[index].isspace():
        index += 1
    if index == size:
        return "prefix", False

    closing = candidate[index] == "/"
    if closing:
        index += 1
        while index < size and candidate[index].isspace():
            index += 1
        if index == size:
            return "prefix", True

    for expected in "think":
        if index == size:
            return "prefix", closing
        if re.fullmatch(expected, candidate[index], re.IGNORECASE) is None:
            return "invalid", closing
        index += 1
    if index == size:
        return "prefix", closing
    if candidate[index] == ">":
        match = _THINK_TAG.fullmatch(candidate[:index + 1])
        return ("complete", bool(match.group(1))) if match else ("invalid", closing)
    if not candidate[index].isspace():
        return "invalid", closing
    index += 1
    while index < size:
        if candidate[index] == ">":
            match = _THINK_TAG.fullmatch(candidate[:index + 1])
            return ("complete", bool(match.group(1))) if match else ("invalid", closing)
        index += 1
    return "prefix", closing


def _partial_think_tag(text: str) -> tuple[int, bool] | None:
    start = text.find("<")
    while start >= 0:
        state, closing = _think_tag_candidate(text[start:])
        if state == "prefix":
            return start, closing
        start = text.find("<", start + 1)
    return None


def strip_thinking(text: str) -> str:
    """Remove all model reasoning blocks from completed assistant text.

    A response can contain reasoning before a tool call and another block after
    the tool result.  Treat an unmatched closing tag as the end of an implicit
    leading reasoning block, and an unmatched opening tag as reasoning through
    end-of-response.  This deliberately fails closed for legacy transcripts.
    """

    if not isinstance(text, str):
        raise TypeError("assistant content must be a string")

    visible: list[str] = []
    cursor = 0
    depth = 0
    for tag in _THINK_TAG.finditer(text):
        closing = bool(tag.group(1))
        if closing:
            if depth:
                depth -= 1
                if depth == 0:
                    cursor = tag.end()
            else:
                # Some local reasoning models omit the opening tag. Preserve
                # the established fail-closed behavior and discard that prefix.
                visible.clear()
                cursor = tag.end()
            continue

        if depth == 0:
            visible.append(text[cursor:tag.start()])
        depth += 1

    if depth == 0:
        tail = text[cursor:]
        partial = _partial_think_tag(tail)
        if partial is None:
            visible.append(tail)
        elif partial[1]:
            visible.clear()
        else:
            # Keep completed visible text before an unfinished opening tag.
            visible.append(tail[:partial[0]])
    return "".join(visible).strip()


def sanitize_messages(
    messages: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deep-copy messages and remove reasoning from every assistant entry.

    Non-content fields such as ``tool_calls`` are retained verbatim so a live
    turn can finish its tool protocol before the sanitized history is stored.
    """

    copied: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"message {index} must be a mapping")
        item = copy.deepcopy(dict(message))
        if item.get("role") == "assistant":
            for key in list(item):
                if isinstance(key, str) and key.casefold() in _REASONING_FIELDS:
                    item.pop(key, None)
            content = item.get("content")
            item["content"] = _sanitize_assistant_content(content)
        copied.append(item)
    return copied


def _sanitize_assistant_content(value: Any) -> Any:
    if isinstance(value, str):
        return strip_thinking(value)
    if isinstance(value, Mapping):
        return {key: _sanitize_assistant_content(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_assistant_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_assistant_content(item) for item in value)
    return copy.deepcopy(value)


def _message_dicts(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sanitize_messages(messages)


class SessionStore:
    """Own creation, persistence, discovery, and archiving of local sessions."""

    def __init__(self, root: PathInput) -> None:
        self.root = Path(root).expanduser()
        self.archive_root = self.root / "archive"

    @staticmethod
    def _validate_id(session_id: str) -> str:
        if not isinstance(session_id, str) or not _SAFE_SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session id")
        return session_id

    def _path(self, session_id: str, *, archived: bool = False) -> Path:
        safe_id = self._validate_id(session_id)
        parent = self.archive_root if archived else self.root
        return parent / f"{safe_id}.json"

    @staticmethod
    def _session_from_dict(
        value: Mapping[str, Any], *, expected_id: str
    ) -> ChatSession:
        session_id = value.get("id")
        if session_id != expected_id:
            raise ValueError("session id does not match its filename")

        title = value.get("title")
        model = value.get("model", "")
        workspace = value.get("workspace")
        messages = value.get("messages", [])
        created_at = value.get("created_at")
        updated_at = value.get("updated_at")
        archived_at = value.get("archived_at")

        if not isinstance(title, str) or not title:
            raise ValueError("session title is invalid")
        if not isinstance(model, str):
            raise ValueError("session model is invalid")
        if workspace is not None and not isinstance(workspace, str):
            raise ValueError("session workspace is invalid")
        if not isinstance(messages, list):
            raise ValueError("session messages are invalid")
        if not isinstance(created_at, str) or not isinstance(updated_at, str):
            raise ValueError("session timestamps are invalid")
        if archived_at is not None and not isinstance(archived_at, str):
            raise ValueError("session archive timestamp is invalid")

        return ChatSession(
            id=session_id,
            title=title,
            model=model,
            workspace=workspace,
            messages=_message_dicts(messages),
            created_at=created_at,
            updated_at=updated_at,
            archived_at=archived_at,
        )

    def create(
        self,
        *,
        title: str = "New session",
        model: str = "",
        workspace: PathInput | None = None,
        messages: Iterable[Mapping[str, Any]] = (),
    ) -> ChatSession:
        if not isinstance(title, str):
            raise TypeError("session title must be a string")
        if not isinstance(model, str):
            raise TypeError("session model must be a string")
        normalized_title = title.strip() or "New session"
        normalized_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace is not None else None
        )
        now = _utc_now()
        session = ChatSession(
            id=uuid4().hex,
            title=normalized_title,
            model=model,
            workspace=normalized_workspace,
            messages=_message_dicts(messages),
            created_at=now,
            updated_at=now,
        )
        _atomic_write_json(self._path(session.id), session.to_dict())
        return session

    def load(self, session_id: str, *, archived: bool = False) -> ChatSession | None:
        path = self._path(session_id, archived=archived)
        value = _read_json_object(path)
        if value is None:
            return None
        try:
            return self._session_from_dict(value, expected_id=session_id)
        except (TypeError, ValueError):
            _quarantine_corrupt(path)
            return None

    def save(self, session: ChatSession) -> ChatSession:
        if not isinstance(session, ChatSession):
            raise TypeError("session must be a ChatSession")
        self._validate_id(session.id)
        if not isinstance(session.title, str) or not session.title.strip():
            raise ValueError("session title must be non-empty")
        if not isinstance(session.model, str):
            raise TypeError("session model must be a string")
        if session.workspace is not None and not isinstance(session.workspace, str):
            raise TypeError("session workspace must be a string or None")
        if not isinstance(session.created_at, str) or not session.created_at:
            raise ValueError("session creation timestamp must be non-empty")
        if not isinstance(session.updated_at, str) or not session.updated_at:
            raise ValueError("session update timestamp must be non-empty")
        if session.archived_at is not None and not isinstance(session.archived_at, str):
            raise TypeError("session archive timestamp must be a string or None")

        normalized = replace(
            session,
            title=session.title.strip(),
            messages=_message_dicts(session.messages),
            updated_at=_utc_now(),
        )
        target = self._path(normalized.id, archived=normalized.archived_at is not None)
        payload = normalized.to_dict()
        stored = _read_json_object(target)
        if stored is not None and stored.get("messages") == payload["messages"]:
            # Sessions list newest-conversation-first, so a write that adds no
            # history - a rename, a model swap, re-saving one just opened -
            # must not promote it past sessions with newer conversation.
            kept = stored.get("updated_at")
            if isinstance(kept, str) and kept:
                normalized = replace(normalized, updated_at=kept)
                payload["updated_at"] = kept
        _atomic_write_json(target, payload)

        # Keep the mutable value object useful to callers that retain it.
        session.title = normalized.title
        session.messages = copy.deepcopy(normalized.messages)
        session.updated_at = normalized.updated_at
        return session

    def list(self, *, archived: bool = False) -> list[ChatSession]:
        parent = self.archive_root if archived else self.root
        if not parent.is_dir():
            return []

        sessions: list[ChatSession] = []
        for path in parent.glob("*.json"):
            if not path.is_file():
                continue
            try:
                session = self.load(path.stem, archived=archived)
            except ValueError:
                _quarantine_corrupt(path)
                continue
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return sessions

    def archive(self, session_id: str) -> ChatSession | None:
        session = self.load(session_id)
        if session is None:
            return self.load(session_id, archived=True)

        now = _utc_now()
        session.updated_at = now
        session.archived_at = now
        destination = self._path(session.id, archived=True)
        _atomic_write_json(destination, session.to_dict())
        try:
            self._path(session.id).unlink()
        except FileNotFoundError:
            pass
        return session

    def delete(self, session_id: str) -> bool:
        """Permanently remove every stored copy of a session from this machine."""

        deleted = False
        for archived in (False, True):
            path = self._path(session_id, archived=archived)
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            deleted = True
        return deleted


@dataclass(frozen=True)
class WorkspaceChange:
    status: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    is_git: bool
    branch: str | None
    dirty: bool
    changes: tuple[WorkspaceChange, ...]
    agents_files: tuple[str, ...]
    file_tree: tuple[str, ...]
    tree_truncated: bool

    @property
    def change_summary(self) -> str:
        if not self.is_git:
            return "Not a Git repository"
        if not self.changes:
            return "Clean"

        staged = sum(
            change.status[0] not in {" ", "?", "!"} for change in self.changes
        )
        unstaged = sum(
            len(change.status) > 1 and change.status[1] not in {" ", "?", "!"}
            for change in self.changes
        )
        untracked = sum(change.status == "??" for change in self.changes)
        details: list[str] = []
        if staged:
            details.append(f"{staged} staged")
        if unstaged:
            details.append(f"{unstaged} unstaged")
        if untracked:
            details.append(f"{untracked} untracked")
        noun = "change" if len(self.changes) == 1 else "changes"
        suffix = f" · {', '.join(details)}" if details else ""
        return f"{len(self.changes)} {noun}{suffix}"


class WorkspaceInspector:
    """Inspect a selected directory without allowing a shell to reinterpret it."""

    _SKIP_DIRECTORIES = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "target",
            "venv",
        }
    )

    def __init__(
        self,
        max_entries: int = 200,
        max_depth: int = 4,
        max_diff_chars: int = 200_000,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if max_depth < 0:
            raise ValueError("max_depth cannot be negative")
        if max_diff_chars < 1:
            raise ValueError("max_diff_chars must be positive")
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.max_diff_chars = max_diff_chars

    @staticmethod
    def _root(selected: PathInput) -> Path:
        try:
            root = Path(selected).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(f"workspace does not exist: {selected}") from exc
        if not root.is_dir():
            raise ValueError(f"workspace is not a directory: {selected}")
        return root

    @staticmethod
    def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        environment["LC_ALL"] = "C"
        try:
            return subprocess.run(
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.untrackedCache=false",
                    *arguments,
                ],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=15,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"could not run Git: {exc}") from exc

    @staticmethod
    def _parse_status(output: bytes) -> tuple[WorkspaceChange, ...]:
        records = output.split(b"\0")
        changes: list[WorkspaceChange] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 3:
                continue
            status = record[:2].decode("ascii", errors="replace")
            path = os.fsdecode(record[3:])
            if os.name == "nt":
                path = path.replace("\\", "/")
            original_path = None
            if "R" in status or "C" in status:
                if index < len(records) and records[index]:
                    original_path = os.fsdecode(records[index])
                    if os.name == "nt":
                        original_path = original_path.replace("\\", "/")
                    index += 1
            changes.append(
                WorkspaceChange(
                    status=status,
                    path=path,
                    original_path=original_path,
                )
            )
        return tuple(changes)

    def _tree(self, root: Path) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        entries: list[str] = []
        agents: list[str] = []
        truncated = False
        stack: list[tuple[Path, int]] = [(root, 0)]

        while stack:
            directory, depth = stack.pop()
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                continue

            directories: list[tuple[Path, int]] = []
            for child in children:
                if child.is_dir() and child.name in self._SKIP_DIRECTORIES:
                    continue
                relative = child.relative_to(root).as_posix()
                is_directory = child.is_dir()
                if len(entries) >= self.max_entries:
                    truncated = True
                    break
                entries.append(relative + ("/" if is_directory else ""))
                if child.name.casefold() == "agents.md" and child.is_file():
                    agents.append(relative)
                is_junction = bool(
                    getattr(child, "is_junction", lambda: False)()
                )
                if is_directory and not child.is_symlink() and not is_junction:
                    try:
                        child.resolve(strict=True).relative_to(root)
                    except (OSError, ValueError):
                        continue
                    if depth < self.max_depth:
                        directories.append((child, depth + 1))
                    else:
                        truncated = True
            if len(entries) >= self.max_entries:
                truncated = True
                break
            stack.extend(reversed(directories))

        return tuple(entries), tuple(agents), truncated

    def inspect(self, selected: PathInput) -> WorkspaceSnapshot:
        root = self._root(selected)
        file_tree, agents_files, tree_truncated = self._tree(root)

        try:
            inside = self._git(root, "rev-parse", "--is-inside-work-tree")
        except RuntimeError:
            inside = None
        is_git = bool(
            inside is not None
            and inside.returncode == 0
            and inside.stdout.strip() == b"true"
        )
        if not is_git:
            return WorkspaceSnapshot(
                root=root,
                is_git=False,
                branch=None,
                dirty=False,
                changes=(),
                agents_files=agents_files,
                file_tree=file_tree,
                tree_truncated=tree_truncated,
            )

        branch_result = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch_result.returncode == 0:
            branch = os.fsdecode(branch_result.stdout).strip() or None
        else:
            detached = self._git(root, "rev-parse", "--short", "HEAD")
            branch = (
                f"detached@{os.fsdecode(detached.stdout).strip()}"
                if detached.returncode == 0 and detached.stdout.strip()
                else None
            )

        status_result = self._git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        )
        if status_result.returncode != 0:
            detail = os.fsdecode(status_result.stderr).strip() or "Git status failed"
            raise RuntimeError(detail)
        changes = self._parse_status(status_result.stdout)
        return WorkspaceSnapshot(
            root=root,
            is_git=True,
            branch=branch,
            dirty=bool(changes),
            changes=changes,
            agents_files=agents_files,
            file_tree=file_tree,
            tree_truncated=tree_truncated,
        )

    def diff(self, selected: PathInput, relative_path: PathInput | None = None) -> str:
        root = self._root(selected)
        path_arguments: list[str] = []
        relative_filter: str | None = None
        if relative_path is not None:
            candidate = Path(relative_path)
            candidate = candidate if candidate.is_absolute() else root / candidate
            candidate = candidate.resolve(strict=False)
            try:
                relative = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("diff path must stay inside the workspace") from exc
            relative_filter = relative.as_posix()
            path_arguments = ["--", relative_filter]
        else:
            path_arguments = ["--"]

        result = self._git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--no-color",
            "HEAD",
            *path_arguments,
        )
        if result.returncode != 0:
            # An unborn repository has no HEAD.  Its index and worktree can
            # still be inspected without executing user-controlled shell text.
            cached = self._git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
                "--no-color",
                "--cached",
                *path_arguments,
            )
            working = self._git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
                "--no-color",
                *path_arguments,
            )
            if cached.returncode != 0 or working.returncode != 0:
                message = os.fsdecode(result.stderr).strip() or "Git diff failed"
                raise RuntimeError(message)
            output = cached.stdout + working.stdout
        else:
            output = result.stdout

        text = output.decode("utf-8", errors="replace")
        untracked = self._untracked_previews(root, relative_filter)
        if untracked:
            if text and not text.endswith("\n"):
                text += "\n"
            text += untracked
        if len(text) > self.max_diff_chars:
            return text[: self.max_diff_chars] + "\n… diff truncated …\n"
        return text

    def _untracked_previews(self, root: Path, relative_filter: str | None) -> str:
        """Return bounded, filter-free previews for untracked regular files."""

        arguments = ["ls-files", "--others", "--exclude-standard", "-z", "--"]
        if relative_filter is not None:
            arguments.append(relative_filter)
        result = self._git(root, *arguments)
        if result.returncode != 0:
            return ""

        chunks: list[str] = []
        records = [record for record in result.stdout.split(b"\0") if record]
        for raw_path in records[:20]:
            relative_text = os.fsdecode(raw_path)
            if os.name == "nt":
                relative_text = relative_text.replace("\\", "/")
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                continue

            candidate = root
            unsafe_link = False
            for component in relative.parts:
                candidate = candidate / component
                try:
                    is_junction = bool(
                        getattr(candidate, "is_junction", lambda: False)()
                    )
                    if candidate.is_symlink() or is_junction:
                        unsafe_link = True
                        break
                except OSError:
                    unsafe_link = True
                    break
            if unsafe_link:
                continue

            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file():
                    continue
                with resolved.open("rb") as handle:
                    payload = handle.read(32_769)
            except (OSError, ValueError):
                continue

            header = (
                f"\nUntracked file preview: {relative_text}\n"
                f"{'-' * min(72, max(24, len(relative_text) + 24))}\n"
            )
            if b"\0" in payload:
                body = "[binary content omitted]\n"
            else:
                truncated = len(payload) > 32_768
                body = payload[:32_768].decode("utf-8", errors="replace")
                if body and not body.endswith("\n"):
                    body += "\n"
                if truncated:
                    body += "… file preview truncated …\n"
            chunks.append(header + body)

        if len(records) > 20:
            chunks.append(f"\n… {len(records) - 20} more untracked files omitted …\n")
        return "".join(chunks)


# The honest ceiling on the Tor boundary, said in the user's face rather than
# only in TECHNICAL.md. Every layer that routes the agent through Tor - the proxy
# variables, the loopback bridge, the python and node socket shims - is
# environment handed to a process running as the user, so anything that does not
# read that environment is not covered. Only the kernel can cover it.
TOR_CONTAINMENT_CEILING = (
    "Tor is enforced by the environment the agent runs in, not by the operating "
    "system. Proxy variables and the injected Python and Node socket shims cover "
    "every runtime the agent normally reaches for, but a compiled binary, "
    "python -S, or a tool that ignores proxies outright (ping, nslookup, "
    "traceroute) can still reach the network directly. Closing that needs an OS "
    "egress rule - a firewall rule for this user, a container, a network "
    "namespace - or the Matchstick live USB, whose nftables ruleset already does it."
)


HARNESS_LIMITATION = (
    "Onionmind Agent is an early-access local coding workflow. It starts in the "
    "selected working directory, while its own tools govern what it can access. "
    "Approvals are on by default: it asks before a protected action, and where "
    "there is nobody to ask it stops instead of continuing. Ticking YOLO lets it "
    "edit files and run commands unattended - that widens what it may do to this "
    "machine, and moves the network boundary not at all. "
    "The agent reaches the web only through Tor: it verifies "
    "a circuit before it starts and refuses to run without one.\n\n"
    + TOR_CONTAINMENT_CEILING
)


@dataclass(frozen=True)
class HarnessAvailability:
    available: bool
    executable: str | None
    reason: str
    limitation: str = HARNESS_LIMITATION


@dataclass(frozen=True)
class HarnessCommand:
    argv: tuple[str, ...]
    cwd: Path


class HarnessSpec:
    """Build and preflight the public Ollama DeepSeek Harness launcher."""

    def __init__(self, executable: str = "ollama") -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("harness executable must be non-empty")
        self.executable = executable

    @property
    def limitation(self) -> str:
        return HARNESS_LIMITATION

    def build(self, *, model: str, task: str, cwd: PathInput) -> HarnessCommand:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("harness model must be non-empty")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("harness task must be non-empty")
        if "\x00" in model or "\x00" in task:
            raise ValueError("harness arguments cannot contain NUL")
        working_directory = WorkspaceInspector._root(cwd)
        return HarnessCommand(
            argv=(
                self.executable,
                "launch",
                "dsh",
                "--model",
                model,
                "--",
                "--profile",
                "headless",
                task,
            ),
            cwd=working_directory,
        )

    def check(self) -> HarnessAvailability:
        executable = shutil.which(self.executable)
        if executable is None:
            return HarnessAvailability(
                available=False,
                executable=None,
                reason=(
                    "Onionmind's local engine is not ready. Re-run Onionmind setup "
                    "or start its local model service, then try Agent mode again."
                ),
            )
        try:
            result = subprocess.run(
                [executable, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=5,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=f"Onionmind's local engine could not be started: {exc}",
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).decode(
                "utf-8", errors="replace"
            ).strip()
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=detail or "Onionmind's local engine did not pass its readiness check.",
            )

        node = shutil.which("node")
        if node is None:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=(
                    "Onionmind Agent prerequisites are incomplete. Re-run Onionmind "
                    "setup, then try Agent mode again."
                ),
            )
        try:
            node_result = subprocess.run(
                [node, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=5,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=f"Onionmind Agent prerequisites could not be checked: {exc}",
            )
        node_text = (node_result.stdout or node_result.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
        version_match = re.search(r"v?(\d+)\.(\d+)(?:\.\d+)?", node_text)
        supported = bool(
            node_result.returncode == 0
            and version_match is not None
            and (
                int(version_match.group(1)) >= 24
                or (
                    int(version_match.group(1)) == 22
                    and int(version_match.group(2)) >= 19
                )
            )
        )
        if not supported:
            shown = node_text or "unknown version"
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=(
                    f"Onionmind Agent needs a newer local runtime; found {shown}. "
                    "Re-run Onionmind setup, then try Agent mode again."
                ),
            )
        return HarnessAvailability(
            available=True,
            executable=executable,
            reason="Onionmind Agent is ready and will start on demand.",
        )


def _split_windows_commandline(command: str) -> tuple[str, ...]:
    """Split with the quoting rules used for native Windows process argv."""

    arguments: list[str] = []
    length = len(command)
    index = 0
    while index < length:
        while index < length and command[index] in " \t":
            index += 1
        if index >= length:
            break

        argument: list[str] = []
        quoted = False
        while index < length:
            if command[index] in " \t" and not quoted:
                break
            if command[index] == "\\":
                slash_start = index
                while index < length and command[index] == "\\":
                    index += 1
                slash_count = index - slash_start
                if index < length and command[index] == '"':
                    argument.extend("\\" * (slash_count // 2))
                    if slash_count % 2:
                        argument.append('"')
                        index += 1
                    else:
                        if quoted and index + 1 < length and command[index + 1] == '"':
                            argument.append('"')
                            index += 2
                        else:
                            quoted = not quoted
                            index += 1
                else:
                    argument.extend("\\" * slash_count)
                continue
            if command[index] == '"':
                if quoted and index + 1 < length and command[index + 1] == '"':
                    argument.append('"')
                    index += 2
                else:
                    quoted = not quoted
                    index += 1
                continue
            argument.append(command[index])
            index += 1
        arguments.append("".join(argument))
        while index < length and command[index] in " \t":
            index += 1
    return tuple(arguments)


def parse_terminal_command(
    command: str,
    *,
    interpreter: str = "direct",
) -> tuple[str, ...]:
    """Return argv suitable for ``subprocess`` with ``shell=False``.

    ``direct`` launches a native executable and parses only argv quoting.
    ``powershell``, ``cmd``, and ``sh`` explicitly wrap commands that require
    those interpreters, still passing an explicit argument list to the process
    API instead of enabling its implicit shell mode.
    """

    if not isinstance(command, str) or not command.strip():
        raise ValueError("terminal command must be non-empty")
    if "\x00" in command:
        raise ValueError("terminal command cannot contain NUL")

    if not isinstance(interpreter, str):
        raise TypeError("interpreter must be a string")
    mode = interpreter.casefold()
    if mode == "powershell":
        executable = "powershell.exe" if os.name == "nt" else "pwsh"
        return (
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        )
    if mode == "cmd":
        return ("cmd.exe", "/d", "/s", "/c", command)
    if mode == "sh":
        return ("/bin/sh", "-c", command)
    if mode != "direct":
        raise ValueError(
            "interpreter must be 'direct', 'powershell', 'cmd', or 'sh'"
        )

    arguments = (
        _split_windows_commandline(command)
        if os.name == "nt"
        else tuple(shlex.split(command, posix=True))
    )
    if not arguments or not arguments[0]:
        raise ValueError("terminal command must name an executable")
    return arguments


# --- Tor-routed self-update -------------------------------------------------
#
# The installed workbench is a Nuitka standalone bundle: its code is compiled
# into Onionmind.exe, so an update means a whole new bundle directory. The
# feed is a plain GitHub release-asset URL (no api.github.com call, which is
# aggressively rate-limited for shared Tor exit addresses) whose small JSON
# manifest carries the source revision plus the size and SHA-256 of the zip.
# Every request - manifest and bundle alike - goes through the local Tor SOCKS
# port with fresh credentials, so each fetch rides its own circuit. A failed
# Tor check fails closed; there is no direct-network fallback anywhere in this path.

UPDATE_REPO = "Codemaster64/onionmind"
UPDATE_FEED_TAG = "desktop-latest"
UPDATE_MANIFEST_ASSET = "onionmind-update.json"
UPDATE_REVISION_FILENAME = ".onionmind-source-revision"
UPDATE_FEED_URL = (
    f"https://github.com/{UPDATE_REPO}/releases/download/"
    f"{UPDATE_FEED_TAG}/{UPDATE_MANIFEST_ASSET}"
)
_UPDATE_ASSET_HOSTS = ("github.com", "githubusercontent.com", "github.io")


class BundleUpdateError(RuntimeError):
    """A user-facing update failure. Never retried outside Tor."""


@dataclass(frozen=True)
class UpdateManifest:
    revision: str
    version: str
    asset_name: str
    asset_url: str
    size: int
    sha256: str


def short_revision(revision: Optional[str]) -> str:
    """Seven hex characters for display; honest fallbacks for odd values."""

    if not revision:
        return "unknown"
    text = revision.strip()
    return text[:7] if re.fullmatch(r"[0-9a-fA-F]{7,40}", text) else text[:12]


def parse_update_manifest(text: str) -> UpdateManifest:
    """Validate the release manifest strictly - it decides what gets executed.

    A sloppily parsed manifest is the one file an attacker controlling the feed
    could use to point the updater at an arbitrary URL, so asset names must be
    plain filenames, URLs must be GitHub hosts over HTTPS, and the digest must
    be a full lowercase SHA-256.
    """

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise BundleUpdateError(f"update manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BundleUpdateError("update manifest must be a JSON object")

    revision = payload.get("revision")
    version = payload.get("version")
    asset_name = payload.get("asset")
    asset_url = payload.get("asset_url")
    size = payload.get("size")
    sha256 = payload.get("sha256")

    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise BundleUpdateError("update manifest has no valid revision")
    if not isinstance(version, str) or not version.strip() or not version.isprintable():
        raise BundleUpdateError("update manifest has no valid version")
    if not isinstance(asset_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset_name):
        raise BundleUpdateError("update manifest has no valid asset name")
    if (
        not isinstance(asset_url, str)
        or not asset_url.startswith("https://")
        or not any(
            asset_url[len("https://") :].split("/", 1)[0].endswith(host)
            for host in _UPDATE_ASSET_HOSTS
        )
    ):
        raise BundleUpdateError("update manifest asset URL is not a GitHub HTTPS URL")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise BundleUpdateError("update manifest has no valid asset size")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise BundleUpdateError("update manifest has no valid SHA-256 digest")

    return UpdateManifest(
        revision=revision,
        version=version,
        asset_name=asset_name,
        asset_url=asset_url,
        size=size,
        sha256=sha256,
    )


def installed_revision(install_dir: PathInput) -> Optional[str]:
    """The revision marker written into the bundle at build time, if present."""

    marker = Path(install_dir) / UPDATE_REVISION_FILENAME
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def update_state(installed: Optional[str], manifest: Optional[UpdateManifest]) -> str:
    """'current', 'available', or 'development' - the honest tri-state.

    Without git metadata the app cannot order revisions, so any difference
    between the local marker and the feed is reported as an available update;
    the dialog always shows both revisions and the user decides.
    """

    if manifest is None:
        return "unavailable"
    if installed is None:
        return "development"
    if installed == manifest.revision:
        return "current"
    return "available"


# The swap itself runs outside the app: a running Windows executable cannot be
# replaced in place, so this script waits for the app to exit, renames the old
# bundle to a dated backup beside itself (the naming the installer already
# uses), moves the verified staging directory into place, and relaunches. Any
# failure rolls the backup back before giving up, so a half-applied update
# cannot leave the machine without a working Onionmind.
_APPLY_SCRIPT_TEMPLATE = r"""
param(
  [Parameter(Mandatory=$true)][int]$ParentPid,
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$StagingDir,
  [Parameter(Mandatory=$true)][string]$LogFile
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-ApplyLog([string]$Message) {
  try {
    [IO.File]::AppendAllText($LogFile, ("{0} {1}`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message), $Utf8NoBom)
  } catch { }
}

function Get-MarkerRevision([string]$Directory) {
  $Marker = Join-Path $Directory '@MARKER@'
  try { return [string](Get-Content -LiteralPath $Marker -ErrorAction Stop | Select-Object -First 1) }
  catch { return '' }
}

try {
  if (-not (Test-Path -LiteralPath (Join-Path $StagingDir '@EXE_NAME@') -PathType Leaf)) {
    throw "Staging directory has no @EXE_NAME@; refusing to swap."
  }

  # 1. The caller exits right after spawning this script; give it time to die
  #    so the old executable stops being locked.
  $Deadline = (Get-Date).AddSeconds(120)
  while ($true) {
    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
    if ((Get-Date) -gt $Deadline) { throw "Onionmind (pid $ParentPid) did not exit within 120s." }
    Start-Sleep -Milliseconds 500
  }
  Start-Sleep -Milliseconds 800

  $Parent = Split-Path -Parent $InstallDir
  $Leaf = Split-Path -Leaf $InstallDir
  $OldRevision = Get-MarkerRevision $InstallDir
  $OldShort = if ($OldRevision) { $OldRevision.Substring(0, [Math]::Min(7, $OldRevision.Length)) } else { 'unknown' }
  $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $Backup = Join-Path $Parent ("{0}.backup-before-{1}-{2}" -f $Leaf, $OldShort, $Stamp)

  # 2. Swap. Rename first so the move lands at the exact original path.
  Rename-Item -LiteralPath $InstallDir -NewName (Split-Path -Leaf $Backup)
  Write-ApplyLog "renamed old bundle to $(Split-Path -Leaf $Backup)"
  try {
    Move-Item -LiteralPath $StagingDir -Destination $InstallDir
    Write-ApplyLog "moved staged bundle into place"
  } catch {
    Rename-Item -LiteralPath $Backup -NewName $Leaf
    Write-ApplyLog "move failed; restored the previous bundle"
    throw
  }

  # 3. Keep the two newest backups only; older swaps otherwise pile up forever.
  Get-ChildItem -LiteralPath $Parent -Directory -Filter ($Leaf + '.backup-before-*') -ErrorAction SilentlyContinue |
    Sort-Object CreationTime -Descending |
    Select-Object -Skip 2 |
    ForEach-Object {
      try { Remove-Item -LiteralPath $_.FullName -Recurse -Force; Write-ApplyLog "pruned old backup $($_.Name)" }
      catch { Write-ApplyLog "could not prune old backup $($_.Name): $($_.Exception.Message)" }
    }

  # 4. Relaunch the freshly installed workbench. By this point the update is
  #    applied regardless, so a relaunch problem is logged, not fatal.
  try {
    Start-Process -FilePath (Join-Path $InstallDir '@EXE_NAME@') -WorkingDirectory $InstallDir
    Write-ApplyLog "relaunched the new workbench"
  } catch {
    Write-ApplyLog ("could not relaunch automatically; start Onionmind by hand: " + $_.Exception.Message)
  }
  Write-ApplyLog ("update applied; now running revision " + (Get-MarkerRevision $InstallDir))
  exit 0
} catch {
  Write-ApplyLog ("FAILED: " + $_.Exception.Message)
  exit 1
}
"""


class BundleUpdater:
    """Tor-only download and staging for the installed standalone bundle.

    ``proxies_factory`` mirrors ``onionmind._proxies``: it receives the SOCKS
    port and returns a requests proxy mapping, and it is called once per
    request so every fetch gets a fresh isolated circuit.
    """

    def __init__(
        self,
        install_dir: PathInput,
        work_dir: PathInput,
        proxies_factory: Callable[[int], dict[str, str]],
        user_agent: str,
        session: Optional[Any] = None,
    ) -> None:
        self.install_dir = Path(install_dir)
        self.work_dir = Path(work_dir)
        self.proxies_factory = proxies_factory
        self.user_agent = user_agent
        self._session = session

    def _request(self, method: str, url: str, port: int, **kwargs: Any) -> Any:
        import requests  # deferred: the pure module stays importable without it

        proxies = self.proxies_factory(port)
        if not proxies:
            raise BundleUpdateError("No verified Tor proxy is pinned for this update.")
        request = self._session or requests
        kwargs.setdefault("timeout", 90)
        kwargs.setdefault(
            "headers", {"User-Agent": self.user_agent, "Accept": "application/octet-stream"}
        )
        kwargs["proxies"] = proxies
        try:
            return request.request(method, url, **kwargs)
        except BundleUpdateError:
            raise
        except Exception as exc:
            raise BundleUpdateError(
                f"Could not reach the update feed over Tor: {exc}"
            ) from exc

    def fetch_manifest(self, port: int) -> UpdateManifest:
        response = self._request("GET", UPDATE_FEED_URL, port)
        self._raise_for_status(response)
        try:
            body = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleUpdateError("Update manifest is not UTF-8 text.") from exc
        return parse_update_manifest(body)

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        status = getattr(response, "status_code", 0)
        if status == 200:
            return
        detail = ""
        text = getattr(response, "text", "")
        if text:
            detail = ": " + text[:160].strip()
        raise BundleUpdateError(f"Update feed returned HTTP {status}{detail}")

    def download(
        self,
        port: int,
        manifest: UpdateManifest,
        progress: Optional[Callable[[Optional[float], str], None]] = None,
        stop_event: Optional[Any] = None,
    ) -> Path:
        """Stream the bundle zip through Tor into the work directory.

        The archive lands under a ``.part`` name and is verified against the
        manifest size and SHA-256 before it is allowed to keep the final name,
        so a truncated or tampered download can never be staged.
        """

        downloads = self.work_dir / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        final_path = downloads / f"{manifest.asset_name}"
        part_path = downloads / (manifest.asset_name + ".part")

        response = self._request(
            "GET",
            manifest.asset_url,
            port,
            stream=True,
            timeout=(60, 300),
        )
        self._raise_for_status(response)
        total = manifest.size
        digest = hashlib.sha256()
        done = 0
        last_note = -1.0
        try:
            with part_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=262144):
                    if stop_event is not None and stop_event.is_set():
                        raise BundleUpdateError("Update download was stopped.")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if done > total:
                        raise BundleUpdateError(
                            "Downloaded bundle is larger than the manifest announced."
                        )
                    if progress is not None and (done - last_note >= 1048576 or done == total):
                        last_note = float(done)
                        note = f"{done // 1048576} MB of {total // 1048576} MB over Tor"
                        progress(done / total if total else None, note)
        except BundleUpdateError:
            part_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(f"Could not write the update download: {exc}") from exc

        if done != total:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(
                f"Download stopped early at {done} of {total} bytes; nothing was installed."
            )
        if digest.hexdigest() != manifest.sha256:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(
                "Downloaded bundle failed the SHA-256 check; nothing was installed."
            )
        part_path.replace(final_path)
        return final_path

    def stage(self, manifest: UpdateManifest, archive_path: PathInput) -> Path:
        """Unpack a verified archive into a staging directory beside the install.

        Extraction is guarded against zip-slip (every member must stay inside
        the staging root) and the result must contain both the executable and a
        revision marker matching the manifest, so what gets swapped in is
        exactly what the feed described.
        """

        staging = self.work_dir / f"staging-{manifest.revision[:12]}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        root = staging.resolve()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.namelist():
                    resolved = (root / member).resolve()
                    if resolved != root and root not in resolved.parents:
                        raise BundleUpdateError(
                            f"Update archive entry escapes the staging directory: {member}"
                        )
                archive.extractall(root)
        except BundleUpdateError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError(f"Update archive could not be unpacked: {exc}") from exc

        if not (staging / "Onionmind.exe").is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError("Update archive has no Onionmind.exe; nothing was installed.")
        staged_revision = installed_revision(staging)
        if staged_revision != manifest.revision:
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError(
                "Update archive revision marker does not match the manifest; nothing was installed."
            )
        return staging

    def write_apply_script(self) -> Path:
        """Materialise the post-exit swap script and return its path."""

        self.work_dir.mkdir(parents=True, exist_ok=True)
        script_path = self.work_dir / "apply-onionmind-update.ps1"
        text = _APPLY_SCRIPT_TEMPLATE
        for token, value in (
            ("@MARKER@", UPDATE_REVISION_FILENAME),
            ("@EXE_NAME@", "Onionmind.exe"),
        ):
            text = text.replace(token, value)
        # BOM so Windows PowerShell 5.1 reads the script as UTF-8 regardless
        # of the system code page.
        script_path.write_text(text, encoding="utf-8-sig")
        return script_path

    def apply_command(self, staging_dir: PathInput) -> list[str]:
        """The detached command that finishes the update after the app exits."""

        script = self.write_apply_script()
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ParentPid",
            str(os.getpid()),
            "-InstallDir",
            str(self.install_dir),
            "-StagingDir",
            str(Path(staging_dir)),
            "-LogFile",
            str(self.work_dir / "apply.log"),
        ]


def pending_staging_dir(work_dir: PathInput) -> Optional[Path]:
    """A previously staged, still-verified bundle waiting to be applied."""

    work = Path(work_dir)
    if not work.is_dir():
        return None
    candidates = sorted(
        (entry for entry in work.iterdir() if entry.is_dir() and entry.name.startswith("staging-")),
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "Onionmind.exe").is_file() and installed_revision(candidate):
            return candidate
    return None


def prune_update_workdir(
    work_dir: PathInput,
    running_revision: Optional[str] = None,
    max_age_days: int = 14,
) -> None:
    """Drop long-lived downloads and stale staging directories.

    Staging directories whose revision matches the running bundle are stale by
    definition - the update they hold is already installed - so they go first.
    """

    work = Path(work_dir)
    if not work.is_dir():
        return
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    downloads = work / "downloads"
    if downloads.is_dir():
        for entry in downloads.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    for entry in work.iterdir():
        if not entry.is_dir() or not entry.name.startswith("staging-"):
            continue
        try:
            if installed_revision(entry) == running_revision or entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
DESKTOPCOREEOF
cat > "$DIR/onionmind_desktop.py" <<'DESKTOPUIEOF'
"""THESIS: Onionmind is one calm local-work loop, not a chat page ringed by dashboards.
OWN-WORLD: Matte warm graphite planes, fine charcoal seams, bone type, and aubergine selection; native controls stay compact and square-edged.
STORY: Choose a repository and Onionmind model, describe the work, watch an interruptible Chat or Agent run, then verify observed context, changes, and activity.
FIRST VIEWPORT: A 224px project/session rail, dominant open transcript with terminal drawer and composer, and a 292px three-tab inspector under a compact state toolbar.
FORM: Approved balanced workbench A; Operate mode; seed approved-onionmind-workbench-a.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
"""

from __future__ import annotations

import argparse
import base64
import copy
import dataclasses
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QPointF,
    QProcess,
    QRectF,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Onionmind"
APP_ID = "OnionmindDesktop"
MODULE_DIR = Path(__file__).resolve().parent
ACCENT = "#8d6aa0"
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_TEXT_FILE_BYTES = 64 * 1024
MAX_TEXT_TOTAL_BYTES = 256 * 1024
MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 24 * 1024 * 1024
MAX_IMAGE_COUNT = 4
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
_SCREENSHOT_PATH: Optional[str] = None


_STYLE_TEMPLATE = r"""
* {
    color: #eee8df;
}
QMainWindow, QDialog { background: #181715; }
QWidget#windowRoot, QWidget#centerPane, QWidget#transcriptViewport { background: #191816; }
QWidget#toolbar { background: #1c1b19; border-bottom: 1px solid #37342f; }
QWidget#leftRail { background: #1d1c1a; border-right: 1px solid #37342f; }
QWidget#inspector { background: #1c1b19; border-left: 1px solid #37342f; }
QFrame#terminalPane { background: #171615; border-top: 1px solid #403c36; }
QFrame#composerFrame { background: #211f1d; border-top: 1px solid #403c36; }
QFrame#card { background: #23211f; border: 1px solid #403c36; border-radius: 5px; }
QFrame#toolCard { background: #1e1d1b; border: 1px solid #423e38; border-radius: 5px; }
QFrame#modeSwitch { background: #191816; border: 1px solid #403c36; border-radius: 4px; }
QFrame#separator { background: #3b3833; min-width: 1px; max-width: 1px; }
QScrollArea#settingsPage { background: transparent; border: none; }
QScrollArea#settingsPage > QWidget > QWidget { background: transparent; }
QLabel { background: transparent; }
QLabel#brand { color: #f5efe7; font-size: @BRAND_PT@; font-weight: 650; }
QLabel#sectionTitle { color: #aaa39a; font-size: @LABEL_PT@; font-weight: 650; }
QLabel#muted, QLabel#meta, QLabel#disclosure { color: #aaa39a; }
QLabel#title { color: #f2ece4; font-weight: 650; }
QLabel#avatarUser { background: #74579a; color: #f9f5ef; border-radius: 16px; font-weight: 650; }
QLabel#avatarAssistant { background: #282327; color: #b791c9; border: 1px solid #624c6c; border-radius: 16px; font-weight: 700; }
QLabel#attachmentLabel { color: #cdbbd5; background: #2c252e; border: 1px solid #493d4e; border-radius: 3px; padding: 3px 7px; }
QLabel#success { color: #84c08f; }
QLabel#danger { color: #d88675; }
QLabel#accent { color: #c3a1d3; }
QLabel#thinkingLabel { color: #c9c1b7; }
QPushButton, QToolButton, QComboBox, QLineEdit {
    background: #24221f;
    border: 1px solid #45413b;
    border-radius: 4px;
    padding: 5px 9px;
    min-height: 18px;
}
QPushButton:hover, QToolButton:hover, QComboBox:hover { background: #2c2926; border-color: #5a554d; }
QPushButton:pressed, QToolButton:pressed { background: #191816; }
QPushButton:disabled, QToolButton:disabled { color: #6f6961; background: #211f1d; border-color: #37342f; }
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QTabWidget:focus {
    border: 1px solid #a481b4;
}
QPushButton#primaryButton { background: #5c4566; border-color: #765984; color: #faf6f0; font-weight: 600; padding-left: 17px; padding-right: 17px; }
QPushButton#primaryButton:hover { background: #684e73; border-color: #8a6b98; }
QPushButton#primaryButton:disabled { background: #2b2729; border-color: #403a3f; color: #777078; }
QPushButton#modeButton { background: transparent; border: none; padding: 4px 16px; color: #b8b1a8; }
QPushButton#modeButton:checked { background: #47364f; color: #f5edf8; border: 1px solid #684f73; }
QPushButton#railAction { text-align: left; background: transparent; border-color: transparent; padding: 7px 9px; }
QPushButton#railAction:hover { background: #292724; border-color: #3f3b36; }
QToolButton#bareButton { background: transparent; border-color: transparent; padding: 4px; }
QToolButton#bareButton:hover { background: #2a2825; border-color: #403c37; }
QToolButton#bareButton:checked { background: #3a3040; border-color: #684f73; }
QToolButton#bareButton:disabled { background: transparent; border-color: transparent; }
QComboBox { padding-right: 25px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #262421; border: 1px solid #4a453f; selection-background-color: #46384b; selection-color: #f5efe7; outline: 0; }
QMenu { background: #24221f; border: 1px solid #4a453f; padding: 4px; }
QMenu::item { border-radius: 3px; padding: 6px 26px 6px 9px; }
QMenu::item:selected { background: #3a3040; color: #f5edf8; }
QMenu::item:disabled { color: #746e67; }
QMenu::separator { height: 1px; background: #403c36; margin: 4px 7px; }
QListWidget, QTreeWidget {
    background: transparent;
    border: none;
    outline: 0;
    alternate-background-color: #211f1d;
}
QListWidget::item, QTreeWidget::item { border-radius: 3px; padding: 5px; }
QListWidget::item:hover, QTreeWidget::item:hover { background: #292724; }
QListWidget::item:selected, QTreeWidget::item:selected { background: #3a3040; color: #f5edf8; }
QTreeWidget::branch { background: transparent; }
QTreeView::indicator { width: 13px; height: 13px; border: 1px solid #625b53; border-radius: 2px; background: #1a1917; }
QTreeView::indicator:hover { border-color: #a98abb; }
QTreeView::indicator:checked { border-color: #a98abb; background: #7b5b8a; }
QTextEdit, QPlainTextEdit {
    background: #191816;
    border: 1px solid #403c36;
    border-radius: 4px;
    selection-background-color: #624d6d;
    selection-color: #fffaf5;
    padding: 7px;
}
QTextEdit#composer { background: #24211f; border: 1px solid #4a453e; padding: 10px; }
QPlainTextEdit#terminalOutput, QPlainTextEdit#diffView {
    font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: @MONO_PT@;
    background: #171615;
    border: none;
    border-radius: 0;
    padding: 7px 10px;
}
QLineEdit#terminalInput { font-family: "Cascadia Mono", "Consolas", monospace; background: #1b1a18; border-color: #3f3b36; }
QTabWidget::pane { border: none; border-top: 1px solid #38352f; top: -1px; }
QTabBar::tab { background: transparent; color: #b7b0a7; padding: 12px 16px 10px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: #eee8df; }
QTabBar::tab:selected { color: #f5efe8; border-bottom-color: #9570a7; }
QCheckBox { spacing: 7px; color: #c8c0b7; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #5b554e; background: #1a1917; border-radius: 3px; }
QCheckBox::indicator:checked { background: #86659a; border-color: #a17bb6; }
QProgressBar { background: #191816; border: 1px solid #403c36; border-radius: 3px; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: #80608f; }
QSplitter::handle { background: #37342f; }
QSplitter::handle:hover { background: #71557c; }
QStatusBar { background: #151412; border-top: 1px solid #35322d; color: #9f988f; }
QStatusBar::item { border: none; }
QPushButton#updateStatus { background: transparent; border: 1px solid transparent; border-radius: 4px; color: #b7b0a7; padding: 2px 8px; }
QPushButton#updateStatus:hover { background: #1d1b19; border-color: #4a453e; color: #f5efe8; }
QPushButton#updateStatus[attention="true"] { background: #71557c; border: 1px solid #a17bb6; border-radius: 4px; color: #faf6fd; font-weight: 600; padding: 2px 12px; }
QPushButton#updateStatus[attention="true"]:hover { background: #86659a; }
QPushButton#torStatusAction {
    background: #24221f;
    border-color: #45413b;
    color: #c8c0b7;
    font-weight: 500;
    min-width: 98px;
}
QPushButton#torStatusAction[torState="ready"] { background: #202620; border-color: #4d6653; color: #9bc8a5; }
QPushButton#torStatusAction[torState="checking"] { background: #27231d; border-color: #695a40; color: #d6b879; }
QPushButton#torStatusAction[torState="error"] { background: #29201e; border-color: #744b43; color: #df9383; }
QPushButton#torStatusAction:hover { background: #2c2926; border-color: #a481b4; color: #f5efe7; }
QPushButton#torStatusAction:pressed { background: #191816; border-color: #b793c6; }
QPushButton#torStatusAction:disabled { background: #211f1d; border-color: #37342f; color: #817982; }
QScrollBar:vertical { background: #191816; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #49443e; min-height: 30px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #5b554e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #191816; height: 10px; }
QScrollBar::handle:horizontal { background: #49443e; min-width: 30px; border-radius: 4px; margin: 2px; }
QToolTip { background: #2a2825; color: #f2ece4; border: 1px solid #514c45; padding: 5px; }
"""


def build_style_sheet(scale: float = 1.0) -> str:
    """The committed stylesheet with its three fixed point sizes scaled.

    Every other rule inherits the application font, which apply_text_scale
    scales from the captured platform base - so one knob moves all text
    without a second size system.
    """
    scale = max(0.75, min(1.5, float(scale)))
    return (
        _STYLE_TEMPLATE.replace("@BRAND_PT@", f"{11 * scale:g}pt")
        .replace("@LABEL_PT@", f"{8.5 * scale:g}pt")
        .replace("@MONO_PT@", f"{9 * scale:g}pt")
    )


STYLE_SHEET = build_style_sheet(1.0)

# Mirrors the core's PREFERENCE_DEFAULTS for the rare window that runs without
# a desktop_core; preferences are presentation-only and never touch the Tor
# boundary or the updater permission.
_FALLBACK_PREFERENCES: dict[str, Any] = {
    "text_scale": "system",
    "enter_sends": True,
    "show_terminal_on_launch": False,
    "startup_mode": "remember",
    "reduce_motion": "system",
    "save_history": True,
    "remember_drafts": True,
    "clear_on_exit": False,
    "context_window": 16384,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _path_key(value: Any) -> str:
    """Return a stable comparison key without requiring the path to still exist."""

    text = _as_text(value).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))


def _path_is_within(value: Any, directory: Any) -> bool:
    """Return whether value is the directory itself or one of its descendants."""

    path = _path_key(value)
    parent = _path_key(directory)
    if not path or not parent:
        return False
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:  # Different drives on Windows.
        return False


_BRAND_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bDeepSeek\s+Harness\b", re.IGNORECASE), "Onionmind Agent"),
    (re.compile(r"\bDeepSeek\b", re.IGNORECASE), "Onionmind"),
    (re.compile(r"\bHarness\b"), "Onionmind Agent"),
    (re.compile(r"\bDSH\b", re.IGNORECASE), "Onionmind Agent"),
    (re.compile(r"\bOllama\b", re.IGNORECASE), "Onionmind local engine"),
    (re.compile(r"\bllama(?:\.cpp|-server)?\b", re.IGNORECASE), "Onionmind local engine"),
    (re.compile(r"\bNode\.js\b", re.IGNORECASE), "Agent runtime"),
)


def _brand_runtime_text(value: Any) -> str:
    """Keep implementation brands behind Onionmind's product language."""
    text = _as_text(value)
    for pattern, replacement in _BRAND_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


_THINK_TAG_PATTERN = r"<\s*(/?)\s*think(?:\s[^>]*)?>"
_THINK_TAG = re.compile(_THINK_TAG_PATTERN, re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


def _think_tag_candidate(candidate: str) -> tuple[str, bool]:
    """Classify text beginning with ``<`` against prefixes of _THINK_TAG."""
    if not candidate.startswith("<"):
        return "invalid", False
    index, size = 1, len(candidate)
    while index < size and candidate[index].isspace():
        index += 1
    if index == size:
        return "prefix", False

    closing = candidate[index] == "/"
    if closing:
        index += 1
        while index < size and candidate[index].isspace():
            index += 1
        if index == size:
            return "prefix", True

    for expected in "think":
        if index == size:
            return "prefix", closing
        if re.fullmatch(expected, candidate[index], re.IGNORECASE) is None:
            return "invalid", closing
        index += 1
    if index == size:
        return "prefix", closing
    if candidate[index] == ">":
        match = _THINK_TAG.fullmatch(candidate[:index + 1])
        return ("complete", bool(match.group(1))) if match else ("invalid", closing)
    if not candidate[index].isspace():
        return "invalid", closing
    index += 1
    while index < size:
        if candidate[index] == ">":
            match = _THINK_TAG.fullmatch(candidate[:index + 1])
            return ("complete", bool(match.group(1))) if match else ("invalid", closing)
        index += 1
    return "prefix", closing


def _partial_think_tag(text: str) -> tuple[int, bool] | None:
    start = text.find("<")
    while start >= 0:
        state, closing = _think_tag_candidate(text[start:])
        if state == "prefix":
            return start, closing
        start = text.find("<", start + 1)
    return None


def _strip_thinking(text: Any) -> str:
    """Fail closed when removing completed or truncated reasoning blocks."""

    value = _as_text(text)
    visible: list[str] = []
    cursor = 0
    depth = 0
    for tag in _THINK_TAG.finditer(value):
        closing = bool(tag.group(1))
        if closing:
            if depth:
                depth -= 1
                if depth == 0:
                    cursor = tag.end()
            else:
                visible.clear()
                cursor = tag.end()
            continue
        if depth == 0:
            visible.append(value[cursor:tag.start()])
        depth += 1
    if depth == 0:
        tail = value[cursor:]
        partial = _partial_think_tag(tail)
        if partial is None:
            visible.append(tail)
        elif partial[1]:
            visible.clear()
        else:
            visible.append(tail[:partial[0]])
    return "".join(visible).strip()


def _sanitize_assistant_messages(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy history while preserving tool protocol and dropping reasoning."""

    sanitized: list[dict[str, Any]] = []
    for message in messages:
        item = copy.deepcopy(dict(message))
        if item.get("role") == "assistant":
            for key in list(item):
                if isinstance(key, str) and key.casefold() in _REASONING_FIELDS:
                    item.pop(key, None)
            content = item.get("content")
            item["content"] = _sanitize_assistant_content(content)
        sanitized.append(item)
    return sanitized


def _sanitize_assistant_content(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_thinking(value)
    if isinstance(value, dict):
        return {
            key: _sanitize_assistant_content(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_assistant_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_assistant_content(item) for item in value)
    return copy.deepcopy(value)


def _conversation_markdown(
    title: str,
    model: str,
    workspace: Optional[str],
    messages: Iterable[dict[str, Any]],
) -> str:
    """Format a local export after defensively cleaning legacy history."""

    lines = [f"# {title}", "", f"- Model: `{model}`"]
    if workspace:
        lines.append(f"- Workspace: `{workspace}`")
    lines.extend(("", "---", ""))
    for message in _sanitize_assistant_messages(messages):
        role = _as_text(message.get("role", "message"))
        heading = {"user": "Developer", "assistant": "Onionmind", "tool": "Local tool"}.get(
            role, role.title()
        )
        content = message.get("content")
        if not isinstance(content, str):
            content = "[local image attachment]"
        lines.extend((f"## {heading}", "", content, ""))
    return "\n".join(lines).rstrip() + "\n"


class ThinkingStreamFilter:
    """Bound model output until the completed response can be sanitized.

    Incremental display cannot safely handle a model that omits its opening
    reasoning tag: text may already be visible when a later closing tag proves
    it was private.  Buffering makes the completed sanitizer the only release
    boundary.  The hard limit bounds memory and fails closed on abnormal output.
    """

    MAX_CHARACTERS = 1_048_576

    def __init__(self, max_characters: int = MAX_CHARACTERS) -> None:
        if not isinstance(max_characters, int) or max_characters <= 0:
            raise ValueError("max_characters must be a positive integer")
        self._max_characters = max_characters
        self._chunks: list[str] = []
        self._characters = 0
        self._finished = False

    def feed(self, chunk: Any) -> str:
        """Store one transport chunk and deliberately emit nothing."""
        if self._finished:
            return ""
        text = _as_text(chunk)
        if self._characters + len(text) > self._max_characters:
            self.abort()
            raise RuntimeError("Model response exceeded the privacy buffer limit.")
        if text:
            self._chunks.append(text)
            self._characters += len(text)
        return ""

    def finish(self) -> str:
        """Sanitize one completed response, erase its raw chunks, and return it."""
        if self._finished:
            return ""
        raw = "".join(self._chunks)
        self._chunks.clear()
        self._characters = 0
        self._finished = True
        return _strip_thinking(raw)

    def abort(self) -> None:
        """Drop buffered model output after stop or failure."""
        self._chunks.clear()
        self._characters = 0
        self._finished = True


def _friendly_error(core: Any, exc: BaseException) -> str:
    text = _as_text(exc) or exc.__class__.__name__
    helper = getattr(core, "user_error", None)
    if callable(helper):
        try:
            text = _as_text(helper(exc)) or text
        except Exception:
            pass
    return _brand_runtime_text(text)


def _icon(name: str, size: int = 18, color: str = "#c9c1b7") -> QIcon:
    """Render Onionmind's compact, platform-neutral monochrome icon language."""
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor(color))
    pen.setWidthF(1.45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def box(x: float, y: float, width: float, height: float, radius: float = 1.2) -> None:
        painter.drawRoundedRect(QRectF(x, y, width, height), radius, radius)

    def file_shape() -> None:
        path = QPainterPath(QPointF(4.5, 2.5))
        path.lineTo(10.5, 2.5)
        path.lineTo(14, 6)
        path.lineTo(14, 15.5)
        path.lineTo(4.5, 15.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(10.5, 2.8, 10.5, 6)
        line(10.5, 6, 13.7, 6)

    def folder_shape(opened: bool = False) -> None:
        path = QPainterPath(QPointF(2.5, 5.5))
        path.lineTo(7, 5.5)
        path.lineTo(8.5, 7)
        path.lineTo(15.5, 7)
        if opened:
            path.lineTo(13.8, 14.5)
            path.lineTo(3.8, 14.5)
            path.lineTo(2.5, 8)
        else:
            path.lineTo(15.5, 14.5)
            path.lineTo(2.5, 14.5)
        path.closeSubpath()
        painter.drawPath(path)

    if name in {"file", "new_task"}:
        file_shape()
        if name == "new_task":
            line(6.2, 10, 10.2, 10)
            line(8.2, 8, 8.2, 12)
    elif name in {"folder", "folder_open", "folder_plus"}:
        folder_shape(name == "folder_open")
        if name == "folder_plus":
            line(7, 10.8, 11, 10.8)
            line(9, 8.8, 9, 12.8)
    elif name == "pencil":
        line(5, 13, 12.8, 5.2)
        line(11.4, 3.8, 14.2, 6.6)
        line(3.6, 14.4, 5, 13)
    elif name == "archive":
        box(3, 5.5, 12, 9)
        box(2.5, 3, 13, 3, 0.8)
        line(7, 9, 11, 9)
    elif name == "export":
        box(3, 8, 12, 7)
        line(9, 11, 9, 2.5)
        line(6.5, 5, 9, 2.5)
        line(11.5, 5, 9, 2.5)
    elif name == "model":
        box(3, 3, 12, 12, 2)
        painter.drawEllipse(QRectF(6.2, 6.2, 5.6, 5.6))
        line(9, 1.5, 9, 3)
        line(9, 15, 9, 16.5)
        line(1.5, 9, 3, 9)
        line(15, 9, 16.5, 9)
    elif name == "settings":
        painter.drawEllipse(QRectF(5.7, 5.7, 6.6, 6.6))
        painter.drawEllipse(QRectF(8, 8, 2, 2))
        for x1, y1, x2, y2 in ((9, 2, 9, 5), (9, 13, 9, 16), (2, 9, 5, 9), (13, 9, 16, 9), (4, 4, 6, 6), (12, 12, 14, 14), (14, 4, 12, 6), (6, 12, 4, 14)):
            line(x1, y1, x2, y2)
    elif name == "stop":
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(5, 5, 8, 8), 1.2, 1.2)
    elif name == "power":
        line(9, 2.5, 9, 8.8)
        painter.drawArc(QRectF(3.5, 4, 11, 11), 45 * 16, 270 * 16)
    elif name == "clear":
        box(5, 5.5, 8, 10, 1)
        line(3.8, 5.5, 14.2, 5.5)
        line(7, 3.2, 11, 3.2)
        line(7.5, 8, 7.5, 13)
        line(10.5, 8, 10.5, 13)
    elif name == "close":
        line(4.5, 4.5, 13.5, 13.5)
        line(13.5, 4.5, 4.5, 13.5)
    elif name == "refresh":
        painter.drawArc(QRectF(3, 3, 12, 12), 35 * 16, 280 * 16)
        line(12.8, 2.8, 15.2, 3.7)
        line(15.2, 3.7, 14.3, 6)
    elif name in {"rail", "terminal", "inspector"}:
        box(2.5, 3, 13, 12, 1.2)
        if name == "rail":
            line(6.5, 3.5, 6.5, 14.5)
        elif name == "inspector":
            line(11.5, 3.5, 11.5, 14.5)
        else:
            line(5, 7, 7.2, 9)
            line(7.2, 9, 5, 11)
            line(9.2, 11, 12.5, 11)
    elif name == "attach":
        path = QPainterPath(QPointF(6.2, 8.2))
        path.cubicTo(6.2, 4.2, 11.8, 4.2, 11.8, 8.2)
        path.lineTo(11.8, 12)
        path.cubicTo(11.8, 15.3, 6.2, 15.3, 6.2, 12)
        path.lineTo(6.2, 6.5)
        path.cubicTo(6.2, 3.2, 13.8, 3.2, 13.8, 7)
        path.lineTo(13.8, 11.5)
        painter.drawPath(path)
    else:
        box(4, 4, 10, 10)

    painter.end()
    return QIcon(canvas)


def _register_system_fonts(app: QApplication) -> None:
    """Register platform fonts and keep the platform's proportional UI size.

    The first proportional family that registers becomes the UI font, so the
    order is a legibility ranking rather than a list: Verdana and DejaVu Sans
    (its free cousin) carry a taller x-height and wider spacing than the
    platform defaults, which reads better at UI sizes without needing a bigger
    point size - and a bigger point size is what clips the two-line rows in
    the rail. Both ship with their platform, so nothing is bundled.
    """
    system_point_size = app.font().pointSizeF()
    candidates = [
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/verdanab.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/consolab.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    ui_family = ""
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if not ui_family and not QFontDatabase.isFixedPitch(family):
                ui_family = family
    if ui_family:
        ui_font = app.font()
        ui_font.setFamily(ui_family)
        if system_point_size > 0:
            ui_font.setPointSizeF(system_point_size)
        app.setFont(ui_font)


# The platform UI font captured before the first text-scale application, so
# every later scale multiplies the system's own point size instead of compound
# scaling whatever the previous preference left behind.
_BASE_APP_FONT: Optional[QFont] = None


def apply_text_scale(scale: float) -> None:
    """Scale all workbench text from the captured platform base font."""
    global _BASE_APP_FONT
    app = QApplication.instance()
    if app is None:
        return
    if _BASE_APP_FONT is None:
        _BASE_APP_FONT = QFont(app.font())
    font = QFont(_BASE_APP_FONT)
    size = _BASE_APP_FONT.pointSizeF()
    if size > 0:
        font.setPointSizeF(size * scale)
    app.setFont(font)
    app.setStyleSheet(build_style_sheet(scale))


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    event = Signal(object)
    progress = Signal(float, str)
    finished = Signal()


class SafeWorker:
    def __init__(self, fn: Callable[[WorkerSignals], Any], core: Any = None) -> None:
        self.fn = fn
        self.core = core
        self.signals = WorkerSignals()

    @staticmethod
    def _emit(signal: Any, *values: Any) -> None:
        try:
            signal.emit(*values)
        except RuntimeError:
            # The application may have closed while a daemon worker was
            # finishing a network or filesystem operation.
            pass

    def run(self) -> None:
        try:
            self._emit(self.signals.result, self.fn(self.signals))
        except BaseException as exc:  # core functions use SystemExit for user-facing failures
            if isinstance(exc, KeyboardInterrupt):
                self._emit(self.signals.error, "The operation was interrupted.")
            else:
                self._emit(self.signals.error, _friendly_error(self.core, exc))
        finally:
            self._emit(self.signals.finished)


class StatusDot(QWidget):
    def __init__(self, color: str = "#a39b91", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)
        self.setAccessibleName("Status indicator")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(2, 2, 6, 6)


class StatusPill(QFrame):
    clicked = Signal()

    COLORS = {
        "good": "#78b889",
        "warn": "#c9a36b",
        "bad": "#d47d6b",
        "idle": "#8e8880",
        "busy": "#a98abb",
    }

    def __init__(self, prefix: str, text: str, state: str = "idle", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self.setObjectName("statusPill")
        self.setStyleSheet("QFrame#statusPill { background:#211f1d; border:1px solid #403c36; border-radius:4px; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(5)
        prefix_label = QLabel(prefix)
        prefix_label.setObjectName("muted")
        self.dot = StatusDot(self.COLORS.get(state, self.COLORS["idle"]))
        self.label = QLabel(text)
        layout.addWidget(prefix_label)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.setAccessibleName(f"{prefix} status: {text}")

    def set_status(self, text: str, state: str = "idle") -> None:
        self.label.setText(text)
        self.dot.set_color(self.COLORS.get(state, self.COLORS["idle"]))
        self.setAccessibleName(f"{self.prefix} status: {text}")

    def make_clickable(self) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusActionButton(QPushButton):
    """Compact native Tor toggle: current state at rest, action in its semantics."""

    COLORS = StatusPill.COLORS

    def __init__(
        self,
        prefix: str,
        text: str,
        state: str,
        action: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self._status_text = text
        self._action_text = action
        self._action_accessible = action
        self.setObjectName("torStatusAction")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_status(text, state)

    def status_text(self) -> str:
        return self._status_text

    def action_text(self) -> str:
        return self._action_text

    def set_status(self, text: str, state: str = "idle") -> None:
        self._status_text = text
        visual_state = {
            "good": "ready",
            "warn": "checking",
            "busy": "checking",
            "bad": "error",
        }.get(state, "off")
        self.setProperty("torState", visual_state)
        self.setIcon(_icon("power", 14, self.COLORS.get(state, self.COLORS["idle"])))
        self.setIconSize(QSize(14, 14))
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self._sync_copy()

    def set_action(
        self,
        text: str,
        *,
        enabled: bool,
        tooltip: str,
        accessible: str,
    ) -> None:
        self._action_text = text
        self._action_accessible = accessible
        self.setEnabled(enabled)
        self.setToolTip(tooltip)
        self._sync_copy()

    def _sync_copy(self) -> None:
        self.setText(f"{self.prefix} · {self._status_text}")
        self.setAccessibleName(
            f"{self.prefix} status: {self._status_text}. {self._action_accessible}"
        )


def _tor_is_enabled(core: Any) -> bool:
    getter = getattr(core, "tor_enabled", None)
    if not callable(getter):
        return True
    try:
        return bool(getter())
    except Exception:
        return False


def _set_tor_enabled(core: Any, enabled: bool) -> bool:
    setter = getattr(core, "set_tor_enabled", None)
    if not callable(setter):
        return False
    setter(enabled)
    return True


def _set_tor_action(
    host: Any,
    text: str,
    *,
    enabled: bool = True,
    tooltip: str = "",
) -> None:
    """Keep the Tor status/action control explicit, operable, and truthful."""
    accessible = {
        "Turn on": "Turn on Tor proxy",
        "Turn off": "Turn off Tor proxy for Onionmind",
        "Cancel": "Cancel Tor proxy startup",
        "Retry": "Retry Tor proxy verification",
        "External": "Tor proxy is managed outside Onionmind",
        "Unavailable": "Tor proxy control is unavailable",
    }.get(text, f"Tor proxy action: {text}")
    status_control = getattr(host, "tor_status", None)
    set_action = getattr(status_control, "set_action", None)
    if callable(set_action):
        set_action(text, enabled=enabled, tooltip=tooltip, accessible=accessible)


# The reduce-motion preference as a process-wide override. None means follow
# the system (and the ONIONMIND_REDUCE_MOTION environment variable).
_MOTION_OVERRIDE: Optional[bool] = None


def set_motion_override(reduce_motion: str) -> None:
    global _MOTION_OVERRIDE
    if reduce_motion == "reduced":
        _MOTION_OVERRIDE = False
    elif reduce_motion == "full":
        _MOTION_OVERRIDE = True
    else:
        _MOTION_OVERRIDE = None


def _ui_animations_enabled() -> bool:
    if _MOTION_OVERRIDE is not None:
        return _MOTION_OVERRIDE
    override = os.environ.get("ONIONMIND_REDUCE_MOTION", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION follows Windows' Animation effects setting.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


def _apply_native_dark_title_bar(window: Any) -> None:
    """Pin the Windows title bar to dark regardless of the system scheme.

    The workbench is dark by design on every platform, so on a light-mode
    Windows install the native frame would be the one bright surface in the
    room. DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20; 19 on pre-2004 builds)
    matches it to the body. Purely cosmetic: every failure path is silent and
    the app runs identically without it.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(window.winId()), attribute, ctypes.byref(ctypes.c_int(1)), 4
            ) == 0:
                return
    except (AttributeError, OSError, TypeError, ValueError):
        pass


class ThinkingDots(QWidget):
    """A tiny, low-cost progress cue; adjacent text carries the meaning."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._frame = 0
        self.setFixedSize(34, 16)
        self.setAccessibleName("Thinking progress")

    def advance(self) -> None:
        self._frame = (self._frame + 1) % 3
        self.update()

    def reset(self) -> None:
        self._frame = 0
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, x in enumerate((6, 17, 28)):
            active = index == self._frame
            painter.setBrush(QColor("#b791c9" if active else "#625b63"))
            radius = 3.0 if active else 2.5
            painter.drawEllipse(QPointF(float(x), 8.0), radius, radius)


class ThinkingIndicator(QWidget):
    """Accessible pending state that becomes static when motion is reduced."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._running = False
        self._motion_enabled = _ui_animations_enabled()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 2)
        layout.setSpacing(6)
        self.label = QLabel("Thinking")
        self.label.setObjectName("thinkingLabel")
        self.dots = ThinkingDots(self)
        layout.addWidget(self.label)
        layout.addWidget(self.dots)
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(280)
        self.timer.timeout.connect(self.dots.advance)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._application_state_changed)
        self.setAccessibleName("Onionmind is thinking")
        self.setAccessibleDescription("A local response is pending")
        self.hide()

    def start(self, text: str = "Thinking") -> None:
        self._running = True
        self.set_label(text)
        self.dots.reset()
        self.show()
        self._sync_timer()

    def stop(self) -> None:
        self._running = False
        self.timer.stop()
        self.hide()

    def set_label(self, text: str) -> None:
        label = text.strip() or "Thinking"
        self.label.setText(label)
        self.setAccessibleName(f"Onionmind is {label.lower()}")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event: Any) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def _application_state_changed(self, state: Qt.ApplicationState) -> None:
        del state
        self._sync_timer()

    def _sync_timer(self) -> None:
        app = QApplication.instance()
        active = app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive
        should_run = self._running and self._motion_enabled and self.isVisible() and active
        if should_run:
            self.timer.start()
        else:
            self.timer.stop()


class ComposerEdit(QTextEdit):
    sendRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAcceptDrops(True)
        self.setAccessibleName("Task composer")
        self.setTabChangesFocus(True)
        # A workbench preference: Enter sends (Shift+Enter is always a newline),
        # or Ctrl+Enter sends when Enter-to-send is turned off.
        self.enterSends = True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            control = bool(
                event.modifiers()
                & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            )
            if not shift and (self.enterSends or control):
                self.sendRequested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: Any) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MessageBlock(QWidget):
    def __init__(self, role: str, text: str = "", name: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.role = role
        self._text = text
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 12)
        outer.setSpacing(12)
        self.avatar = QLabel("DE" if role == "user" else "O")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(32, 32)
        self.avatar.setObjectName("avatarUser" if role == "user" else "avatarAssistant")
        self.avatar.setAccessibleName("Developer" if role == "user" else "Onionmind")
        if role != "user" and (MODULE_DIR / "onionmind.ico").exists():
            self.avatar.setText("")
            self.avatar.setPixmap(
                QPixmap(str(MODULE_DIR / "onionmind.ico")).scaled(
                    18,
                    18,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        outer.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(7)
        who = QLabel(name or ("Developer" if role == "user" else "Onionmind"))
        who.setObjectName("title")
        timestamp = QLabel(datetime.now().strftime("%I:%M %p").lstrip("0"))
        timestamp.setObjectName("meta")
        header.addWidget(who)
        header.addWidget(timestamp)
        header.addStretch(1)
        column.addLayout(header)
        self.body = QLabel(text)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setMinimumHeight(18)
        self.body.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._target_body_width = max(520, self.body.fontMetrics().horizontalAdvance("0" * 74))
        self.set_reading_width(self._target_body_width)
        self._author_name = who.text()
        self.body.setAccessibleName(f"{self._author_name} message")
        self.thinking = ThinkingIndicator(self)
        column.addWidget(self.thinking)
        column.addWidget(self.body)
        outer.addLayout(column, 1)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self.stop_thinking()
        self._text = text
        self.body.setText(text)
        self._sync_body_height()

    def append_text(self, text: str) -> None:
        if text:
            self.stop_thinking()
        self._text += text
        self.body.setText(self._text)
        self._sync_body_height()

    def start_thinking(self, text: str = "Thinking") -> None:
        self._text = ""
        self.body.clear()
        self.body.hide()
        self.thinking.start(text)
        self.setAccessibleName(self.thinking.accessibleName())
        self.updateGeometry()

    def set_pending_label(self, text: str) -> None:
        if self.thinking._running:
            self.thinking.set_label(text)
            self.setAccessibleName(self.thinking.accessibleName())

    def stop_thinking(self) -> None:
        if self.thinking._running or not self.thinking.isHidden():
            self.thinking.stop()
            self.body.show()
            self.setAccessibleName(f"{self._author_name} message")
            self._sync_body_height()

    def set_reading_width(self, available_width: int) -> None:
        body_width = max(240, min(self._target_body_width, available_width))
        self.body.setFixedWidth(body_width)
        self.setFixedWidth(body_width + 56)
        self._sync_body_height()

    def _sync_body_height(self) -> None:
        required_height = self.body.heightForWidth(max(1, self.body.width()))
        if required_height < 0:
            required_height = self.body.sizeHint().height()
        self.body.setFixedHeight(max(18, required_height))
        self.body.updateGeometry()
        own_layout = self.layout()
        if own_layout is not None:
            own_layout.invalidate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().invalidate()


class ToolActivityCard(QFrame):
    def __init__(self, title: str, rows: Iterable[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        actual_rows = list(rows)
        self.setObjectName("toolCard")
        self._target_width = max(560, self.fontMetrics().horizontalAdvance("0" * 78))
        self.set_reading_width(self._target_width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(11, 8, 11, 8)
        outer.setSpacing(5)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("title")
        count = QLabel(f"{len(actual_rows)} items")
        count.setObjectName("meta")
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(count)
        outer.addLayout(header)
        for path, state in actual_rows:
            line = QHBoxLayout()
            file_label = QLabel(path)
            file_label.setTextFormat(Qt.TextFormat.PlainText)
            file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            state_label = QLabel(state)
            state_label.setObjectName("accent")
            line.addWidget(file_label, 1)
            line.addWidget(state_label)
            outer.addLayout(line)

    def set_reading_width(self, available_width: int) -> None:
        self.setFixedWidth(max(280, min(self._target_width, available_width)))


class TranscriptView(QScrollArea):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Conversation transcript")
        self.viewport().setObjectName("transcriptViewport")
        self.content = QWidget()
        self.content.setObjectName("transcriptViewport")
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(48, 20, 48, 24)
        self.layout.setSpacing(2)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._message_blocks: list[MessageBlock] = []
        self._tool_cards: list[ToolActivityCard] = []
        self.setWidget(self.content)

    def clear(self) -> None:
        self._message_blocks.clear()
        self._tool_cards.clear()
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def add_message(self, role: str, text: str = "", name: Optional[str] = None) -> MessageBlock:
        block = MessageBlock(role, text, name)
        self._message_blocks.append(block)
        self.layout.addWidget(block, 0, Qt.AlignmentFlag.AlignLeft)
        self._apply_reading_widths()
        self._scroll_later()
        return block

    def add_tool_card(self, title: str, rows: list[tuple[str, str]]) -> ToolActivityCard:
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(44, 0, 0, 12)
        card = ToolActivityCard(title, rows)
        self._tool_cards.append(card)
        holder_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        holder_layout.addStretch(1)
        self.layout.addWidget(holder)
        self._apply_reading_widths()
        self._scroll_later()
        return card

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_reading_widths()

    def _apply_reading_widths(self) -> None:
        margins = self.layout.contentsMargins()
        content_width = max(320, self.viewport().width() - margins.left() - margins.right())
        for block in self._message_blocks:
            block.set_reading_width(content_width - 56)
        for card in self._tool_cards:
            card.set_reading_width(content_width - 44)

    def _scroll_later(self) -> None:
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))


class SessionBridge:
    """Use onionmind_desktop_core when present; keep a small QSettings fallback."""

    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.desktop_core = desktop_core
        self.root = root
        self.store = None
        if desktop_core is not None and hasattr(desktop_core, "SessionStore"):
            try:
                self.store = desktop_core.SessionStore(root)
            except Exception:
                self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)

    @staticmethod
    def _clean_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return _sanitize_assistant_messages(messages)

    @classmethod
    def _clean_session(cls, session: Any) -> Any:
        messages = cls._clean_messages(_field(session, "messages", ()) or ())
        if dataclasses.is_dataclass(session):
            return dataclasses.replace(session, messages=messages)
        if isinstance(session, dict):
            cleaned = copy.deepcopy(session)
            cleaned["messages"] = messages
            return cleaned
        setattr(session, "messages", messages)
        return session

    def create(self, title: str, model: str, workspace: Optional[str], messages: Iterable[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            return self.store.create(title=title, model=model, workspace=workspace, messages=clean_messages)
        now = _now_iso()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "model": model,
            "workspace": workspace,
            "messages": clean_messages,
            "created_at": now,
            "updated_at": now,
        }

    def list(self, *, archived: bool = False) -> list[Any]:
        if self.store is not None:
            try:
                return [
                    self._clean_session(item)
                    for item in self.store.list(archived=archived)
                ]
            except Exception:
                return []
        key = "archived_sessions" if archived else "sessions"
        try:
            return [
                self._clean_session(item)
                for item in json.loads(self.fallback.value(key, "[]"))
            ]
        except (TypeError, ValueError):
            return []

    def save(self, session: Any, *, title: str, model: str, workspace: Optional[str], messages: list[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            if dataclasses.is_dataclass(session):
                session = dataclasses.replace(
                    session,
                    title=title,
                    model=model,
                    workspace=workspace,
                    messages=clean_messages,
                )
            else:
                for key, value in {
                    "title": title,
                    "model": model,
                    "workspace": workspace,
                    "messages": clean_messages,
                }.items():
                    setattr(session, key, value)
            return self.store.save(session)
        payload = dict(session)
        payload.update(
            title=title,
            model=model,
            workspace=workspace,
            messages=clean_messages,
            updated_at=_now_iso(),
        )
        sessions = [s for s in self.list() if _field(s, "id") != payload["id"]]
        sessions.insert(0, payload)
        self.fallback.setValue("sessions", json.dumps(sessions[:80]))
        return payload

    def archive(self, session_id: str) -> Any:
        if self.store is not None:
            try:
                return self.store.archive(session_id)
            except Exception:
                return None
        sessions = self.list()
        archived = next((item for item in sessions if _as_text(_field(item, "id")) == session_id), None)
        remaining = [item for item in sessions if _as_text(_field(item, "id")) != session_id]
        self.fallback.setValue("sessions", json.dumps(remaining[:80]))
        if archived is not None:
            try:
                archive_items = list(json.loads(self.fallback.value("archived_sessions", "[]")))
            except (TypeError, ValueError):
                archive_items = []
            archive_items.insert(0, archived)
            self.fallback.setValue("archived_sessions", json.dumps(archive_items[:80]))
        return archived

    def delete(self, session_id: str) -> bool:
        """Permanently remove a session instead of moving it to local archive storage."""

        if self.store is not None:
            deleter = getattr(self.store, "delete", None)
            if not callable(deleter):
                return False
            try:
                return bool(deleter(session_id))
            except Exception:
                return False

        deleted = False
        for key in ("sessions", "archived_sessions"):
            try:
                items = list(json.loads(self.fallback.value(key, "[]")))
            except (TypeError, ValueError):
                items = []
            remaining = [
                item for item in items if _as_text(_field(item, "id")) != session_id
            ]
            if len(remaining) != len(items):
                deleted = True
                self.fallback.setValue(key, json.dumps(remaining[:80]))
        return deleted


class SettingsBridge:
    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)
        if desktop_core is not None and hasattr(desktop_core, "SettingsStore"):
            try:
                self.store = desktop_core.SettingsStore(root / "settings.json", defaults={})
            except Exception:
                self.store = None

    def load(self) -> dict[str, Any]:
        if self.store is not None:
            try:
                return dict(self.store.load())
            except Exception:
                return {}
        try:
            return dict(json.loads(self.fallback.value("settings_json", "{}")))
        except (TypeError, ValueError):
            return {}

    def save(self, settings: dict[str, Any]) -> None:
        if self.store is not None:
            try:
                self.store.save(settings)
                return
            except Exception:
                pass
        self.fallback.setValue("settings_json", json.dumps(settings))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _optional_callable(source: Any, name: str) -> Optional[Callable[..., Any]]:
    fn = getattr(source, name, None)
    return fn if callable(fn) else None


class WorkspaceBridge:
    def __init__(self, desktop_core: Any) -> None:
        self.inspector = None
        if desktop_core is not None and hasattr(desktop_core, "WorkspaceInspector"):
            try:
                self.inspector = desktop_core.WorkspaceInspector(max_entries=260, max_depth=5)
            except Exception:
                self.inspector = None

    def inspect(self, selected: str) -> dict[str, Any]:
        if self.inspector is not None:
            snap = self.inspector.inspect(selected)
            try:
                diff = self.inspector.diff(selected)
            except Exception as exc:
                diff = f"Diff unavailable: {exc}"
            changes = [
                {
                    "status": _field(change, "status", "?"),
                    "path": _field(change, "path", ""),
                    "original_path": _field(change, "original_path"),
                }
                for change in (_field(snap, "changes", ()) or ())
            ]
            return {
                "root": _as_text(_field(snap, "root", selected)),
                "is_git": bool(_field(snap, "is_git", False)),
                "branch": _as_text(_field(snap, "branch", "No repository")),
                "dirty": bool(_field(snap, "dirty", False)),
                "changes": changes,
                "agents_files": [_as_text(p) for p in (_field(snap, "agents_files", ()) or ())],
                "file_tree": [_as_text(p) for p in (_field(snap, "file_tree", ()) or ())],
                "tree_truncated": bool(_field(snap, "tree_truncated", False)),
                "summary": _as_text(_field(snap, "change_summary", "")),
                "diff": diff,
            }
        return self._fallback_inspect(selected)

    def _fallback_inspect(self, selected: str) -> dict[str, Any]:
        root = Path(selected).resolve()
        files: list[str] = []
        agents: list[str] = []
        for current, dirs, names in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            safe_directories: list[str] = []
            for directory_name in dirs:
                if directory_name in SKIP_DIRS or directory_name.startswith("."):
                    continue
                candidate = current_path / directory_name
                is_junction = bool(
                    getattr(candidate, "is_junction", lambda: False)()
                )
                if candidate.is_symlink() or is_junction:
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(root)
                except (OSError, ValueError):
                    continue
                safe_directories.append(directory_name)
            dirs[:] = safe_directories
            if depth >= 5:
                dirs[:] = []
            for name in sorted(names):
                relative = (current_path / name).relative_to(root).as_posix()
                files.append(relative)
                if name.upper() == "AGENTS.MD":
                    agents.append(relative)
                if len(files) >= 260:
                    break
            if len(files) >= 260:
                break
        git = shutil.which("git")
        branch, changes, diff = "No repository", [], "This folder is not a Git repository."
        is_git = False
        if git:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            git_base = [
                git,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
            ]
            try:
                probe = subprocess.run(
                    [*git_base, "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True,
                    text=True, timeout=6, creationflags=flags,
                )
                is_git = probe.returncode == 0 and probe.stdout.strip() == "true"
                if is_git:
                    branch_run = subprocess.run(
                        [*git_base, "branch", "--show-current"], cwd=root, capture_output=True,
                        text=True, timeout=6, creationflags=flags,
                    )
                    branch = branch_run.stdout.strip() or "detached HEAD"
                    status_run = subprocess.run(
                        [*git_base, "status", "--short", "--untracked-files=normal", "--ignore-submodules=all"], cwd=root, capture_output=True,
                        text=True, timeout=8, creationflags=flags,
                    )
                    for line in status_run.stdout.splitlines():
                        if not line.strip():
                            continue
                        changes.append({"status": line[:2].strip() or "?", "path": line[3:], "original_path": None})
                    diff_run = subprocess.run(
                        [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3", "HEAD", "--"], cwd=root, capture_output=True,
                        text=True, timeout=10, creationflags=flags,
                    )
                    diff = diff_run.stdout
                    if diff_run.returncode != 0:
                        cached_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--cached", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        working_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        diff = cached_run.stdout + working_run.stdout
                    untracked = [
                        change["path"] for change in changes if change["status"] == "??"
                    ]
                    if untracked:
                        diff += "\nUntracked files (content preview unavailable in fallback mode):\n"
                        diff += "".join(f"  {path}\n" for path in untracked[:50])
                    diff = diff[:200000] or "No staged or unstaged diff."
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "root": str(root), "is_git": is_git, "branch": branch, "dirty": bool(changes),
            "changes": changes, "agents_files": agents, "file_tree": files,
            "tree_truncated": len(files) >= 260, "summary": f"{len(changes)} observed change(s)", "diff": diff,
        }


class HarnessBridge:
    FALLBACK_LIMITATION = (
        "Onionmind Agent is an early-access local coding workflow. Approvals are on "
        "by default, and YOLO runs edits and commands without asking - which never "
        "moves the network boundary. "
        "The agent reaches the web only through Tor and does not start without it.\n\n"
        "Tor is enforced by the environment the agent runs in, not by the operating "
        "system: a compiled binary, python -S, or a tool that ignores proxies (ping, "
        "nslookup) can still reach the network directly. Closing that needs an OS "
        "egress rule - a firewall rule, a container - or the Matchstick live USB."
    )

    def __init__(self, desktop_core: Any, core: Any = None) -> None:
        self.desktop_core = desktop_core
        self.core = core
        self.spec = None
        if desktop_core is not None and hasattr(desktop_core, "HarnessSpec"):
            try:
                self.spec = desktop_core.HarnessSpec()
            except Exception:
                self.spec = None

    def _launcher(self, model: str, task: str, cwd: str = "",
                  yolo: bool = False) -> Optional[list[str]]:
        """``onionmind.py --agent``: the one place Tor is verified and enforced.

        Launching the agent directly would inherit this window's environment,
        which has no proxy and no socket containment - the agent would have
        direct web access whether Tor is up or not.
        """
        script = _as_text(getattr(self.core, "__file__", ""))
        if not script or not callable(getattr(self.core, "run_agent", None)):
            return None
        argv = [sys.executable, os.path.abspath(script), "--agent",
                "--model", model]
        if yolo:
            argv.append("--yolo")
        if cwd:
            # The working directory is an argument, not just the process cwd:
            # run_agent writes the project settings there before it starts.
            argv += ["--cwd", cwd]
        return argv + [task]

    @property
    def limitation(self) -> str:
        if self.spec is not None:
            value = _as_text(getattr(self.spec, "limitation", "")) or _as_text(
                getattr(self.desktop_core, "HARNESS_LIMITATION", "")
            ) or self.FALLBACK_LIMITATION
            return _brand_runtime_text(value)
        return self.FALLBACK_LIMITATION

    def check(self) -> tuple[bool, str]:
        # The launcher IS the Tor boundary, so without one there is nothing to
        # start: launching the harness ourselves would hand it this window's
        # unproxied environment.
        if self._launcher("model", "task") is None:
            return False, (
                "Onionmind Agent needs the Onionmind runtime to route it through Tor, "
                "and that runtime is not loaded. Re-run Onionmind setup, then restart."
            )
        # A hint, not the gate: re-verifying Tor here would repeat the round trip
        # the launcher makes anyway, on every task. The launcher is what refuses.
        if not getattr(self.core, "_port", None):
            return False, (
                "Tor is not up. Onionmind Agent reaches the web only through Tor and "
                "does not start without it. Start Tor from the toolbar, then try again."
            )
        if self.spec is not None:
            availability = self.spec.check()
            return bool(_field(availability, "available", False)), _brand_runtime_text(
                _field(availability, "reason", "")
            )
        executable = shutil.which("ollama")
        return bool(executable), (
            ""
            if executable
            else "Onionmind Agent is not ready. Re-run Onionmind setup, then restart the app."
        )

    def build(self, *, model: str, task: str, cwd: str,
              yolo: bool = False) -> tuple[list[str], str]:
        launcher = self._launcher(model, task, cwd, yolo)
        if launcher is None:                     # check() refuses first; belt and braces
            raise RuntimeError(
                "Onionmind Agent has no Tor-verified launcher, so it will not start."
            )
        return launcher, cwd


class UpdateBridge:
    """Tor-only self-update for the installed standalone bundle.

    Source installs and development checkouts have no bundle to swap, so the
    bridge reports itself unavailable there and the UI says so instead of
    half-offering an update. Every network call goes through the verified Tor
    port with a fresh isolated circuit, exactly like Chat search: a failed
    check never falls back to a direct request.
    """

    def __init__(self, core: Any, desktop_core: Any) -> None:
        self.core = core
        self.desktop_core = desktop_core
        # Nuitka puts __compiled__ into each compiled module's globals (and
        # sets sys.frozen in standalone); a plain `python onionmind_desktop.py`
        # checkout has neither.
        frozen = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))
        candidate = Path(sys.executable).resolve().parent if frozen else None
        if candidate is not None and not (candidate / "Onionmind.exe").is_file():
            candidate = None
        self.install_dir: Optional[Path] = candidate
        self.work_dir: Optional[Path] = (
            self.install_dir.parent / "onionmind-update" if self.install_dir else None
        )

    @property
    def available(self) -> bool:
        return self.desktop_core is not None and self.install_dir is not None

    def revision(self) -> Optional[str]:
        reader = getattr(self.desktop_core, "installed_revision", None)
        if not self.available or not callable(reader):
            return None
        return reader(self.install_dir)

    def revision_label(self) -> str:
        revision = self.revision()
        if revision is None:
            return "development copy" if not self.available else "unknown revision"
        helper = getattr(self.desktop_core, "short_revision", None)
        return f"revision {helper(revision) if callable(helper) else revision[:7]}"

    def _updater(self) -> Any:
        factory = getattr(self.core, "_proxies", None)
        if not callable(factory):
            raise RuntimeError("This Onionmind build cannot build Tor proxy settings.")
        user_agent = _as_text(getattr(self.core, "UA", "")) or (
            "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        return self.desktop_core.BundleUpdater(
            self.install_dir,
            self.work_dir,
            lambda port: factory(port, True),   # fresh credentials => fresh circuit
            user_agent,
        )

    def tor_port(self) -> Optional[int]:
        port = getattr(self.core, "_port", None)
        return int(port) if port else None

    def check(self) -> Any:
        """Fetch and validate the feed manifest. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct update check.")
        return self._updater().fetch_manifest(port)

    def download(
        self,
        manifest: Any,
        progress: Callable[[Optional[float], str], None],
        stop_event: Any,
    ) -> Path:
        """Download, verify, and stage the new bundle. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct download.")
        updater = self._updater()
        archive = updater.download(port, manifest, progress=progress, stop_event=stop_event)
        return updater.stage(manifest, archive)

    def pending(self) -> Optional[Path]:
        finder = getattr(self.desktop_core, "pending_staging_dir", None)
        if not self.available or not callable(finder):
            return None
        return finder(self.work_dir)

    def housekeep(self) -> None:
        pruner = getattr(self.desktop_core, "prune_update_workdir", None)
        if self.available and callable(pruner):
            try:
                pruner(self.work_dir, running_revision=self.revision())
            except OSError:
                pass

    def apply_command(self, staging_dir: str) -> list[str]:
        return self._updater().apply_command(staging_dir)


class LeftRail(QWidget):
    newTaskRequested = Signal()
    addSessionRequested = Signal()
    openFolderRequested = Signal()
    sessionSelected = Signal(str)
    projectSelected = Signal(str)
    removeProjectRequested = Signal(str)
    deleteProjectRequested = Signal(str)
    modelsRequested = Signal()
    settingsRequested = Signal()
    exportRequested = Signal()
    archiveRequested = Signal(str)
    deleteSessionRequested = Signal(str)
    renameSessionRequested = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftRail")
        self.setMinimumWidth(190)
        self.setMaximumWidth(290)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(9)

        quick = QHBoxLayout()
        new_button = QPushButton("New task")
        new_button.setObjectName("newTaskButton")
        new_button.setIcon(_icon("new_task"))
        new_button.setAccessibleName("Create new task")
        new_button.clicked.connect(self.newTaskRequested)
        self.new_button = new_button
        folder_button = QToolButton()
        folder_button.setObjectName("openFolderButton")
        folder_button.setIcon(_icon("folder_open"))
        folder_button.setToolTip("Open folder (Ctrl+O)")
        folder_button.setAccessibleName("Open project folder")
        folder_button.clicked.connect(self.openFolderRequested)
        self.folder_button = folder_button
        quick.addWidget(new_button, 1)
        quick.addWidget(folder_button)
        layout.addLayout(quick)

        project_header = QHBoxLayout()
        project_label = QLabel("PROJECTS")
        project_label.setObjectName("sectionTitle")
        add_project = QToolButton()
        add_project.setObjectName("bareButton")
        add_project.setIcon(_icon("folder_plus"))
        add_project.setToolTip("Open another project")
        add_project.setAccessibleName("Open another project")
        add_project.clicked.connect(self.openFolderRequested)
        self.add_project_button = add_project
        remove_project = QToolButton()
        remove_project.setObjectName("bareButton")
        remove_project.setIcon(_icon("close"))
        remove_project.setToolTip(
            "Remove selected project from this list; its folder stays on this machine"
        )
        remove_project.setAccessibleName(
            "Remove selected project from Projects and keep its folder"
        )
        remove_project.clicked.connect(self._remove_selected_project)
        remove_project.setEnabled(False)
        self.remove_project_button = remove_project
        delete_project = QToolButton()
        delete_project.setObjectName("bareButton")
        delete_project.setIcon(_icon("clear"))
        delete_project.setToolTip(
            "Permanently delete the selected project folder from this machine"
        )
        delete_project.setAccessibleName(
            "Permanently delete selected project folder from this machine"
        )
        delete_project.clicked.connect(self._delete_selected_project)
        delete_project.setEnabled(False)
        self.delete_project_button = delete_project
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        project_header.addWidget(add_project)
        project_header.addWidget(remove_project)
        project_header.addWidget(delete_project)
        layout.addLayout(project_header)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(240)
        self.projects.setAccessibleName("Projects")
        self.projects.itemClicked.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.itemActivated.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.currentItemChanged.connect(self._project_current_changed)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self._show_project_menu)
        self.project_menu = QMenu(self)
        self.project_add_action = self.project_menu.addAction(
            _icon("folder_plus"), "Add project…"
        )
        self.project_add_action.triggered.connect(self.openFolderRequested)
        self.project_menu.addSeparator()
        self.project_remove_action = self.project_menu.addAction(
            _icon("close"), "Remove from Projects (keep folder)"
        )
        self.project_remove_action.triggered.connect(self._remove_selected_project)
        self.project_remove_action.setEnabled(False)
        self.project_delete_action = self.project_menu.addAction(
            _icon("clear"), "Delete folder from machine…"
        )
        self.project_delete_action.triggered.connect(self._delete_selected_project)
        self.project_delete_action.setEnabled(False)
        layout.addWidget(self.projects)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color:#3a3732;")
        layout.addWidget(divider)
        session_header = QHBoxLayout()
        session_label = QLabel("SESSIONS")
        session_label.setObjectName("sectionTitle")
        self.session_label = session_label
        add_session = QToolButton()
        add_session.setObjectName("bareButton")
        add_session.setIcon(_icon("new_task"))
        add_session.setToolTip("Add a saved session")
        add_session.setAccessibleName("Add saved session")
        add_session.clicked.connect(self.addSessionRequested)
        self.add_session_button = add_session
        archive = QToolButton()
        archive.setObjectName("bareButton")
        archive.setIcon(_icon("archive"))
        archive.setToolTip(
            "Remove selected session from this list; keep it in local archive storage"
        )
        archive.setAccessibleName(
            "Remove selected session from Sessions and keep an archived copy"
        )
        archive.clicked.connect(self._archive_selected)
        archive.setEnabled(False)
        self.archive_button = archive
        delete_session = QToolButton()
        delete_session.setObjectName("bareButton")
        delete_session.setIcon(_icon("clear"))
        delete_session.setToolTip(
            "Permanently delete the selected session from this machine"
        )
        delete_session.setAccessibleName(
            "Permanently delete selected session from this machine"
        )
        delete_session.clicked.connect(self._delete_selected_session)
        delete_session.setEnabled(False)
        self.delete_session_button = delete_session
        session_header.addWidget(session_label)
        session_header.addStretch(1)
        session_header.addWidget(add_session)
        session_header.addWidget(archive)
        session_header.addWidget(delete_session)
        layout.addLayout(session_header)
        self.session_filter = QLineEdit()
        self.session_filter.setPlaceholderText("Filter sessions")
        self.session_filter.setAccessibleName("Filter sessions by name")
        self.session_filter.setClearButtonEnabled(True)
        clear_filter = self.session_filter.findChild(QToolButton)
        if clear_filter is not None:
            clear_filter.setAccessibleName("Clear the session filter")
        self.session_filter.textChanged.connect(self._apply_session_filter)
        layout.addWidget(self.session_filter)
        self.sessions = QListWidget()
        self.sessions.setAccessibleName("Saved sessions")
        self.sessions.itemClicked.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.itemActivated.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.currentItemChanged.connect(self._session_current_changed)
        self.sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._show_session_menu)
        self.session_menu = QMenu(self)
        self.session_add_action = self.session_menu.addAction(
            _icon("new_task"), "Add session"
        )
        self.session_add_action.triggered.connect(self.addSessionRequested)
        self.session_menu.addSeparator()
        self.session_rename_action = self.session_menu.addAction(
            _icon("pencil"), "Rename…"
        )
        self.session_rename_action.triggered.connect(self._rename_selected_session)
        self.session_rename_action.setEnabled(False)
        self.session_remove_action = self.session_menu.addAction(
            _icon("archive"), "Remove from Sessions (keep archived copy)"
        )
        self.session_remove_action.triggered.connect(self._archive_selected)
        self.session_remove_action.setEnabled(False)
        self.session_delete_action = self.session_menu.addAction(
            _icon("clear"), "Delete session from machine…"
        )
        self.session_delete_action.triggered.connect(self._delete_selected_session)
        self.session_delete_action.setEnabled(False)
        layout.addWidget(self.sessions, 1)

        export = QPushButton("Export conversation")
        export.setObjectName("railAction")
        export.setIcon(_icon("export"))
        export.setToolTip("Export this conversation as Markdown (Ctrl+Shift+S)")
        export.setAccessibleName("Export conversation as Markdown")
        export.clicked.connect(self.exportRequested)
        export.setEnabled(False)
        self.export_button = export
        models = QPushButton("Models")
        models.setObjectName("railAction")
        models.setIcon(_icon("model"))
        models.setToolTip("Manage local models")
        models.setAccessibleName("Manage local models")
        models.clicked.connect(self.modelsRequested)
        self.models_button = models
        settings = QPushButton("Settings")
        settings.setObjectName("railAction")
        settings.setIcon(_icon("settings"))
        settings.setToolTip("Settings (Ctrl+,)")
        settings.setAccessibleName("Open Onionmind settings")
        settings.clicked.connect(self.settingsRequested)
        self.settings_button = settings
        layout.addWidget(export)
        layout.addWidget(models)
        layout.addWidget(settings)

    def _archive_selected(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.archiveRequested.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))

    def _rename_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is None:
            return
        session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
        current_title = item.text().split("\n", 1)[0]
        name, accepted = QInputDialog.getText(
            self, "Rename session", "Session name", text=current_title
        )
        if accepted and name.strip():
            self.renameSessionRequested.emit(session_id, name.strip())

    def _apply_session_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.sessions.count()):
            item = self.sessions.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _delete_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.deleteSessionRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _remove_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.removeProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _delete_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.deleteProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _project_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.remove_project_button.setEnabled(available)
        self.delete_project_button.setEnabled(available)
        self.project_remove_action.setEnabled(available)
        self.project_delete_action.setEnabled(available)

    def _session_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.archive_button.setEnabled(available)
        self.delete_session_button.setEnabled(available)
        self.session_rename_action.setEnabled(available)
        self.session_remove_action.setEnabled(available)
        self.session_delete_action.setEnabled(available)

    def _show_project_menu(self, position: Any) -> None:
        item = self.projects.itemAt(position)
        if item is not None:
            self.projects.setCurrentItem(item)
        self.project_menu.popup(self.projects.viewport().mapToGlobal(position))

    def _show_session_menu(self, position: Any) -> None:
        item = self.sessions.itemAt(position)
        if item is not None:
            self.sessions.setCurrentItem(item)
        self.session_menu.popup(self.sessions.viewport().mapToGlobal(position))

    def set_conversation_available(self, available: bool) -> None:
        self.export_button.setEnabled(bool(available))

    def add_project(self, path: str, select: bool = True) -> None:
        if not path:
            return
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if _path_key(item.data(Qt.ItemDataRole.UserRole)) == normalized:
                if select:
                    self.projects.setCurrentItem(item)
                return
        name = Path(path).name or path
        item = QListWidgetItem(f"{name}\n{path}")
        item.setIcon(_icon("folder"))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setSizeHint(QSize(100, 47))
        self.projects.insertItem(0, item)
        if select:
            self.projects.setCurrentItem(item)

    def remove_project(self, path: str) -> bool:
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            item_path = _as_text(item.data(Qt.ItemDataRole.UserRole))
            if _path_key(item_path) == normalized:
                self.projects.takeItem(index)
                self.projects.clearSelection()
                self.projects.setCurrentRow(-1)
                return True
        return False

    def clear_projects(self) -> int:
        """Forget every remembered project. The folders themselves are not
        touched - this list is only Onionmind's memory of them."""
        removed = self.projects.count()
        self.projects.clear()
        return removed

    def clear_session_selection(self) -> None:
        self.sessions.clearSelection()
        self.sessions.setCurrentRow(-1)

    def set_session_scope(self, project: Optional[str]) -> None:
        """Name the project whose sessions are listed, so a short list reads as
        scoping rather than loss."""
        name = Path(project).name if project else ""
        self.session_label.setText(f"SESSIONS · {name}" if name else "SESSIONS")
        self.session_label.setToolTip(
            f"Sessions saved in {project}" if project else "Sessions saved with no project open"
        )

    def set_sessions(self, sessions: Iterable[Any], current_id: Optional[str] = None) -> None:
        self.sessions.clear()
        for session in sessions:
            session_id = _as_text(_field(session, "id"))
            title = _as_text(_field(session, "title", "New session"))
            updated = _field(session, "updated_at")
            if isinstance(updated, datetime):
                meta = updated.astimezone().strftime("%b %d, %H:%M")
            else:
                meta = _as_text(updated)[:16].replace("T", " ") or "Saved locally"
            item = QListWidgetItem(f"{title}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            item.setSizeHint(QSize(100, 47))
            self.sessions.addItem(item)
            if session_id == current_id:
                self.sessions.setCurrentItem(item)
        self._apply_session_filter(self.session_filter.text())


class TerminalPane(QFrame):
    closeRequested = Signal()
    stateChanged = Signal(str)

    def __init__(self, desktop_core: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.desktop_core = desktop_core
        self.setObjectName("terminalPane")
        self.setMinimumHeight(135)
        self.setMaximumHeight(200)
        self.workspace = str(Path.cwd())
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel("TERMINAL")
        title.setObjectName("sectionTitle")
        self.scope_label = QLabel(Path(self.workspace).name)
        self.scope_label.setObjectName("meta")
        stop = QToolButton()
        stop.setObjectName("bareButton")
        stop.setIcon(_icon("stop"))
        stop.setToolTip(
            "Stop the shell command; child processes started by it may require manual termination"
        )
        stop.setAccessibleName("Stop terminal command")
        stop.clicked.connect(self.stop)
        stop.setEnabled(False)
        self.stop_button = stop
        clear = QToolButton()
        clear.setObjectName("bareButton")
        clear.setIcon(_icon("clear"))
        clear.setToolTip("Clear terminal")
        clear.setAccessibleName("Clear terminal output")
        clear.clicked.connect(lambda: self.output.clear())
        clear.setEnabled(False)
        self.clear_button = clear
        close = QToolButton()
        close.setObjectName("bareButton")
        close.setIcon(_icon("close"))
        close.setToolTip("Close terminal drawer (Ctrl+`)")
        close.setAccessibleName("Close terminal drawer")
        close.clicked.connect(self.closeRequested)
        self.close_button = close
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.scope_label)
        header.addSpacing(8)
        header.addWidget(stop)
        header.addWidget(clear)
        header.addWidget(close)
        outer.addLayout(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminalOutput")
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Terminal output")
        self.output.document().setMaximumBlockCount(5000)
        self.output.textChanged.connect(
            lambda: self.clear_button.setEnabled(bool(self.output.toPlainText()))
        )
        outer.addWidget(self.output, 1)
        command_row = QHBoxLayout()
        prompt = QLabel(">")
        prompt.setObjectName("accent")
        self.command = QLineEdit()
        self.command.setObjectName("terminalInput")
        shell_name = "PowerShell" if os.name == "nt" else "/bin/sh"
        self.command.setPlaceholderText(f"Run a {shell_name} command in this project")
        self.command.setToolTip(
            f"Commands run through {shell_name} in the active project directory."
        )
        self.command.setAccessibleName("Terminal command")
        self.command.returnPressed.connect(self.run_current)
        self.process.stateChanged.connect(self._sync_process_state)
        command_row.addWidget(prompt)
        command_row.addWidget(self.command, 1)
        outer.addLayout(command_row)

    def set_workspace(self, path: str) -> None:
        self.workspace = path
        self.scope_label.setText(Path(path).name or path)
        self.scope_label.setToolTip(path)

    def append(self, text: str) -> None:
        if not text:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def run_current(self) -> None:
        command = self.command.text().strip()
        if not command:
            return
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stateChanged.emit("A terminal command is already running.")
            return
        self.command.clear()
        self.append(f"\n{Path(self.workspace).name}> {command}\n")
        argv: tuple[str, ...]
        parser = getattr(self.desktop_core, "parse_terminal_command", None) if self.desktop_core else None
        try:
            if callable(parser):
                interpreter = "powershell" if os.name == "nt" else "sh"
                argv = tuple(parser(command, interpreter=interpreter))
            elif os.name == "nt":
                argv = ("powershell", "-NoLogo", "-NoProfile", "-Command", command)
            else:
                argv = ("/bin/sh", "-lc", command)
        except Exception as exc:
            self.append(f"Could not parse command: {exc}\n")
            return
        if not argv:
            self.append("No command to run.\n")
            return
        self.process.setWorkingDirectory(self.workspace)
        self.process.start(argv[0], list(argv[1:]))
        self.stateChanged.emit(f"Running {command}")

    def _sync_process_state(self, state: QProcess.ProcessState) -> None:
        running = state != QProcess.ProcessState.NotRunning
        self.stop_button.setEnabled(running)
        self.command.setEnabled(not running)

    def stop(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop_button.setEnabled(False)
            self.stateChanged.emit("Stopping terminal command…")
            self.process.terminate()
            QTimer.singleShot(1200, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append(ANSI_ESCAPE.sub("", text))

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        label = "finished" if status == QProcess.ExitStatus.NormalExit else "crashed"
        self.append(f"\n[command {label}, exit {exit_code}]\n")
        self.stateChanged.emit(f"Terminal command {label} with exit code {exit_code}.")

    def _error(self, error: QProcess.ProcessError) -> None:
        del error
        self.append(f"\nCould not start command: {self.process.errorString()}\n")
        self.stateChanged.emit("The terminal command could not start.")


class InspectorPane(QWidget):
    refreshRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self.setMinimumWidth(250)
        self.setMaximumWidth(390)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 5, 10, 10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName("Workspace inspector")
        outer.addWidget(self.tabs)

        self.context_tab = QWidget()
        context_layout = QVBoxLayout(self.context_tab)
        context_layout.setContentsMargins(2, 12, 2, 3)
        context_layout.setSpacing(10)
        self.agent_card = QFrame()
        self.agent_card.setObjectName("card")
        agent_layout = QVBoxLayout(self.agent_card)
        agent_layout.setContentsMargins(10, 9, 10, 9)
        self.agent_title = QLabel("Repository guidance")
        self.agent_title.setObjectName("title")
        self.agent_state = QLabel("No AGENTS.md observed")
        self.agent_state.setObjectName("meta")
        self.agent_state.setWordWrap(True)
        agent_layout.addWidget(self.agent_title)
        agent_layout.addWidget(self.agent_state)
        context_layout.addWidget(self.agent_card)

        tree_header = QHBoxLayout()
        files_title = QLabel("PROJECT FILES")
        files_title.setObjectName("sectionTitle")
        self.context_count = QLabel("0 selected")
        self.context_count.setObjectName("meta")
        tree_header.addWidget(files_title)
        tree_header.addStretch(1)
        tree_header.addWidget(self.context_count)
        context_layout.addLayout(tree_header)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAccessibleName("Project files for context")
        self.file_tree.itemChanged.connect(self._context_selection_changed)
        context_layout.addWidget(self.file_tree, 1)

        privacy = QFrame()
        privacy.setObjectName("card")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(10, 9, 10, 9)
        privacy_title = QHBoxLayout()
        p_title = QLabel("Privacy boundary")
        p_title.setObjectName("title")
        self.privacy_state = QLabel("Local inference")
        self.privacy_state.setObjectName("success")
        privacy_title.addWidget(p_title)
        privacy_title.addStretch(1)
        privacy_title.addWidget(self.privacy_state)
        privacy_layout.addLayout(privacy_title)
        privacy_copy = QLabel(
            "Prompts and model inference stay on this machine. Anything that leaves it goes over Tor: Chat search, and the agent, which verifies a circuit before it starts and refuses to run without one."
        )
        privacy_copy.setObjectName("meta")
        privacy_copy.setWordWrap(True)
        privacy_layout.addWidget(privacy_copy)
        context_layout.addWidget(privacy)

        self.changes_tab = QWidget()
        changes_layout = QVBoxLayout(self.changes_tab)
        changes_layout.setContentsMargins(2, 10, 2, 3)
        changes_header = QHBoxLayout()
        self.change_summary = QLabel("No observed changes")
        self.change_summary.setObjectName("title")
        refresh = QToolButton()
        refresh.setObjectName("bareButton")
        refresh.setIcon(_icon("refresh"))
        refresh.setToolTip("Refresh observed Git changes")
        refresh.setAccessibleName("Refresh Git changes")
        refresh.clicked.connect(self.refreshRequested)
        refresh.setEnabled(False)
        self.refresh_button = refresh
        changes_header.addWidget(self.change_summary)
        changes_header.addStretch(1)
        changes_header.addWidget(refresh)
        changes_layout.addLayout(changes_header)
        self.change_list = QListWidget()
        self.change_list.setMaximumHeight(175)
        self.change_list.setAccessibleName("Observed Git changes")
        changes_layout.addWidget(self.change_list)
        diff_title = QLabel("DIFF")
        diff_title.setObjectName("sectionTitle")
        changes_layout.addWidget(diff_title)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("diffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setAccessibleName("Git diff")
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        changes_layout.addWidget(self.diff_view, 1)

        self.activity_tab = QWidget()
        activity_layout = QVBoxLayout(self.activity_tab)
        activity_layout.setContentsMargins(2, 10, 2, 3)
        activity_copy = QLabel("Observed application events. Agent output is not treated as proof of a file change.")
        activity_copy.setObjectName("meta")
        activity_copy.setWordWrap(True)
        activity_layout.addWidget(activity_copy)
        self.activity = QListWidget()
        self.activity.setAccessibleName("Activity history")
        activity_layout.addWidget(self.activity, 1)

        self.tabs.addTab(self.context_tab, "Context")
        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.activity_tab, "Activity")

    def append_activity(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M")
        self.activity.insertItem(0, QListWidgetItem(f"{stamp}  {_brand_runtime_text(text)}"))
        while self.activity.count() > 150:
            self.activity.takeItem(self.activity.count() - 1)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.refresh_button.setEnabled(bool(snapshot.get("root")))
        agents = snapshot.get("agents_files") or []
        if agents:
            self.agent_title.setText(Path(agents[0]).name)
            self.agent_state.setText(f"Loaded from {agents[0]}" + (f" and {len(agents) - 1} more" if len(agents) > 1 else ""))
        else:
            self.agent_title.setText("Repository guidance")
            self.agent_state.setText("No AGENTS.md observed in the inspected tree")
        self.populate_tree(snapshot.get("file_tree") or [], bool(snapshot.get("tree_truncated")))
        changes = snapshot.get("changes") or []
        self.change_list.clear()
        for change in changes:
            status = _as_text(change.get("status", "?"))
            path = _as_text(change.get("path", ""))
            original = change.get("original_path")
            text = f"{status:>2}  {path}" + (f"  from {original}" if original else "")
            self.change_list.addItem(QListWidgetItem(text))
        if changes:
            self.change_summary.setText(snapshot.get("summary") or f"{len(changes)} observed change(s)")
        else:
            self.change_summary.setText("Working tree clean" if snapshot.get("is_git") else "Not a Git repository")
        self.diff_view.setPlainText(snapshot.get("diff") or "No diff to display.")

    def populate_tree(self, paths: list[str], truncated: bool = False) -> None:
        self.file_tree.blockSignals(True)
        self.file_tree.clear()
        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for raw in paths:
            slash_path = raw.replace("\\", "/")
            directory_entry = slash_path.endswith("/")
            normalized = slash_path.strip("/")
            if not normalized or normalized == ".":
                continue
            parts = tuple(part for part in normalized.split("/") if part)
            parent: Optional[QTreeWidgetItem] = None
            for index, part in enumerate(parts):
                key = parts[: index + 1]
                node = nodes.get(key)
                if node is None:
                    node = QTreeWidgetItem(parent if parent is not None else self.file_tree, [part])
                    nodes[key] = node
                    if index == len(parts) - 1 and not directory_entry:
                        node.setData(0, Qt.ItemDataRole.UserRole, normalized)
                        node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        node.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        node.setIcon(0, _icon("folder"))
                parent = node
        self.file_tree.blockSignals(False)
        self.file_tree.expandToDepth(0)
        self.context_count.setText("0 selected" + (" · tree limited" if truncated else ""))

    def selected_context_files(self) -> list[str]:
        selected: list[str] = []
        iterator = self.file_tree.invisibleRootItem()

        def walk(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                child = parent.child(index)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path and child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(_as_text(path))
                walk(child)

        walk(iterator)
        return selected

    def _context_selection_changed(self, item: QTreeWidgetItem, column: int) -> None:
        del item, column
        count = len(self.selected_context_files())
        self.context_count.setText(f"{count} selected")


class ModelManagerDialog(QDialog):
    pullRequested = Signal(str)
    catalogRequested = Signal()

    def __init__(
        self,
        models: list[str],
        current: str,
        label_for_model: Optional[Callable[[str], str]] = None,
        parent: Optional[QWidget] = None,
        reference_normalizer: Optional[Callable[[str], str]] = None,
        marker_for: Optional[Callable[[str], Optional[str]]] = None,
        catalog_entry_label: Optional[Callable[[Any], str]] = None,
    ) -> None:
        super().__init__(parent)
        self._label_for_model = label_for_model or (lambda value: "ONIONMIND MODEL")
        self._normalize = reference_normalizer or (lambda value: value)
        self._marker_for = marker_for
        self._catalog_entry_label = catalog_entry_label or (
            lambda entry: _as_text(_field(entry, "id"))
        )
        self.setWindowTitle("Onionmind models")
        self.setModal(True)
        self.resize(560, 640)
        layout = QVBoxLayout(self)
        heading = QLabel("Onionmind models")
        heading.setObjectName("brand")
        copy_label = QLabel(
            "Paste an Onionmind name (BLAZE), an Ollama name (llama3.2:3b), or a "
            "huggingface.co model link. Pulling asks the local model service to "
            "download directly; that download is not Tor-routed. The popular list "
            "is fetched over Tor and ordered by what this machine can run."
        )
        copy_label.setObjectName("meta")
        copy_label.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy_label)
        installed_label = QLabel("INSTALLED")
        installed_label.setObjectName("sectionTitle")
        layout.addWidget(installed_label)
        self.models = QListWidget()
        self.models.setAccessibleName("Installed local models")
        layout.addWidget(self.models, 1)
        self.set_models(models, current)

        popular_label = QLabel("POPULAR")
        popular_label.setObjectName("sectionTitle")
        layout.addWidget(popular_label)
        browse_row = QHBoxLayout()
        self.browse_button = QPushButton("Browse popular models")
        self.browse_button.setAccessibleName("Fetch the popular model list over Tor")
        self.browse_button.setToolTip(
            "Fetches the most-downloaded GGUF models from huggingface.co through "
            "the verified Tor circuit - huggingface.co sees a Tor exit, not this "
            "machine. Needs Tor up; never falls back to a direct connection."
        )
        self.browse_button.clicked.connect(self._request_catalog)
        self.catalog_status = QLabel(
            "Not fetched yet - the list is checked live every time you browse"
        )
        self.catalog_status.setObjectName("meta")
        self.catalog_status.setWordWrap(True)
        self.catalog_add = QPushButton("Add selected")
        self.catalog_add.setAccessibleName("Add the selected popular model")
        self.catalog_add.setEnabled(False)
        self.catalog_add.clicked.connect(self._request_pull)
        browse_row.addWidget(self.browse_button)
        browse_row.addStretch(1)
        browse_row.addWidget(self.catalog_add)
        layout.addLayout(browse_row)
        layout.addWidget(self.catalog_status)
        self.catalog_list = QListWidget()
        self.catalog_list.setAccessibleName("Popular models fetched from huggingface.co")
        self.catalog_list.setToolTip(
            "Selecting a model fills the add field with its hf.co reference; "
            "Add selected downloads it"
        )
        self.catalog_list.setWordWrap(True)
        self.catalog_list.currentItemChanged.connect(self._catalog_picked)
        self.catalog_list.itemDoubleClicked.connect(lambda item: self._request_pull())
        layout.addWidget(self.catalog_list, 1)

        row = QHBoxLayout()
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("BLAZE, llama3.2:3b, user/model-gguf, or a huggingface.co link")
        self.model_name.setAccessibleName("Onionmind model to add")
        self.pull_button = QPushButton("Add model")
        self.pull_button.setObjectName("primaryButton")
        self.pull_button.setAccessibleName("Add Onionmind model")
        self.pull_button.clicked.connect(self._request_pull)
        self.pull_button.setEnabled(False)
        self.model_name.textChanged.connect(self._sync_pull_button)
        self.model_name.textChanged.connect(self._sync_reference_hint)
        self.model_name.returnPressed.connect(self._request_pull)
        row.addWidget(self.model_name, 1)
        row.addWidget(self.pull_button)
        layout.addLayout(row)
        self.reference_hint = QLabel()
        self.reference_hint.setObjectName("meta")
        self.reference_hint.setWordWrap(True)
        layout.addWidget(self.reference_hint)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        layout.addWidget(self.progress)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setAccessibleName("Close Onionmind model manager")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_models(self, models: list[str], current: str = "") -> None:
        self.models.clear()
        counts: dict[str, int] = {}
        for model in models:
            base = self._label_for_model(model)
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} {counts[base]}"
            item = QListWidgetItem(label + ("  · selected" if model == current else ""))
            item.setData(Qt.ItemDataRole.UserRole, model)
            self.models.addItem(item)

    def _sync_pull_button(self) -> None:
        self.pull_button.setEnabled(bool(self.model_name.text().strip()))

    def _sync_reference_hint(self) -> None:
        """Say what will actually be pulled, and flag refusal-removed names."""
        raw = self.model_name.text().strip()
        if not raw:
            self.reference_hint.setText("")
            return
        normalized = self._normalize(raw)
        parts = [f"Pulls as: {normalized}"] if normalized != raw else []
        if self._marker_for is not None:
            marker = self._marker_for(raw)
            if marker:
                parts.append(
                    f"Flagged by name as refusal-removed ({marker}) - verify before relying on it"
                )
        self.reference_hint.setText(" · ".join(parts))

    def _request_catalog(self) -> None:
        # The click is the action: every browse re-fetches the live list
        # through the same verified Tor circuit as search and updates, never
        # directly and never from a stored copy.
        self.browse_button.setEnabled(False)
        self.catalog_add.setEnabled(False)
        self.catalog_list.clear()
        self.catalog_status.setText("Fetching the current list over Tor…")
        self.catalogRequested.emit()

    def _catalog_picked(self, current: Any = None, previous: Any = None) -> None:
        del previous
        entry = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.catalog_add.setEnabled(entry is not None)
        if entry is None:
            return
        model_id = _as_text(_field(entry, "id"))
        if model_id:
            self.model_name.setText(f"hf.co/{model_id}")

    @staticmethod
    def _catalog_flag(entry: Any) -> str:
        """State the refusal-removal screen on every row, here rather than in
        an injected labeller, so no caller can drop it."""
        marker = _as_text(_field(entry, "uncensored"))
        if marker:
            return (
                f"UNCENSORED / ABLITERATED - flagged by name ({marker}); "
                "its refusals have been removed"
            )
        return "No refusal-removal flag in its name (not a guarantee)"

    def set_catalog(self, entries: Iterable[Any]) -> None:
        self.browse_button.setEnabled(True)
        self.browse_button.setText("Refresh popular models")
        self.catalog_list.clear()
        rows = list(entries)
        if not rows:
            self.catalog_status.setText("No models came back")
            return
        for entry in rows:
            text = f"{self._catalog_entry_label(entry)}\n{self._catalog_flag(entry)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(text)
            self.catalog_list.addItem(item)
        self.catalog_status.setText(
            f"{len(rows)} models · checked over Tor at {time.strftime('%H:%M')}"
        )

    def set_catalog_error(self, message: str) -> None:
        self.browse_button.setEnabled(True)
        self.catalog_add.setEnabled(False)
        self.catalog_list.clear()
        self.catalog_status.setText("Could not fetch the list")
        self.reference_hint.setText(_brand_runtime_text(message)[:120])

    def _request_pull(self) -> None:
        raw = self.model_name.text().strip()
        if not raw:
            self.progress.setFormat("Enter a model name or link")
            return
        name = self._normalize(raw)
        answer = QMessageBox.warning(
            self,
            "Direct model download",
            "The local model service will download this model directly from its "
            "configured registry. This is not Tor-routed and exposes this machine's "
            "network address. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.progress.setFormat("Download cancelled")
            return
        self.pull_button.setEnabled(False)
        self.model_name.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Starting…")
        self.pullRequested.emit(name)

    def set_progress(self, fraction: float, status: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, round(fraction * 100))))
        self.progress.setFormat(f"{_brand_runtime_text(status)} · %p%")

    def set_error(self, message: str) -> None:
        self.model_name.setEnabled(True)
        self._sync_pull_button()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat(_brand_runtime_text(message)[:80])

    def set_complete(self) -> None:
        self.model_name.setEnabled(True)
        self.model_name.clear()
        self.progress.setValue(100)
        self.progress.setFormat("Installed locally")


class SettingsDialog(QDialog):
    def __init__(
        self,
        data_root: Path,
        agent_limitation: str,
        update_bridge: Optional[UpdateBridge] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.update_bridge = update_bridge
        self.update_manifest: Any = None
        self.update_staging: Optional[str] = None
        self._update_stop: Optional[threading.Event] = None
        self.setWindowTitle("Onionmind settings")
        # One tab per concern, and each tab scrolls: a small laptop panel
        # cannot show the whole of any of them, so the size clamps to the
        # available geometry instead of pushing the Close row out of reach.
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(
            min(560, available.width() - 40) if available else 560,
            min(660, available.height() - 80) if available else 660,
        )
        preferences = dict(getattr(self._window(), "preferences", {}) or {})
        tabs = QTabWidget(self)
        tabs.setAccessibleName("Onionmind settings sections")
        tabs.addTab(self._page(self._general_page(preferences)), "General")
        tabs.addTab(self._page(self._privacy_page(preferences, agent_limitation)), "Privacy")
        tabs.addTab(self._page(self._updates_page(update_bridge)), "Updates")
        tabs.addTab(self._page(self._storage_page(data_root)), "Storage")

        self.storage_feedback = QLabel()
        self.storage_feedback.setObjectName("meta")
        self.storage_feedback.setWordWrap(True)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_folder = actions.addButton("Open storage folder", QDialogButtonBox.ButtonRole.ActionRole)
        open_folder.setAccessibleName("Open Onionmind storage folder")
        open_folder.clicked.connect(self._open_storage_folder)
        actions.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addWidget(tabs, 1)
        root.addWidget(self.storage_feedback)
        root.addWidget(actions)

        if update_bridge is None or not update_bridge.available:
            self.check_updates_button.setEnabled(False)
            self.update_feedback.setText("")
        pending = update_bridge.pending() if update_bridge and update_bridge.available else None
        if pending is not None:
            self._offer_restart(str(pending))

    @staticmethod
    def _page(content: QWidget) -> QScrollArea:
        """Each tab scrolls on its own, so a short laptop panel never pushes
        the Close row off the screen."""
        scroll = QScrollArea()
        # Named so the sheet can keep the page transparent: a bare QWidget in a
        # scroll area takes the platform's own background, which on a dark app
        # is a white slab.
        scroll.setObjectName("settingsPage")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _general_page(self, preferences: dict[str, Any]) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Workbench")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)

        self.text_size_combo = QComboBox()
        self.text_size_combo.setAccessibleName("Workbench text size")
        for label, value in (
            ("System default", "system"),
            ("Compact", "compact"),
            ("Comfortable", "comfortable"),
        ):
            self.text_size_combo.addItem(label, value)
        self.text_size_combo.setCurrentIndex(
            max(0, self.text_size_combo.findData(_as_text(preferences.get("text_scale", "system"))))
        )
        form.addRow("Text size", self.text_size_combo)

        self.enter_sends_box = QCheckBox("Enter sends the message")
        self.enter_sends_box.setToolTip(
            "On: Enter sends and Shift+Enter makes a newline. "
            "Off: Ctrl+Enter sends and Enter makes a newline."
        )
        self.enter_sends_box.setAccessibleName(
            "Enter sends the message; when off, Ctrl+Enter sends"
        )
        self.enter_sends_box.setChecked(bool(preferences.get("enter_sends", True)))
        form.addRow("Composer", self.enter_sends_box)

        self.startup_combo = QComboBox()
        self.startup_combo.setAccessibleName("Composer mode to start in")
        self.startup_combo.setToolTip("Applies the next time Onionmind opens.")
        for label, value in (
            ("Remember last", "remember"),
            ("Chat", "chat"),
            ("Agent", "agent"),
        ):
            self.startup_combo.addItem(label, value)
        self.startup_combo.setCurrentIndex(
            max(0, self.startup_combo.findData(_as_text(preferences.get("startup_mode", "remember"))))
        )
        form.addRow("Start in", self.startup_combo)

        self.terminal_box = QCheckBox("Show the terminal drawer on launch")
        self.terminal_box.setChecked(bool(preferences.get("show_terminal_on_launch", False)))
        form.addRow("Terminal", self.terminal_box)

        self.motion_combo = QComboBox()
        self.motion_combo.setAccessibleName("Workbench animation")
        self.motion_combo.setToolTip(
            "Reduced keeps pending states static; Follow the system honours the "
            "operating system's animation setting."
        )
        for label, value in (
            ("Follow the system", "system"),
            ("Reduced", "reduced"),
            ("Full", "full"),
        ):
            self.motion_combo.addItem(label, value)
        self.motion_combo.setCurrentIndex(
            max(0, self.motion_combo.findData(_as_text(preferences.get("reduce_motion", "system"))))
        )
        form.addRow("Animation", self.motion_combo)

        self.context_combo = QComboBox()
        self.context_combo.setAccessibleName("Chat context window in tokens")
        self.context_combo.setToolTip(
            "How much conversation the local model keeps in mind in Chat. Pick a "
            "preset or type any token count - 24000 and 24k both work. Bigger "
            "remembers more and costs more memory on this machine; it applies to "
            "the next turn. The agent has its own budget - the toolbar slider."
        )
        # Editable: the presets are stops worth one click, not a fence. Anything
        # that parses as a positive count is accepted and only warned about.
        self.context_combo.setEditable(True)
        self.context_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        presets = getattr(self.desktop_core(), "CONTEXT_WINDOW_PRESETS", None) or (
            4096, 8192, 16384, 32768, 65536, 131072
        )
        for value in presets:
            self.context_combo.addItem(str(value), value)
        self.context_combo.setCurrentText(
            str(self._context_tokens(preferences.get("context_window", 16384)))
        )
        form.addRow("Chat context", self.context_combo)
        self.context_hint = QLabel()
        self.context_hint.setObjectName("meta")
        self.context_hint.setWordWrap(True)
        form.addRow("", self.context_hint)
        self._describe_context(self.context_combo.currentText())
        outer.addLayout(form)

        # States are set above; wiring afterwards keeps the constructor's
        # initial values from firing preference writes of their own.
        self.text_size_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("text_scale", self.text_size_combo.currentData())
        )
        self.enter_sends_box.toggled.connect(
            lambda checked: self._set_preference("enter_sends", checked)
        )
        self.startup_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("startup_mode", self.startup_combo.currentData())
        )
        self.terminal_box.toggled.connect(
            lambda checked: self._set_preference("show_terminal_on_launch", checked)
        )
        self.motion_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("reduce_motion", self.motion_combo.currentData())
        )
        # Typing only previews; the value is stored when the field is left or a
        # preset is chosen, so a half-typed "2" never becomes the setting.
        self.context_combo.currentTextChanged.connect(self._describe_context)
        self.context_combo.activated.connect(lambda _index: self._commit_context())
        line = self.context_combo.lineEdit()
        if line is not None:
            line.editingFinished.connect(self._commit_context)

        window_heading = QLabel("Window")
        window_heading.setObjectName("brand")
        outer.addWidget(window_heading)
        window_row = QHBoxLayout()
        window_note = QLabel(
            "Window size, pane widths, and pane visibility are remembered between launches."
        )
        window_note.setWordWrap(True)
        window_row.addWidget(window_note, 1)
        self.reset_layout_button = QPushButton("Reset window layout")
        self.reset_layout_button.setAccessibleName("Reset the remembered Onionmind window layout")
        self.reset_layout_button.clicked.connect(self._reset_window_layout)
        window_row.addWidget(self.reset_layout_button)
        outer.addLayout(window_row)
        self.window_feedback = QLabel()
        self.window_feedback.setObjectName("meta")
        self.window_feedback.setWordWrap(True)
        outer.addWidget(self.window_feedback)
        outer.addStretch(1)
        return page

    def _privacy_page(self, preferences: dict[str, Any], agent_limitation: str) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Boundaries")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.addRow("Inference", QLabel("Onionmind inference on this machine"))
        tor = QLabel(
            "Chat search and the coding agent both leave over Tor; a failed Tor "
            "check never falls back to a direct request."
        )
        tor.setWordWrap(True)
        form.addRow("Tor", tor)
        agent = QLabel(_brand_runtime_text(agent_limitation))
        agent.setWordWrap(True)
        form.addRow("Agent", agent)
        form.addRow("Telemetry", QLabel("No Onionmind telemetry or account"))
        outer.addLayout(form)

        on_disk_heading = QLabel("What is written to disk")
        on_disk_heading.setObjectName("brand")
        outer.addWidget(on_disk_heading)
        self.save_history_box = QCheckBox("Save conversations to this machine")
        self.save_history_box.setAccessibleName("Save conversations to this machine")
        self.save_history_box.setToolTip(
            "Off: conversations stay in memory only and are gone when the window "
            "closes. Sessions already saved are kept until you delete them."
        )
        self.save_history_box.setChecked(bool(preferences.get("save_history", True)))
        outer.addWidget(self.save_history_box)
        self.draft_box = QCheckBox("Remember an unsent message between launches")
        self.draft_box.setAccessibleName("Remember an unsent composer draft")
        self.draft_box.setToolTip(
            "Off: whatever is typed but not sent is dropped when Onionmind closes."
        )
        self.draft_box.setChecked(bool(preferences.get("remember_drafts", True)))
        outer.addWidget(self.draft_box)
        self.save_history_box.toggled.connect(
            lambda checked: self._set_preference("save_history", checked)
        )
        self.draft_box.toggled.connect(
            lambda checked: self._set_preference("remember_drafts", checked)
        )
        outer.addStretch(1)
        return page

    def _updates_page(self, update_bridge: Optional[UpdateBridge]) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        updates_heading = QLabel("Updates")
        updates_heading.setObjectName("brand")
        outer.addWidget(updates_heading)
        updates_row = QHBoxLayout()
        version = QLabel(
            update_bridge.revision_label() if update_bridge and update_bridge.available
            else "Development copy — the updater applies to an installed Onionmind bundle"
        )
        version.setWordWrap(True)
        updates_row.addWidget(version, 1)
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.setAccessibleName("Check for Onionmind updates over Tor")
        self.check_updates_button.clicked.connect(self._check_updates)
        updates_row.addWidget(self.check_updates_button)
        outer.addLayout(updates_row)
        boundary = QLabel("The check and the download both travel over Tor; there is no direct-network fallback.")
        boundary.setObjectName("meta")
        boundary.setWordWrap(True)
        outer.addWidget(boundary)
        self.autocheck_box = QCheckBox("Check automatically over Tor (at most every 12 hours)")
        self.autocheck_box.setAccessibleName("Permission for automatic Onionmind update checks over Tor")
        self.autocheck_box.setToolTip(
            "Off by default. While off, the updater contacts nothing until you press "
            "Check for updates; while on, Onionmind looks for updates over Tor for as "
            "long as it stays open."
        )
        window = self._window()
        if window is not None:
            self.autocheck_box.setChecked(window.update_permission_enabled())
        self.autocheck_box.toggled.connect(self._permission_toggled)
        outer.addWidget(self.autocheck_box)
        self.update_feedback = QLabel()
        self.update_feedback.setObjectName("meta")
        self.update_feedback.setWordWrap(True)
        outer.addWidget(self.update_feedback)
        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedHeight(6)
        self.update_progress.hide()
        outer.addWidget(self.update_progress)
        outer.addStretch(1)
        return page

    def _storage_page(self, data_root: Path) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Storage")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        storage = QLabel(str(data_root))
        storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage.setWordWrap(True)
        form.addRow("Session storage", storage)
        outer.addLayout(form)
        note = QLabel(
            "Conversations, settings, and the saved window layout live in that "
            "folder and nowhere else."
        )
        note.setObjectName("meta")
        note.setWordWrap(True)
        outer.addWidget(note)
        wipe_row = QHBoxLayout()
        wipe_note = QLabel("Remove every saved and archived conversation from this machine.")
        wipe_note.setWordWrap(True)
        wipe_row.addWidget(wipe_note, 1)
        self.wipe_button = QPushButton("Delete all conversations")
        self.wipe_button.setAccessibleName("Delete every saved Onionmind conversation")
        self.wipe_button.clicked.connect(self._delete_all_sessions)
        wipe_row.addWidget(self.wipe_button)
        outer.addLayout(wipe_row)

        wipe_heading = QLabel("Wipe this machine")
        wipe_heading.setObjectName("brand")
        outer.addWidget(wipe_heading)
        clear_row = QHBoxLayout()
        clear_note = QLabel(
            "Destroys your work on this machine: every conversation, the project "
            "list, and the project folders with every file inside them, plus the "
            "last opened project, any unsent message, the agent's network log, and "
            "Tor's state, caches and logs. Files are overwritten before they are "
            "removed. This cannot be undone."
        )
        clear_note.setWordWrap(True)
        clear_row.addWidget(clear_note, 1)
        self.clear_button = QPushButton("Wipe everything")
        self.clear_button.setAccessibleName(
            "Wipe sessions, projects and their files from this machine"
        )
        self.clear_button.clicked.connect(self._wipe_machine_data)
        clear_row.addWidget(self.clear_button)
        outer.addLayout(clear_row)
        self.clear_on_exit_box = QCheckBox(
            "Wipe automatically every time Onionmind closes"
        )
        self.clear_on_exit_box.setAccessibleName(
            "Wipe sessions, projects and their files automatically when Onionmind closes"
        )
        self.clear_on_exit_box.setToolTip(
            "On: every close destroys the conversations, the project list, and the "
            "project folders themselves - without asking again. Off: nothing is "
            "removed until you press the button above."
        )
        window = self._window()
        self.clear_on_exit_box.setChecked(
            bool(getattr(window, "preferences", {}).get("clear_on_exit", False))
            if window is not None
            else False
        )
        self.clear_on_exit_box.toggled.connect(self._clear_on_exit_toggled)
        outer.addWidget(self.clear_on_exit_box)
        limit = QLabel(
            "Overwriting is best effort: on an SSD or a copy-on-write filesystem "
            "the old blocks can survive. Full-disk encryption, or the Matchstick "
            "RAM-only image, is the guarantee."
        )
        limit.setObjectName("meta")
        limit.setWordWrap(True)
        outer.addWidget(limit)
        outer.addStretch(1)
        return page

    _WIPE_DETAIL = (
        "Removes every saved and archived conversation, the remembered projects, "
        "and each project folder with all of its files - overwritten first, then "
        "deleted. The last opened project, any unsent message, and the agent's "
        "network log go too, along with Tor's state file, cached consensus and "
        "descriptors, and any Tor log on this machine.\n\n"
        "A drive root, your home folder, Onionmind's own data or program folder, "
        "and linked folders are skipped.\n\n"
        "Overwriting is best effort: on an SSD or a copy-on-write filesystem the "
        "original blocks can survive."
    )

    def _wipe_machine_data(self) -> None:
        window = self._window()
        if window is None:
            self.storage_feedback.setText(
                "The workbench window is unavailable; nothing was wiped."
            )
            return
        if not window._confirm_permanent_deletion(
            title="Wipe everything from this machine",
            text="Permanently destroy every conversation and every project folder?",
            detail=self._WIPE_DETAIL,
            confirm_label="Wipe everything",
        ):
            self.storage_feedback.setText("Nothing was wiped.")
            return
        result = window.wipe_machine_data()
        skipped = result.get("skipped", 0)
        self.storage_feedback.setText(
            f"Wiped {result.get('sessions', 0)} conversation(s), "
            f"{result.get('projects', 0)} project folder(s) and "
            f"{result.get('tor', 0)} Tor state file(s) from this machine."
            + (f" {skipped} protected path(s) were skipped." if skipped else "")
        )

    def _clear_on_exit_toggled(self, checked: bool) -> None:
        """Arming an unattended folder-destroying wipe is itself confirmed once;
        after that every close runs it without asking, which is the point."""
        window = self._window()
        if checked and window is not None:
            if not window._confirm_permanent_deletion(
                title="Wipe on every close",
                text="Destroy every conversation and every project folder each time Onionmind closes?",
                detail=self._WIPE_DETAIL,
                confirm_label="Wipe on close",
            ):
                self.clear_on_exit_box.setChecked(False)
                self.storage_feedback.setText("Automatic wipe stays off.")
                return
        self._set_preference("clear_on_exit", checked)
        self.storage_feedback.setText(
            "Every close will wipe conversations and project folders."
            if checked
            else "Automatic wipe is off."
        )

    def _delete_all_sessions(self) -> None:
        window = self._window()
        if window is None:
            self.storage_feedback.setText(
                "The workbench window is unavailable; nothing was deleted."
            )
            return
        removed = window.delete_all_sessions()
        self.storage_feedback.setText(
            f"Deleted {removed} saved conversation(s) from this machine."
            if removed
            else "There were no saved conversations to delete."
        )


    def _window(self) -> Any:
        parent = self.parent()
        return parent if isinstance(parent, OnionmindWindow) else None

    def desktop_core(self) -> Any:
        window = self._window()
        return getattr(window, "desktop_core", None)

    def _context_tokens(self, value: Any) -> int:
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        parsed = parser(value) if callable(parser) else None
        if parsed is None:
            try:
                parsed = int(_as_text(value).strip() or 0)
            except ValueError:
                parsed = 0
        return parsed if parsed and parsed > 0 else 16384

    def _describe_context(self, text: str) -> None:
        """Say what the typed value means, and warn when it is out of range -
        the field itself refuses nothing."""
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        tokens = parser(text) if callable(parser) else self._context_tokens(text)
        if tokens is None:
            self.context_hint.setStyleSheet("color: #d88675;")
            self.context_hint.setText("Enter a token count, for example 24000 or 24k.")
            return
        warner = _optional_callable(self.desktop_core(), "context_window_warning")
        warning = warner(tokens) if callable(warner) else ""
        self.context_hint.setStyleSheet("color: #d88675;" if warning else "")
        self.context_hint.setText(
            warning or f"{tokens:,} tokens · type any count, or pick a preset from the list"
        )

    def _commit_context(self) -> None:
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        tokens = parser(self.context_combo.currentText()) if callable(parser) else None
        if tokens is None:
            self._describe_context(self.context_combo.currentText())
            return
        self._set_preference("context_window", tokens)
        self.context_combo.setCurrentText(str(tokens))
        self._describe_context(str(tokens))

    def _set_preference(self, key: str, value: Any) -> None:
        window = self._window()
        if window is not None:
            window.set_preference(key, value)

    def _reset_window_layout(self) -> None:
        window = self._window()
        if window is None:
            self.window_feedback.setText(
                "The workbench window is unavailable; the layout was not reset."
            )
            return
        window.reset_window_layout()
        self.window_feedback.setText(
            "Window layout reset: size, panes, and pane widths are back to the default workbench."
        )

    def _permission_toggled(self, checked: bool) -> None:
        window = self._window()
        if window is None:
            return
        window.set_update_permission(checked)
        if checked:
            self.update_feedback.setText(
                "Automatic checks are on: Onionmind will look for updates over Tor "
                "while it runs - never over a direct connection."
            )
        else:
            self.update_feedback.setText(
                "Automatic checks are off: nothing is contacted until you press Check for updates."
            )

    def _check_updates(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        if bridge is None or not bridge.available or window is None:
            return
        # The pill can read "Running" on a Tor that has never been verified as
        # Tor - a listening SOCKS port is not proof. The check needs a verified
        # circuit, so ask for one here rather than refusing and pointing the
        # user at a control that no longer exists.
        probe = getattr(window.core, "tor_proxy_port", None)
        listening = probe() if callable(probe) else None
        if not listening and bridge.tor_port() is None:
            self.update_feedback.setText(
                "Tor is not up. Allow Tor search on a chat turn to start it, then check "
                "again - updates never use a direct connection."
            )
            return
        self.check_updates_button.setEnabled(False)
        self.update_feedback.setText("Checking for updates through Tor…")

        def check_job(signals: WorkerSignals) -> Any:
            del signals
            if bridge.tor_port() is None:
                verify = getattr(window.core, "tor_check", None)
                if not callable(verify):
                    raise RuntimeError("This Onionmind core cannot verify a Tor circuit.")
                try:
                    verify()
                except SystemExit as exc:
                    # tor_check() exits the process on the CLI; in the desktop
                    # app that would kill a worker thread without a word.
                    raise RuntimeError(_as_text(exc) or "Tor could not be verified.") from None
                if bridge.tor_port() is None:
                    raise RuntimeError(
                        "The local proxy did not verify as Tor; refusing a direct update check."
                    )
            return bridge.check()

        def wire_check(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._update_check_done)
            worker.signals.error.connect(self._update_check_failed)

        window._start_worker(check_job, wire_check)

    def _update_check_done(self, manifest: Any) -> None:
        self.check_updates_button.setEnabled(True)
        bridge = self.update_bridge
        window = self._window()
        if window is not None:
            window.note_update_check(manifest)
        self.update_manifest = manifest
        state = bridge.desktop_core.update_state(bridge.revision(), manifest)
        short = bridge.desktop_core.short_revision(manifest.revision)
        if state == "available":
            self.update_feedback.setText(
                f"Version {manifest.version} (revision {short}) is available. "
                "The download runs through Tor and is verified against its SHA-256."
            )
            self._reveal_download()
        elif state == "current":
            self.update_feedback.setText(f"Onionmind is up to date (revision {short}).")
        else:
            self.update_feedback.setText("The feed could not be compared with this installation.")

    def _update_check_failed(self, message: str) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_feedback.setText(message)

    def _reveal_download(self) -> None:
        box = self.findChild(QDialogButtonBox)
        if box is None or getattr(self, "_download_button", None) is not None:
            return
        self._download_button = box.addButton(
            "Download and install", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._download_button.setAccessibleName("Download the Onionmind update over Tor")
        self._download_button.clicked.connect(self._download_update)

    def _download_update(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        manifest = self.update_manifest
        if bridge is None or window is None or manifest is None:
            return
        self._download_button.setEnabled(False)
        self.check_updates_button.setEnabled(False)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.update_feedback.setText("Downloading the update through Tor…")
        self._update_stop = threading.Event()
        manifest_ref = manifest

        def download_job(signals: WorkerSignals) -> str:
            def progress(fraction: Optional[float], note: str) -> None:
                if fraction is None:
                    signals.text.emit(note)
                else:
                    signals.progress.emit(float(fraction), note)

            return str(bridge.download(manifest_ref, progress, self._update_stop))

        worker = window._start_worker(download_job)
        worker.signals.progress.connect(self._update_download_progress)
        worker.signals.text.connect(self._update_download_note)
        worker.signals.result.connect(self._update_download_done)
        worker.signals.error.connect(self._update_download_failed)

    def _update_download_progress(self, fraction: float, note: str) -> None:
        self.update_progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.update_feedback.setText(note)

    def _update_download_note(self, note: str) -> None:
        self.update_feedback.setText(note)

    def _update_download_done(self, staging: str) -> None:
        self.update_progress.hide()
        self.update_staging = staging
        self._offer_restart(staging)
        window = self._window()
        if window is not None:
            window.show_update_ready()

    def _update_download_failed(self, message: str) -> None:
        self.update_progress.hide()
        self.update_feedback.setText(message)
        if getattr(self, "_download_button", None) is not None:
            self._download_button.setEnabled(True)
        self.check_updates_button.setEnabled(True)

    def _offer_restart(self, staging: str) -> None:
        self.update_staging = staging
        self.update_feedback.setText(
            "The update is downloaded, verified, and staged. Restart Onionmind to finish installing it."
        )
        box = self.findChild(QDialogButtonBox)
        if box is None or getattr(self, "_restart_button", None) is not None:
            return
        self._restart_button = box.addButton(
            "Restart and update", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._restart_button.setAccessibleName("Restart Onionmind to install the update")
        self._restart_button.clicked.connect(self._restart_for_update)

    def _restart_for_update(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        if bridge is None or window is None or not self.update_staging:
            return
        window.restart_for_update(self.update_staging)

    def _open_storage_folder(self) -> None:
        try:
            self.data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.storage_feedback.setText(f"Could not open storage: {exc}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_root)))
        self.storage_feedback.setText(
            "Opened Onionmind storage." if opened else "The system could not open the storage folder."
        )


class OnionmindWindow(QMainWindow):
    def __init__(self, core: Any, desktop_core: Any = None, demo: bool = False) -> None:
        super().__init__()
        self.core = core
        self.desktop_core = desktop_core
        self.demo = demo
        self._workers: set[SafeWorker] = set()
        data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.data_root = Path(data_location or (Path.home() / ".onionmind")) / "desktop"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions")
        self.workspace_bridge = WorkspaceBridge(desktop_core)
        self.harness_bridge = HarnessBridge(desktop_core, core)
        self.update_bridge = UpdateBridge(core, desktop_core)
        self.settings_data = {} if demo else self.settings_bridge.load()
        self.workspace: Optional[str] = None
        self.current_snapshot: dict[str, Any] = {}
        self.current_session: Any = None
        self.session_objects: dict[str, Any] = {}
        self.chat_messages: list[dict[str, Any]] = []
        self.attachments: list[str] = []
        self.installed_model_ids: list[str] = []
        self.active_kind: Optional[str] = None
        self.stop_event: Optional[threading.Event] = None
        self.stream_block: Optional[MessageBlock] = None
        self.harness_process: Optional[QProcess] = None
        self.harness_output = ""
        self.harness_generation = 0
        self.tor_probe_generation = 0
        self.tor_phase = "off"
        self.tor_stop_event: Optional[threading.Event] = None
        self.tor_in_use = False
        self._project_delete_pending: Optional[str] = None
        self._pending_redirect = False
        self._catalog_specs: tuple[Optional[int], Optional[int]] = (None, None)
        self._rail_requested = True
        self._inspector_requested = True
        self._model_dialog: Optional[ModelManagerDialog] = None
        self._update_timer: Optional[QTimer] = None
        self._build_window()
        self.preferences = self._load_preferences()
        self._apply_preferences()
        self._install_shortcuts()
        if demo:
            self._populate_demo()
        else:
            self._restore_state()
            # A staged update is local state, not network: surface it without
            # requiring the automatic-check permission.
            if self.update_bridge.available and self.update_bridge.pending() is not None:
                self.show_update_ready()
            if self.update_permission_enabled():
                self._start_update_timer()
            self._probe_services()
            self.tor_liveness_timer = QTimer(self)
            self.tor_liveness_timer.setInterval(2500)
            self.tor_liveness_timer.timeout.connect(self._poll_tor_liveness)
            self.tor_liveness_timer.start()

    def _build_window(self) -> None:
        self.setWindowTitle("Onionmind — private local workbench")
        icon_path = MODULE_DIR / "onionmind.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1420, 900)
        self.setMinimumSize(760, 620)
        root = QWidget()
        root.setObjectName("windowRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        toolbar.setFixedHeight(57)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 7, 12, 7)
        toolbar_layout.setSpacing(9)

        brand_box = QWidget()
        brand_box.setFixedWidth(205)
        self.brand_box = brand_box
        brand_layout = QHBoxLayout(brand_box)
        brand_layout.setContentsMargins(7, 0, 4, 0)
        brand_layout.setSpacing(9)
        logo = QLabel()
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pixmap)
        else:
            logo.setText("O")
            logo.setObjectName("avatarAssistant")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(28, 28)
        logo.setAccessibleName("Onionmind logo")
        brand = QLabel("Onionmind")
        brand.setObjectName("brand")
        self.brand_label = brand
        brand_layout.addWidget(logo)
        brand_layout.addWidget(brand)
        brand_layout.addStretch(1)

        rail_toggle = QToolButton()
        rail_toggle.setObjectName("bareButton")
        rail_toggle.setCheckable(True)
        rail_toggle.setChecked(True)
        rail_toggle.setIcon(_icon("rail"))
        rail_toggle.setToolTip("Toggle projects and sessions")
        rail_toggle.setAccessibleName("Toggle projects and sessions rail")
        rail_toggle.clicked.connect(self.toggle_rail)
        self.rail_toggle = rail_toggle
        brand_layout.addWidget(rail_toggle)
        toolbar_layout.addWidget(brand_box)

        self.repo_label = QLabel("No project")
        self.repo_label.setObjectName("title")
        self.repo_label.setMinimumWidth(95)
        self.repo_label.setMaximumWidth(180)
        self.repo_label.setAccessibleName("Current project")
        toolbar_layout.addWidget(self.repo_label)
        self.toolbar_separator = QLabel("/")
        self.toolbar_separator.setObjectName("meta")
        toolbar_layout.addWidget(self.toolbar_separator)
        self.branch_label = QLabel("Open a folder")
        self.branch_label.setObjectName("muted")
        self.branch_label.setMaximumWidth(180)
        self.branch_label.setAccessibleName("Current Git branch")
        toolbar_layout.addWidget(self.branch_label)
        toolbar_layout.addStretch(1)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(190)
        self.model_combo.setMaximumWidth(260)
        self.model_combo.setAccessibleName("Onionmind model")
        self.model_combo.setToolTip("Choose the Onionmind model for the next run")
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        toolbar_layout.addWidget(self.model_combo)
        toolbar_layout.addWidget(self._build_context_slider())
        self.model_status = StatusPill("Model", "Checking", "busy")
        toolbar_layout.addWidget(self.model_status)
        self.tor_status = StatusActionButton("Tor", "Off", "idle", "Turn on")
        self.tor_status.setToolTip(
            "Turn Onionmind's Tor proxy on or off. The same button always shows its current state."
        )
        self.tor_status.clicked.connect(self._toggle_tor)
        toolbar_layout.addWidget(self.tor_status)

        terminal_toggle = QToolButton()
        terminal_toggle.setObjectName("bareButton")
        terminal_toggle.setCheckable(True)
        terminal_toggle.setChecked(True)
        terminal_toggle.setIcon(_icon("terminal"))
        terminal_toggle.setToolTip("Toggle terminal drawer (Ctrl+`)")
        terminal_toggle.setAccessibleName("Toggle terminal drawer")
        terminal_toggle.clicked.connect(self.toggle_terminal)
        self.terminal_toggle = terminal_toggle
        toolbar_layout.addWidget(terminal_toggle)
        inspector_toggle = QToolButton()
        inspector_toggle.setObjectName("bareButton")
        inspector_toggle.setCheckable(True)
        inspector_toggle.setChecked(True)
        inspector_toggle.setIcon(_icon("inspector"))
        inspector_toggle.setToolTip("Toggle inspector (Ctrl+Shift+I)")
        inspector_toggle.setAccessibleName("Toggle context, changes, and activity inspector")
        inspector_toggle.clicked.connect(self.toggle_inspector)
        self.inspector_toggle = inspector_toggle
        toolbar_layout.addWidget(inspector_toggle)
        root_layout.addWidget(toolbar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.left_rail = LeftRail()
        self.left_rail.newTaskRequested.connect(self.new_task)
        self.left_rail.addSessionRequested.connect(self.add_session)
        self.left_rail.openFolderRequested.connect(self.open_folder)
        self.left_rail.projectSelected.connect(self.select_workspace)
        self.left_rail.removeProjectRequested.connect(self.remove_project_from_menu)
        self.left_rail.deleteProjectRequested.connect(self.delete_project_from_machine)
        self.left_rail.sessionSelected.connect(self.load_session)
        self.left_rail.modelsRequested.connect(self.open_model_manager)
        self.left_rail.settingsRequested.connect(self.open_settings)
        self.left_rail.exportRequested.connect(self.export_conversation)
        self.left_rail.archiveRequested.connect(self.archive_session)
        self.left_rail.deleteSessionRequested.connect(self.delete_session_from_machine)
        self.left_rail.renameSessionRequested.connect(self.rename_session)
        self.main_splitter.addWidget(self.left_rail)

        center = QWidget()
        center.setObjectName("centerPane")
        center.setMinimumWidth(450)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        self.transcript = TranscriptView()
        center_layout.addWidget(self.transcript, 1)
        self.terminal = TerminalPane(self.desktop_core)
        self.terminal.closeRequested.connect(lambda: self.toggle_terminal(False))
        self.terminal.stateChanged.connect(self.set_status)
        center_layout.addWidget(self.terminal)
        self.composer_frame = self._build_composer()
        center_layout.addWidget(self.composer_frame)
        self.retry_button = QPushButton("Retry last turn")
        self.retry_button.setToolTip("Ask the local model again with the same conversation")
        self.retry_button.setAccessibleName("Retry the failed chat turn")
        self.retry_button.clicked.connect(self._retry_last_turn)
        self.retry_button.hide()
        center_layout.addWidget(self.retry_button)
        self.main_splitter.addWidget(center)

        self.inspector = InspectorPane()
        self.inspector.refreshRequested.connect(self.refresh_workspace)
        self.main_splitter.addWidget(self.inspector)
        self.main_splitter.setSizes([224, 860, 292])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        root_layout.addWidget(self.main_splitter, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("meta")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setFixedHeight(24)
        self.statusBar().addWidget(self.status_label, 1)
        self.update_status = QPushButton("Updates…")
        self.update_status.setObjectName("updateStatus")
        self.update_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_status.setFlat(True)
        self.update_status.clicked.connect(self.open_settings)
        self._set_update_notice(None)
        self.statusBar().addPermanentWidget(self.update_status)
        self.scope_status = QLabel("No project selected")
        self.scope_status.setObjectName("meta")
        self.statusBar().addPermanentWidget(self.scope_status)

    def _build_context_slider(self) -> QWidget:
        """The agent's context budget, as a slider.

        This is the number that decides whether a complex job is possible, and
        it is also the number that decides whether the model still fits in VRAM
        - so it is the one knob worth reaching for often enough to deserve a
        place in the toolbar rather than a settings page.

        Stops are powers of two because the KV cache is sized from this; the
        values in between buy nothing and make the control fiddly. The core
        clamps to the same range, so a hand-edited file cannot push it out.
        """
        steps = list(getattr(self.core, "CODE_STEPS", (8192, 16384, 32768, 65536, 131072)))
        self.context_steps = steps

        box = QWidget()
        box.setFixedWidth(150)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(steps) - 1)
        slider.setPageStep(1)
        slider.setAccessibleName("Agent context budget")
        self.context_slider = slider

        label = QLabel()
        label.setObjectName("meta")
        label.setFixedWidth(38)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.context_label = label

        reader = getattr(self.core, "code_ctx", None)
        current = reader() if callable(reader) else steps[len(steps) // 2]
        # Nearest stop, not exact match: an env override or an older saved value
        # can sit between two of them, and the slider still has to land somewhere.
        index = min(range(len(steps)), key=lambda i: abs(steps[i] - current))
        slider.setValue(index)
        self._sync_context_label(index)
        # valueChanged fires for every pixel of a drag; sliderReleased would miss
        # the arrow keys, which is how the control is reachable without a mouse.
        slider.valueChanged.connect(self._context_changed)

        layout.addWidget(slider)
        layout.addWidget(label)
        return box

    def _sync_context_label(self, index: int) -> None:
        value = self.context_steps[index]
        self.context_label.setText(f"{value // 1024}k")
        locked = _as_text(os.environ.get("ONIONMIND_CODE_CTX", ""))
        if locked:
            # The env var wins in the core, so a slider that silently disagreed
            # with the running agent would be a lie. Say so instead.
            self.context_slider.setEnabled(False)
            self.context_slider.setToolTip(
                f"Context budget is pinned to {locked} by ONIONMIND_CODE_CTX"
            )
            return
        self.context_slider.setToolTip(
            f"Agent context budget: {value:,} tokens. Bigger fits more of the job "
            "in one session; too big and the model spills out of VRAM onto the CPU. "
            "Takes effect on the next agent run."
        )

    def _context_changed(self, index: int) -> None:
        self._sync_context_label(index)
        value = self.context_steps[index]
        writer = getattr(self.core, "set_code_ctx", None)
        if callable(writer) and not self.demo:
            try:
                writer(value)
            except OSError as exc:
                self.set_status(f"Could not save the context budget: {exc}")
                return
        self.set_status(f"Agent context budget {value:,} tokens · applies to the next run")

    def _build_composer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("composerFrame")
        frame.setMinimumHeight(148)
        frame.setMaximumHeight(178)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(12, 9, 12, 9)
        outer.setSpacing(6)
        self.composer = ComposerEdit()
        self.composer.setPlaceholderText("Describe what you want Onionmind to do in this project…")
        self.composer.sendRequested.connect(self.submit)
        self.composer.filesDropped.connect(self.add_attachments)
        self.composer.textChanged.connect(self._sync_action_states)
        outer.addWidget(self.composer, 1)
        self.attachment_row = QWidget()
        attachment_layout = QHBoxLayout(self.attachment_row)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_label = QLabel()
        self.attachment_label.setObjectName("attachmentLabel")
        clear = QToolButton()
        clear.setObjectName("bareButton")
        clear.setIcon(_icon("close"))
        clear.setToolTip("Remove all attachments")
        clear.setAccessibleName("Remove all attachments")
        clear.clicked.connect(self.clear_attachments)
        attachment_layout.addWidget(self.attachment_label)
        attachment_layout.addWidget(clear)
        attachment_layout.addStretch(1)
        self.attachment_row.hide()
        outer.addWidget(self.attachment_row)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        attach = QToolButton()
        attach.setIcon(_icon("attach"))
        attach.setToolTip("Attach files or images")
        attach.setAccessibleName("Attach files or images")
        attach.clicked.connect(self.choose_attachments)
        controls.addWidget(attach)
        mode_frame = QFrame()
        mode_frame.setObjectName("modeSwitch")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(2, 2, 2, 2)
        mode_layout.setSpacing(0)
        self.chat_button = QPushButton("Chat")
        self.agent_button = QPushButton("Agent")
        self.chat_button.setAccessibleName("Use Onionmind Chat")
        self.chat_button.setToolTip("Chat privately with the selected Onionmind model")
        self.agent_button.setAccessibleName("Use Onionmind Agent")
        self.agent_button.setToolTip("Ask Onionmind Agent to work in the selected project")
        for button in (self.chat_button, self.agent_button):
            button.setObjectName("modeButton")
            button.setCheckable(True)
            mode_layout.addWidget(button)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.chat_button)
        self.mode_group.addButton(self.agent_button)
        self.chat_button.clicked.connect(lambda: self.set_mode("chat"))
        self.agent_button.clicked.connect(lambda: self.set_mode("agent"))
        controls.addWidget(mode_frame)
        self.approval_state = QLabel("Protected actions stop safely")
        self.approval_state.setObjectName("accent")
        self.approval_state.setToolTip(
            "Approvals are on: the agent asks before a protected action, and where there is nobody to ask it stops instead of continuing"
        )
        self.approval_state.setAccessibleName(
            "Onionmind Agent protected actions stop safely"
        )
        controls.addWidget(self.approval_state)
        self.yolo_consent = QCheckBox("YOLO: run without asking")
        self.yolo_consent.setChecked(False)
        self.yolo_consent.setToolTip(
            "Auto-approve file edits and shell commands for this run. The network "
            "boundary does not move: commands that cannot be proxied stay refused, "
            "and everything still leaves through Tor or not at all."
        )
        self.yolo_consent.setAccessibleName(
            "Run Onionmind Agent without approval prompts"
        )
        self.yolo_consent.toggled.connect(self._yolo_toggled)
        controls.addWidget(self.yolo_consent)
        self.search_consent = QCheckBox("Allow Tor search this turn")
        self.search_consent.setChecked(False)
        self.search_consent.setToolTip(
            "One-turn permission. If needed, Onionmind starts Tor in the background without opening Tor Browser."
        )
        self.search_consent.setAccessibleName("Allow Tor web search for the next Chat turn only")
        controls.addWidget(self.search_consent)
        self.disclosure = QLabel()
        self.disclosure.setObjectName("disclosure")
        self.disclosure.setWordWrap(True)
        controls.addWidget(self.disclosure, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setAccessibleName("Send task")
        self.send_button.clicked.connect(self.submit)
        controls.addWidget(self.send_button)
        outer.addLayout(controls)
        self.set_mode(_as_text(self.settings_data.get("mode", "agent")))
        self._sync_action_states()
        return frame

    def _install_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+N", self.new_task),
            ("Ctrl+O", self.open_folder),
            ("Ctrl+L", self.focus_composer),
            ("Ctrl+Shift+S", self.export_conversation),
            ("Ctrl+,", self.open_settings),
            ("Ctrl+`", lambda: self.toggle_terminal(not self.terminal.isVisible())),
            ("Ctrl+Shift+I", lambda: self.toggle_inspector(not self.inspector.isVisible())),
            ("Escape", self.stop_active),
        ]
        self.shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def _restore_state(self) -> None:
        self._restore_window_layout()
        self._apply_startup_preferences()
        model = _as_text(self.settings_data.get("model") or getattr(self.core, "MODEL", "inferno"))
        self.set_model_options([model], model)
        recent = self.settings_data.get("recent_projects") or []
        for path in reversed(recent[:8]):
            if path:
                self.left_rail.add_project(_as_text(path), select=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(session, "id")): session for session in sessions}
        self.left_rail.set_sessions(self._project_sessions(sessions))
        workspace = _as_text(self.settings_data.get("workspace"))
        if workspace and Path(workspace).is_dir():
            self.select_workspace(workspace)
        else:
            self.new_task(save_current=False)

    def _probe_services(self) -> None:
        def model_probe(signals: WorkerSignals) -> tuple[str, list[str]]:
            del signals
            detector = getattr(self.core, "detect_backend", None)
            if callable(detector):
                detector()
            backend = _as_text(getattr(self.core, "BACKEND", "local service"))
            installed = getattr(self.core, "installed_models", None)
            models = list(installed()) if callable(installed) else []
            return backend, models

        worker = self._start_worker(model_probe)
        worker.signals.result.connect(self._model_probe_complete)
        worker.signals.error.connect(lambda message: self._model_probe_failed(message))

        checker = getattr(self.core, "tor_proxy_port", None)
        if not callable(checker):
            self.tor_phase = "error"
            self.tor_status.set_status("Unavailable", "bad")
            _set_tor_action(
                self,
                "Unavailable",
                enabled=False,
                tooltip="This Onionmind build cannot manage a Tor proxy.",
            )
            return
        if not _tor_is_enabled(self.core):
            self._show_local_tor_state(None)
            return
        self.tor_probe_generation += 1
        generation = self.tor_probe_generation
        self.tor_phase = "probing"
        self.tor_status.set_status("Checking…", "warn")
        _set_tor_action(
            self,
            "Turn off",
            tooltip="Stop checking and turn Tor off for Onionmind.",
        )

        def tor_probe(signals: WorkerSignals) -> Any:
            del signals
            port = checker()
            if not port:
                return None
            return self._verify_tor()

        tor_worker = self._start_worker(tor_probe)
        tor_worker.signals.result.connect(
            lambda port, value=generation: self._tor_probe_complete(port, value)
        )
        tor_worker.signals.error.connect(
            lambda message, value=generation: self._tor_probe_failed(message, value)
        )

    def _start_worker(
        self,
        fn: Callable[[WorkerSignals], Any],
        setup: Optional[Callable[[SafeWorker], None]] = None,
    ) -> SafeWorker:
        worker = SafeWorker(fn, self.core)
        self._workers.add(worker)
        if setup is not None:
            # A fast job can finish before the caller connects, so wire it up first.
            setup(worker)

        def forget() -> None:
            self._workers.discard(worker)

        worker.signals.finished.connect(forget)
        thread = threading.Thread(
            target=worker.run,
            name="onionmind-desktop-worker",
            daemon=True,
        )
        # Deferred to the next event-loop turn, not started here. Thread.start()
        # returns only once the thread is already running, so a job that finishes
        # without blocking - check() returning "Tor is not up" with no subprocess
        # to spawn - can emit result BEFORE the caller connects to it, and the
        # signal goes nowhere: the UI sits on "Preparing..." with no worker alive
        # and no error. singleShot(0) lets the calling slot finish its connects
        # first, so every call site is safe whether or not it passes setup.
        QTimer.singleShot(0, thread.start)
        return worker

    def _model_probe_complete(self, payload: tuple[str, list[str]]) -> None:
        _backend, models = payload
        current = self.current_model_id()
        self.set_model_options(models or [current], current)
        self.model_status.set_status("Ready", "good")
        self.model_status.setToolTip("Onionmind inference is ready on this machine")
        self.inspector.append_activity("Onionmind inference ready")

    def _model_probe_failed(self, message: str) -> None:
        self.model_status.set_status("Unavailable", "bad")
        self.set_status(message)
        self.inspector.append_activity(f"Onionmind inference unavailable: {message}")

    def _tor_probe_failed(self, message: str, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self.tor_phase = "error"
        self.tor_status.set_status("Unavailable", "bad")
        message = _brand_runtime_text(message)
        _set_tor_action(
            self,
            "Retry",
            tooltip=f"Tor could not be verified: {message} Click to try again.",
        )
        self.set_status(f"Tor could not be verified: {message}")
        self.inspector.append_activity("Tor unavailable; protected features remain offline")

    def _tor_probe_complete(self, port: Any, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self._show_local_tor_state(port)

    def _show_local_tor_state(self, port: Any) -> None:
        if not _tor_is_enabled(self.core):
            self.tor_phase = "off"
            self.tor_status.set_status("Off", "idle")
            note = (
                "Tor is off for Onionmind. A local proxy is still running elsewhere, but "
                "Onionmind will not use it until you turn Tor on."
                if port
                else "Tor is off for Onionmind. Protected features stay offline until you turn it on."
            )
            self.tor_status.setToolTip(note)
            _set_tor_action(
                self,
                "Turn on",
                tooltip="Allow Onionmind to use Tor and start or reuse a local proxy.",
            )
            return
        managed = getattr(self.core, "_managed_tor_process", None)
        try:
            managed_running = managed is not None and managed.poll() is None
        except Exception:
            managed_running = False
        if managed_running and not port:
            stop = getattr(self.core, "stop_managed_tor", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
            managed_running = False
        verified = bool(port and getattr(self.core, "_port", None) == port)
        if verified:
            self.tor_phase = "running"
            self.tor_status.set_status("Ready", "good")
            _set_tor_action(
                self,
                "Turn off",
                tooltip=(
                    "Stop Onionmind's background Tor process."
                    if managed_running
                    else "Disconnect Onionmind from this external Tor proxy; the external process stays running."
                ),
            )
            # Tor being verified is also the self-updater's only window to look
            # for updates, so piggyback the permissioned autocheck on it.
            self._maybe_autocheck_updates()
            if managed_running:
                self.tor_status.setToolTip(
                    "Tor is verified and ready. Onionmind's background "
                    "process is running without a browser or console window. Click to turn it off."
                )
                self.inspector.append_activity("Onionmind-owned background Tor is ready")
            else:
                self.tor_status.setToolTip(
                    "Tor is verified and ready. The proxy was already "
                    "running, so turning Onionmind off will leave that external process alone."
                )
                self.inspector.append_activity("Pre-existing local Tor proxy verified")
        elif port:
            self.tor_phase = "error"
            self.tor_status.set_status("Unavailable", "bad")
            if callable(getattr(self.core, "set_tor_enabled", None)):
                _set_tor_action(
                    self,
                    "Retry",
                    tooltip="A local proxy exists but is not verified as Tor. Click to verify it again.",
                )
            else:
                _set_tor_action(
                    self,
                    "External",
                    enabled=False,
                    tooltip="This proxy is managed outside Onionmind and cannot be stopped here.",
                )
            self.tor_status.setToolTip(
                "A local proxy connection exists, but Onionmind has not verified it as Tor. "
                "Protected features remain offline. Click to try verification again."
            )
            self.inspector.append_activity("Unverified local proxy connection detected")
        else:
            self.tor_phase = "off"
            self.tor_status.set_status("Off", "idle")
            self.tor_status.setToolTip(
                "Tor is off. Use Turn on to start it; a permitted search may also start it "
                "unless you explicitly turned it off. No browser window is opened."
            )
            _set_tor_action(
                self,
                "Turn on",
                tooltip="Start Onionmind's background Tor proxy without opening a browser window.",
            )
            self.inspector.append_activity("Background Tor is off; Chat remains local-only")

    def ensure_tor(self, stop_event=None):
        """Bring Tor up if it is not already, and return the port. Worker-side.

        Honours the core contract underneath: an existing listener is reused
        and never adopted, and no browser window is opened.
        """
        port = getattr(self.core, "_port", None)
        if port:
            return port
        starter = getattr(self.core, "start_tor_hidden", None)
        if not callable(starter):
            raise RuntimeError("This Onionmind build cannot start background Tor.")
        return starter(stop_event=stop_event)

    def _verify_tor(self, stop_event=None) -> Any:
        """Verify and return the core's pinned Tor port. Worker-side."""
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Tor check was cancelled.")
        verifier = getattr(self.core, "tor_check", None)
        if not callable(verifier):
            raise RuntimeError("This Onionmind build cannot verify a Tor circuit.")
        verifier()
        verified_port = getattr(self.core, "_port", None)
        if (
            not verified_port
            or not _tor_is_enabled(self.core)
            or (stop_event is not None and stop_event.is_set())
        ):
            raise RuntimeError("Tor check was cancelled or did not verify a circuit.")
        return verified_port

    def announce_tor_starting(self, note: str = "") -> "threading.Event":
        """Flip the control to Checking and return the event that cancels it.

        Called on the GUI thread before the worker that does the starting, so
        the button never sits on Off while Tor is coming up and being verified.
        """
        self.tor_probe_generation += 1
        self.tor_phase = "starting"
        self.tor_stop_event = threading.Event()
        self.tor_status.set_status("Checking…", "warn")
        self.tor_status.setToolTip(
            "Tor is starting or being verified. Click to cancel; no browser window is opened."
        )
        _set_tor_action(
            self,
            "Cancel",
            tooltip="Cancel background Tor startup.",
        )
        self.set_status(note or "Starting background Tor without opening a browser window…")
        self.inspector.append_activity(note or "Background Tor start requested")
        return self.tor_stop_event

    def _toggle_tor(self) -> None:
        """The explicit toolbar action starts Tor, cancels startup, or turns it off.

        Stop is offered in every phase, mid-start included - start_tor_hidden()
        polls the stop event while it waits for the SOCKS port, so cancelling a
        slow bootstrap does not mean waiting out its timeout.
        """
        if self.tor_phase in ("starting", "probing"):
            self.stop_tor("Background Tor start cancelled.")
            return
        if self.tor_phase == "running":
            self.stop_tor()
            return
        self._start_tor_from_toolbar()

    def stop_tor(self, note: str = "") -> None:
        """Turn Tor off for Onionmind, at any point in its life.

        Only ever terminate ours. A listener Onionmind found rather than
        started stays alive, while the core gate prevents Onionmind from using
        it until the user explicitly turns Tor on again.
        """
        if self.tor_in_use:
            self.stop_active()
        event = getattr(self, "tor_stop_event", None)
        if event is not None:
            event.set()                          # unblocks a start still waiting
        managed = getattr(self.core, "_managed_tor_process", None)
        stopper = getattr(self.core, "stop_managed_tor", None)
        external = managed is None and self.tor_phase == "running"
        self.tor_probe_generation += 1
        disabled_for_onionmind = _set_tor_enabled(self.core, False)
        if managed is None and self.tor_phase == "running" and not disabled_for_onionmind:
            self.tor_status.setToolTip(
                "This Tor was already running when Onionmind found it. Onionmind did "
                "not start it and will not stop it; stop it where you started it."
            )
            _set_tor_action(
                self,
                "External",
                enabled=False,
                tooltip="This proxy is managed outside Onionmind and cannot be stopped here.",
            )
            self.set_status("That Tor proxy is not Onionmind's to stop.")
            self.inspector.append_activity("Stop refused: the Tor proxy was not started by Onionmind")
            return
        if managed is not None and callable(stopper):
            try:
                stopper()
            except Exception as exc:             # a dead process is still stopped
                self.inspector.append_activity(f"Stopping background Tor reported: {exc}")
        self.tor_stop_event = None
        self.tor_phase = "off"
        self.tor_status.set_status("Off", "idle")
        self.tor_status.setToolTip(
            "Tor is off for Onionmind. The external proxy remains running."
            if external
            else "Tor is off. Turn it on again without opening a browser window."
        )
        _set_tor_action(
            self,
            "Turn on",
            tooltip="Allow Onionmind to use Tor and start or reuse a local proxy.",
        )
        outcome = note or (
            "Tor turned off for Onionmind; the external proxy is still running."
            if external
            else "Background Tor stopped."
        )
        self.set_status(outcome)
        self.inspector.append_activity(outcome)

    def _start_tor_from_toolbar(self) -> None:
        """Start background Tor on demand, from the one control that shows it."""
        if not callable(getattr(self.core, "start_tor_hidden", None)):
            self.set_status("This Onionmind build cannot start background Tor.")
            _set_tor_action(
                self,
                "Unavailable",
                enabled=False,
                tooltip="This Onionmind build cannot manage a Tor proxy.",
            )
            return
        _set_tor_enabled(self.core, True)
        stop_event = self.announce_tor_starting("Starting background Tor from the toolbar…")
        generation = self.tor_probe_generation

        def start_job(signals: WorkerSignals) -> Any:
            del signals
            self.ensure_tor(stop_event)
            return self._verify_tor(stop_event)

        worker = self._start_worker(start_job)
        worker.signals.result.connect(
            lambda port, value=generation: self._toolbar_tor_started(port, value)
        )
        worker.signals.error.connect(
            lambda message, value=generation: self._toolbar_tor_failed(message, value)
        )

    def _toolbar_tor_started(self, port: Any, generation: int) -> None:
        if generation != self.tor_probe_generation or self.tor_phase != "starting":
            return
        self.tor_stop_event = None
        self._show_local_tor_state(port)
        if self.tor_phase == "running":
            self.set_status("Tor is ready.")
        else:
            self.set_status("Tor is unavailable.")

    def _toolbar_tor_failed(self, message: str, generation: int) -> None:
        if generation != self.tor_probe_generation or self.tor_phase != "starting":
            return
        self.tor_stop_event = None
        self.tor_phase = "error"
        _set_tor_enabled(self.core, False)
        self.tor_status.set_status("Unavailable", "bad")
        message = _brand_runtime_text(message)
        self.tor_status.setToolTip(message)
        _set_tor_action(
            self,
            "Retry",
            tooltip=f"Tor did not become ready: {message} Click to try again.",
        )
        self.set_status(f"Tor did not start: {message}")
        self.inspector.append_activity(f"Background Tor failed to start: {message}")

    def _poll_tor_liveness(self) -> None:
        """Keep the only Tor indicator honest using local process/socket state."""
        if self.tor_phase != "running":
            return
        managed = getattr(self.core, "_managed_tor_process", None)
        managed_exited = False
        if managed is not None:
            try:
                managed_exited = managed.poll() is not None
            except Exception:
                managed_exited = True
            if managed_exited:
                stop = getattr(self.core, "stop_managed_tor", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:
                        pass
        probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = probe() if callable(probe) else None
        except Exception:
            port = None
        if managed_exited:
            self._show_local_tor_state(port)
            return
        if not port:
            try:
                setattr(self.core, "_port", None)
            except Exception:
                pass
            self._show_local_tor_state(None)
        elif getattr(self.core, "_port", None) not in (None, port):
            try:
                setattr(self.core, "_port", None)
            except Exception:
                pass
            self._show_local_tor_state(port)

    def _describe_model(self, raw_id: str) -> str:
        helper = getattr(self.desktop_core, "describe_model", None) if self.desktop_core else None
        if callable(helper):
            try:
                display = helper(raw_id)
                described = _as_text(_field(display, "display_name", ""))
                if described:
                    return described
            except Exception:
                pass
        lower = raw_id.lower()
        # Fallback for a core too old to describe models: the shipped tiers, with
        # what they actually are and what they cost to run.
        for token, described in (
            ("spark", "LFM2.5 2.6B · very light - ~2 GB, fine on CPU"),
            ("ember", "Qwen3.5 4B · light - ~3 GB VRAM"),
            ("blaze", "Qwen3.5 9B · moderate - ~7 GB VRAM"),
            ("inferno", "Qwen3.8 27B · heavy - ~12-16 GB VRAM"),
        ):
            if token in lower:
                return described
        return raw_id

    def set_model_options(self, models: Iterable[str], current: str = "") -> None:
        values: list[str] = []
        for model in [current, *models]:
            model = _as_text(model).strip()
            if model and model not in values:
                values.append(model)
        self.installed_model_ids = values
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        counts: dict[str, int] = {}
        for model in values:
            base = self._describe_model(model)
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} {counts[base]}"
            self.model_combo.addItem(label, model)
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(max(0, index))
        self.model_combo.blockSignals(False)

    def current_model_id(self) -> str:
        return _as_text(self.model_combo.currentData()) or _as_text(getattr(self.core, "MODEL", "inferno")) or "inferno"

    def _model_changed(self, index: int) -> None:
        del index
        model = self.current_model_id()
        try:
            setattr(self.core, "MODEL", model)
        except Exception:
            pass
        self.settings_data["model"] = model
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.set_status(f"Model set to {self._describe_model(model)}")

    def _yolo_toggled(self, on: bool) -> None:
        """The label beside the box states what is actually armed, not a default."""
        self.approval_state.setText(
            "YOLO - no approval prompts" if on else "Protected actions stop safely"
        )
        self.inspector.append_activity(
            "YOLO armed: edits and commands run unattended; network boundary unchanged"
            if on else "Approvals on: protected actions stop safely"
        )

    def set_mode(self, mode: str) -> None:
        mode = "chat" if mode.lower() == "chat" else "agent"
        self.chat_button.setChecked(mode == "chat")
        self.agent_button.setChecked(mode == "agent")
        self.mode = mode
        if mode == "chat":
            self.approval_state.hide()
            self.yolo_consent.hide()
            self.search_consent.show()
            self.disclosure.setText("Private local chat · Tor search needs one-turn permission")
            self.composer.setPlaceholderText("Ask Onionmind anything…")
        else:
            self.approval_state.show()
            self.yolo_consent.show()
            self.search_consent.setChecked(False)
            self.search_consent.hide()
            self.disclosure.setText("Early access · Agent network access is separate from Tor search")
            self.composer.setPlaceholderText("Describe what you want Onionmind Agent to change…")
        self.settings_data["mode"] = mode
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def focus_composer(self) -> None:
        self.composer.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_status(self, text: str) -> None:
        text = _brand_runtime_text(text)
        self.status_label.setText(text)
        self.status_label.setAccessibleName(f"Application status: {text}")

    def toggle_rail(self, visible: Optional[bool] = None) -> None:
        target = (not self.left_rail.isVisible()) if visible is None else bool(visible)
        self._rail_requested = target
        self.left_rail.setVisible(target)
        self.rail_toggle.setChecked(target)

    def toggle_inspector(self, visible: Optional[bool] = None) -> None:
        target = (not self.inspector.isVisible()) if visible is None else bool(visible)
        self._inspector_requested = target
        self.inspector.setVisible(target)
        self.inspector_toggle.setChecked(target)

    def toggle_terminal(self, visible: Optional[bool] = None) -> None:
        target = (not self.terminal.isVisible()) if visible is None else bool(visible)
        self.terminal.setVisible(target)
        self.terminal_toggle.setChecked(target)
        if target:
            self.terminal.command.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # --- Remembered window layout --------------------------------------
    #
    # Size, pane widths, and the *requested* pane visibility persist between
    # launches; the responsive rules in resizeEvent still win at narrow widths,
    # so a remembered layout can never force an unusable window.

    def _save_window_layout(self) -> None:
        if self.demo:
            # Demo windows run on empty settings; saving them would clobber a
            # real settings file with layout keys alone.
            return
        self.settings_data["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.settings_data["splitter_state"] = bytes(self.main_splitter.saveState().toBase64()).decode("ascii")
        self.settings_data["rail_visible"] = bool(self._rail_requested)
        self.settings_data["inspector_visible"] = bool(self._inspector_requested)
        self.settings_bridge.save(self.settings_data)

    def _restore_window_layout(self) -> None:
        geometry = _as_text(self.settings_data.get("window_geometry") or "")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except (ValueError, RuntimeError):
                pass  # an unreadable blob falls back to the default workbench
        splitter_state = _as_text(self.settings_data.get("splitter_state") or "")
        if splitter_state:
            try:
                self.main_splitter.restoreState(QByteArray.fromBase64(splitter_state.encode("ascii")))
            except (ValueError, RuntimeError):
                pass
        self._rail_requested = bool(self.settings_data.get("rail_visible", True))
        self._inspector_requested = bool(self.settings_data.get("inspector_visible", True))
        self.toggle_rail(self._rail_requested)
        self.toggle_inspector(self._inspector_requested)
        draft = _as_text(self.settings_data.get("composer_draft"))
        if draft:
            self.composer.setPlainText(draft)

    def reset_window_layout(self) -> None:
        """Put the workbench back to the shipped layout and forget the rest."""
        for key in ("window_geometry", "splitter_state", "rail_visible", "inspector_visible"):
            self.settings_data.pop(key, None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.showNormal()
        self.resize(1420, 900)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.center().x() - self.width() // 2,
                max(available.top(), available.center().y() - self.height() // 2),
            )
        self._rail_requested = True
        self._inspector_requested = True
        self.toggle_rail(True)
        self.toggle_inspector(True)
        self.main_splitter.setSizes([224, 860, 292])

    # --- Workbench preferences -----------------------------------------
    #
    # Presentation-only knobs: they apply live, persist through the settings
    # bridge, and default to the shipped workbench. The Tor boundary and the
    # updater permission live elsewhere on purpose - privacy is not a theme.

    def _load_preferences(self) -> dict[str, Any]:
        loader = getattr(self.desktop_core, "load_preferences", None)
        if callable(loader):
            return {**_FALLBACK_PREFERENCES, **dict(loader(self.settings_data))}
        return dict(_FALLBACK_PREFERENCES)

    def _apply_preferences(self) -> None:
        preferences = self.preferences
        self.composer.enterSends = bool(preferences.get("enter_sends", True))
        set_motion_override(_as_text(preferences.get("reduce_motion", "system")))
        factor = getattr(self.desktop_core, "text_scale_factor", None)
        scale = (
            factor(_as_text(preferences.get("text_scale", "system")))
            if callable(factor)
            else 1.0
        )
        apply_text_scale(scale)
        tokens = getattr(self.desktop_core, "context_window_tokens", None)
        if callable(tokens) and hasattr(self.core, "NUM_CTX"):
            # The adapters read NUM_CTX per request, so this lands on the next
            # turn without restarting anything.
            self.core.NUM_CTX = tokens(preferences.get("context_window", 16384))

    def set_preference(self, key: str, value: Any) -> None:
        """Validate, persist, and live-apply one workbench preference."""
        loader = getattr(self.desktop_core, "load_preferences", None)
        if callable(loader):
            self.preferences = {
                **_FALLBACK_PREFERENCES,
                **dict(loader({**self.settings_data, key: value})),
            }
        else:
            self.preferences = {**_FALLBACK_PREFERENCES, key: value}
        for name, pref in self.preferences.items():
            if name in _FALLBACK_PREFERENCES:
                self.settings_data[name] = pref
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._apply_preferences()

    def _apply_startup_preferences(self) -> None:
        """The composer mode and terminal state to open with."""
        preferences = self.preferences
        resolver = getattr(self.desktop_core, "resolve_startup_mode", None)
        last = _as_text(self.settings_data.get("mode", ""))
        if callable(resolver):
            mode = resolver(_as_text(preferences.get("startup_mode", "remember")), last)
        else:
            mode = last if last in {"chat", "agent"} else "agent"
        self.set_mode(mode)
        if bool(preferences.get("show_terminal_on_launch", False)):
            self.toggle_terminal(True)

    # --- Session care: rename, retry, drafts, attention -----------------

    def rename_session(self, session_id: str, title: str) -> None:
        session = self.session_objects.get(session_id)
        store = getattr(self.session_bridge, "store", None)
        if session is None or store is None:
            self.set_status("That session could not be renamed.")
            return
        session.title = title
        try:
            store.save(session)
        except (TypeError, ValueError) as exc:
            self.set_status(f"Could not rename the session: {exc}")
            return
        self._refresh_session_rows()
        self.set_status(f"Session renamed to {title}.")

    def _refresh_session_rows(self) -> None:
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(session, "id")): session for session in sessions
        }
        current_id = (
            _as_text(_field(self.current_session, "id"))
            if self.current_session is not None
            else None
        )
        self.left_rail.set_session_scope(self.workspace)
        self.left_rail.set_sessions(self._project_sessions(sessions), current_id)

    def _retry_last_turn(self) -> None:
        """Ask again after a failed chat turn: the error bubble comes off the
        history and the same conversation is re-sent locally."""
        self.retry_button.hide()
        if self.active_kind:
            return
        while (
            self.chat_messages
            and self.chat_messages[-1].get("role") == "assistant"
            and _as_text(self.chat_messages[-1].get("content")).startswith(
                ("Local inference failed", "Local inference could not continue")
            )
        ):
            self.chat_messages.pop()
        if not self.chat_messages or self.chat_messages[-1].get("role") != "user":
            self.set_status("There is no failed chat turn to retry.")
            return
        self._set_active("chat")
        self._start_chat()

    def _alert_if_unfocused(self) -> None:
        """The native attention nudge for a run that ended while another
        window was in front."""
        app = QApplication.instance()
        if app is not None and not self.isActiveWindow():
            QApplication.alert(self)

    def _remember_draft(self) -> None:
        draft = self.composer.toPlainText()
        if draft.strip() and self.preferences.get("remember_drafts", True):
            self.settings_data["composer_draft"] = draft
        else:
            self.settings_data.pop("composer_draft", None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def _forget_draft(self) -> None:
        if self.settings_data.pop("composer_draft", None) is not None and not self.demo:
            self.settings_bridge.save(self.settings_data)

    def _sync_action_states(self) -> None:
        has_draft = bool(self.composer.toPlainText().strip() or self.attachments)
        self.send_button.setEnabled(bool(self.active_kind) or has_draft)
        self.left_rail.set_conversation_available(bool(self.chat_messages))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        width = self.width()
        compact_toolbar = width < 1100
        self.brand_box.setFixedWidth(165 if width < 900 else 205)
        self.repo_label.setVisible(width >= 900)
        self.branch_label.setVisible(not compact_toolbar)
        self.toolbar_separator.setVisible(not compact_toolbar)
        self.model_status.setVisible(width >= 1280)
        self.tor_status.setVisible(True)
        self.model_combo.setMinimumWidth(145 if compact_toolbar else 190)
        if width < 820:
            self.left_rail.hide()
        elif self._rail_requested:
            self.left_rail.show()
        if width < 1080:
            self.inspector.hide()
        elif self._inspector_requested:
            self.inspector.show()
        self.rail_toggle.setChecked(not self.left_rail.isHidden())
        self.inspector_toggle.setChecked(not self.inspector.isHidden())

    def choose_attachments(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach local files or images",
            self.workspace or str(Path.home()),
            "Supported files (*.png *.jpg *.jpeg *.webp *.gif *.py *.js *.ts *.tsx *.md *.txt *.json *.toml *.yml *.yaml);;All files (*.*)",
        )
        self.add_attachments(paths)

    def add_attachments(self, paths: list[str]) -> None:
        for path in paths:
            absolute = os.path.abspath(path)
            if os.path.isfile(absolute) and absolute not in self.attachments:
                self.attachments.append(absolute)
        self._update_attachments()

    def clear_attachments(self) -> None:
        self.attachments.clear()
        self._update_attachments()

    def _update_attachments(self) -> None:
        if not self.attachments:
            self.attachment_row.hide()
            self.attachment_label.clear()
            self._sync_action_states()
            return
        names = [Path(path).name for path in self.attachments]
        display = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        self.attachment_label.setText(f"Attached locally: {display}")
        self.attachment_label.setToolTip("\n".join(self.attachments))
        self.attachment_row.show()
        self._sync_action_states()

    def _confirm_permanent_deletion(
        self,
        *,
        title: str,
        text: str,
        detail: str,
        confirm_label: str,
    ) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(title)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setInformativeText(detail)
        confirm = dialog.addButton(
            confirm_label, QMessageBox.ButtonRole.DestructiveRole
        )
        confirm.setAccessibleName(confirm_label)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() is confirm

    def _validated_project_delete_target(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        is_junction = getattr(candidate, "is_junction", None)
        if candidate.is_symlink() or (callable(is_junction) and is_junction()):
            raise ValueError(
                "Linked project folders cannot be deleted here. Remove the project "
                "from the list, then manage the link in your file manager."
            )
        if not candidate.exists():
            raise ValueError("The project folder no longer exists on this machine.")
        if not candidate.is_dir():
            raise ValueError("The selected project path is not a folder.")

        target = candidate.resolve(strict=True)
        if target.parent == target or target.is_mount():
            raise ValueError("A drive or filesystem root cannot be deleted as a project.")

        home = Path.home().resolve()
        if target == home or target in home.parents:
            raise ValueError("Your home folder or one of its parents cannot be deleted here.")

        for protected, label in (
            (self.data_root.resolve(), "Onionmind's local data"),
            (MODULE_DIR.resolve(), "the running Onionmind application"),
        ):
            if (
                target == protected
                or target in protected.parents
                or protected in target.parents
            ):
                raise ValueError(f"This folder overlaps {label} and cannot be deleted here.")
        return target

    def _forget_project_reference(self, path: str) -> bool:
        target_key = _path_key(path)
        recent = [
            _as_text(item)
            for item in self.settings_data.get("recent_projects", [])
            if _as_text(item)
        ]
        remaining = [item for item in recent if _path_key(item) != target_key]
        removed = self.left_rail.remove_project(path) or len(remaining) != len(recent)
        self.settings_data["recent_projects"] = remaining
        if _path_key(self.settings_data.get("workspace")) == target_key:
            self.settings_data["workspace"] = ""
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        return removed

    def remove_project_from_menu(self, path: str) -> None:
        if not path:
            self.set_status("Select a project to remove from the list.")
            return
        if self._project_delete_pending == _path_key(path):
            self.set_status("That project folder is already being deleted.")
            return
        if not self._forget_project_reference(path):
            self.set_status("That project is no longer in the Projects list.")
            return
        name = Path(path).name or path
        still_open = _path_key(self.workspace) == _path_key(path)
        suffix = " It remains open." if still_open else ""
        self.set_status(
            f"Removed {name} from Projects; its folder remains on this machine.{suffix}"
        )
        self.inspector.append_activity(
            f"Project removed from the list; folder kept: {name}"
        )

    def delete_project_from_machine(self, path: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before deleting a project folder.")
            return
        if self._project_delete_pending is not None:
            self.set_status("Wait for the current project deletion to finish.")
            return
        if not path:
            self.set_status("Select a project folder to delete.")
            return
        if (
            _path_is_within(self.workspace, path)
            and self.terminal.process.state() != QProcess.ProcessState.NotRunning
        ):
            self.set_status(
                "Stop the terminal command before deleting its project folder."
            )
            return
        try:
            target = self._validated_project_delete_target(path)
        except (OSError, ValueError) as exc:
            message = _as_text(exc)
            self.set_status(message)
            QMessageBox.warning(self, "Project folder not deleted", message)
            return

        name = target.name or str(target)
        if not self._confirm_permanent_deletion(
            title="Delete project folder from machine",
            text=f"Permanently delete “{name}” and everything inside it?",
            detail=(
                "This removes the folder from this machine and cannot be undone.\n\n"
                f"{target}"
            ),
            confirm_label="Delete folder",
        ):
            return

        expected = target
        self._project_delete_pending = _path_key(expected)
        self.set_status(f"Deleting project folder from this machine: {name}…")

        def delete_job(signals: WorkerSignals) -> str:
            del signals
            checked = self._validated_project_delete_target(str(expected))
            if _path_key(checked) != _path_key(expected):
                raise RuntimeError(
                    "The project path changed before deletion, so Onionmind stopped safely."
                )
            shutil.rmtree(checked)
            return str(checked)

        def setup(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._project_delete_complete)
            worker.signals.error.connect(
                lambda message: self._project_delete_failed(str(expected), message)
            )

        self._start_worker(delete_job, setup)

    def _project_delete_complete(self, path: Any) -> None:
        deleted_path = _as_text(path)
        self._project_delete_pending = None
        was_open = _path_is_within(self.workspace, deleted_path)
        self._forget_project_reference(deleted_path)
        if was_open:
            self.close_project()
        name = Path(deleted_path).name or deleted_path
        self.set_status(f"Deleted project folder from this machine: {name}")
        self.inspector.append_activity(
            f"Project folder permanently deleted from this machine: {name}"
        )

    def _project_delete_failed(self, path: str, message: str) -> None:
        self._project_delete_pending = None
        name = Path(path).name or path
        text = f"Could not delete {name}: {message}"
        self.set_status(text)
        self.inspector.append_activity(text)
        QMessageBox.warning(
            self,
            "Project folder not deleted",
            text + "\n\nClose programs using the folder, check permissions, then try again.",
        )

    def open_folder(self) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before changing projects.")
            return
        path = QFileDialog.getExistingDirectory(self, "Open project folder", self.workspace or str(Path.home()))
        if path:
            self.select_workspace(path)

    def select_workspace(self, path: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before changing projects.")
            return
        if not path or not Path(path).is_dir():
            self.set_status(f"Project folder is unavailable: {path}")
            return
        self.workspace = str(Path(path).resolve())
        self.left_rail.add_project(self.workspace)
        self.repo_label.setText(Path(self.workspace).name or self.workspace)
        self.repo_label.setToolTip(self.workspace)
        self.branch_label.setText("Inspecting…")
        self.scope_status.setText(self.workspace)
        self.terminal.set_workspace(self.workspace)
        recent = [_as_text(p) for p in self.settings_data.get("recent_projects", [])]
        recent = [p for p in recent if _path_key(p) != _path_key(self.workspace)]
        recent.insert(0, self.workspace)
        self.settings_data.update(workspace=self.workspace, recent_projects=recent[:10])
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._refresh_session_rows()
        self.refresh_workspace()

    def refresh_workspace(self) -> None:
        if not self.workspace:
            self.set_status("Open a project folder to inspect context and Git changes.")
            return
        selected = self.workspace
        self.set_status("Inspecting project files and observed Git state…")

        def inspect_job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            return self.workspace_bridge.inspect(selected)

        worker = self._start_worker(inspect_job)
        worker.signals.result.connect(self._workspace_ready)
        worker.signals.error.connect(lambda message: self._workspace_failed(message))

    def _workspace_ready(self, snapshot: dict[str, Any]) -> None:
        if not self.workspace or _path_key(snapshot.get("root")) != _path_key(self.workspace):
            return
        self.current_snapshot = snapshot
        branch = snapshot.get("branch") or ("No repository" if not snapshot.get("is_git") else "detached HEAD")
        self.branch_label.setText(_as_text(branch))
        self.inspector.update_snapshot(snapshot)
        count = len(snapshot.get("changes") or [])
        self.set_status(f"Project inspected · {count} observed change(s)")
        self.inspector.append_activity(f"Project refreshed; {count} observed change(s)")

    def _workspace_failed(self, message: str) -> None:
        self.branch_label.setText("Inspection failed")
        self.set_status(f"Could not inspect project: {message}")
        self.inspector.append_activity(f"Project inspection failed: {message}")

    def new_task(self, save_current: bool = True) -> None:
        if self.active_kind:
            self.stop_active()
            self.set_status("Stopping the active run; create the new task when it has finished.")
            return
        if save_current and not self.save_current_session():
            self.set_status(
                "The current session was not cleared because its history could not be saved."
            )
            return
        self.current_session = None
        self.chat_messages = []
        self.transcript.clear()
        self.transcript.add_message(
            "assistant",
            "Open a project, choose an Onionmind model, then describe the task. Chat answers privately; Agent works in the selected repository and reports only changes observed on disk.",
        )
        self.composer.clear()
        self.clear_attachments()
        self.left_rail.clear_session_selection()
        self.set_status("New task ready")
        self.focus_composer()

    def add_session(self) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before adding a session.")
            return
        if not self.preferences.get("save_history", True):
            self.set_status(
                "Saving conversations to this machine is off in settings; nothing was written."
            )
            return
        if not self.save_current_session():
            self.set_status(
                "A new session was not added because the current history could not be saved."
            )
            return
        self.new_task(save_current=False)
        try:
            model = self.current_model_id()
            session = self.session_bridge.create(
                "New session", model, self.workspace, ()
            )
            session = self.session_bridge.save(
                session,
                title="New session",
                model=model,
                workspace=self.workspace,
                messages=[],
            )
        except Exception as exc:
            message = f"Could not add a saved session: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return
        self.current_session = session
        session_id = _as_text(_field(session, "id"))
        self.session_objects[session_id] = session
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(item, "id")): item for item in sessions
        }
        self.left_rail.set_sessions(self._project_sessions(sessions), session_id)
        self.set_status("Added saved session")
        self.inspector.append_activity("Saved session added locally")

    def _session_title(self) -> str:
        first = next((_as_text(m.get("content")) for m in self.chat_messages if m.get("role") == "user"), "New session")
        first = re.sub(r"\s+", " ", first).strip()
        return first[:48] + ("…" if len(first) > 48 else "")

    def _session_is_unchanged(self, model: str) -> bool:
        """Opening or leaving a session must not reorder the list; only new
        history may. The list sorts on updated_at and every save stamps it,
        so an unchanged session is not written back at all. The title is not
        compared: it is derived from the first message, and a renamed session
        must not be re-saved (and re-titled) just for being opened."""
        session = self.current_session
        return (
            session is not None
            and _as_text(_field(session, "model")) == model
            and _as_text(_field(session, "workspace")) == _as_text(self.workspace)
            and list(_field(session, "messages", ()) or ()) == self.chat_messages
        )

    def save_current_session(self) -> bool:
        if not self.chat_messages:
            return True
        if not self.preferences.get("save_history", True):
            # Nothing to report: the user turned off writing conversations to
            # disk, so an unwritten history is the expected outcome.
            return True
        try:
            # This is the final persistence boundary. A live tool round keeps
            # its raw structure in the worker's private history until it has
            # completed; only the copy owned by the UI is cleaned here.
            self.chat_messages = _sanitize_assistant_messages(self.chat_messages)
            title = self._session_title()
            model = self.current_model_id()
            if self._session_is_unchanged(model):
                return True
            if self.current_session is None:
                self.current_session = self.session_bridge.create(
                    title, model, self.workspace, self.chat_messages
                )
            self.current_session = self.session_bridge.save(
                self.current_session,
                title=title,
                model=model,
                workspace=self.workspace,
                messages=self.chat_messages,
            )
            session_id = _as_text(_field(self.current_session, "id"))
            self.session_objects[session_id] = self.current_session
            self.left_rail.set_sessions(
                self._project_sessions(self.session_bridge.list()), session_id
            )
            return True
        except Exception as exc:
            message = f"Session history could not be saved: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return False

    def _await_workers(self, timeout_ms: int = 3000) -> None:
        """Wait, bounded, for background jobs to finish. Used before a wipe:
        a worker reading the project folder keeps it undeletable."""
        deadline = time.monotonic() + timeout_ms / 1000
        while self._workers and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)

    def close_project(self) -> None:
        """Leave the workbench with no project open."""
        self.workspace = None
        self.current_snapshot = {}
        self.repo_label.setText("No project")
        self.repo_label.setToolTip("")
        self.branch_label.setText("Open a folder")
        self.scope_status.setText("No project selected")
        self.terminal.set_workspace(str(Path.home()))
        self.inspector.update_snapshot({})
        self._refresh_session_rows()

    def wipe_machine_data(self) -> dict[str, int]:
        """Destroy this user's work on this machine: every saved and archived
        session, the project list, **the project folders and everything in
        them**, the last opened project, any unsent draft, and the agent's
        network log. Files are overwritten before they are unlinked.

        A project that fails the same safety check the per-project delete uses
        (a drive root, your home folder, Onionmind's own data or program
        folder, a symlinked folder) is skipped and counted, not obeyed - the
        wipe is for your work, not for the machine.

        ponytail: the overwrite is best-effort user space. On an SSD or a
        copy-on-write filesystem the old blocks can survive; full-disk
        encryption or the Matchstick RAM-only image is the real guarantee.
        """
        targets: list[str] = []
        for index in range(self.left_rail.projects.count()):
            item = self.left_rail.projects.item(index)
            targets.append(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        targets.extend(
            _as_text(item) for item in self.settings_data.get("recent_projects", [])
        )
        targets.append(_as_text(self.workspace))

        # Release the folders first: a running shell keeps its working
        # directory open, and Windows will not remove a directory in use.
        self.terminal.stop()
        terminal_process = getattr(self.terminal, "process", None)
        if terminal_process is not None and (
            terminal_process.state() != QProcess.ProcessState.NotRunning
        ):
            terminal_process.kill()
            terminal_process.waitForFinished(2000)
        self.close_project()
        # A workspace/Git snapshot runs inside the project folder; a folder
        # being read cannot be removed, so let in-flight jobs land first.
        self._await_workers()

        sessions = self.delete_all_sessions()
        shred_tree = _optional_callable(self.desktop_core, "shred_tree")
        shred_file = _optional_callable(self.desktop_core, "shred_file")
        store_root = getattr(getattr(self.session_bridge, "store", None), "root", None)
        if callable(shred_tree) and store_root is not None:
            # Sweeps the archive and any quarantined leftovers with it.
            shred_tree(store_root)

        wiped, skipped, seen = 0, 0, set()
        for raw in targets:
            key = _path_key(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                target = self._validated_project_delete_target(raw)
            except (ValueError, OSError):
                skipped += 1
                continue
            for attempt in range(3):
                if callable(shred_tree):
                    shred_tree(target)
                if target.exists():
                    # Something still held a handle while the files went; take
                    # the empty shell of the folder with the blunt tool.
                    shutil.rmtree(target, ignore_errors=True)
                if not target.exists():
                    break
                time.sleep(0.15)      # Windows releases handles a beat later
            wiped += 0 if target.exists() else 1

        net_log = _as_text(getattr(self.core, "NET_LOG", ""))
        if net_log and callable(shred_file) and Path(net_log).is_file():
            shred_file(net_log)
        tor = self.wipe_tor_state()

        self.left_rail.clear_projects()
        for key in ("recent_projects", "workspace", "composer_draft"):
            self.settings_data.pop(key, None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.close_project()
        self.inspector.append_activity(
            f"Wiped from this machine: {sessions} session(s), {wiped} project folder(s), "
            f"{tor} Tor state file(s)"
            + (f", {skipped} protected path(s) skipped" if skipped else "")
        )
        return {"sessions": sessions, "projects": wiped, "skipped": skipped, "tor": tor}

    # Tor's own footprint: the state file naming this machine's guard relays,
    # the cached consensus and descriptors, and any log it wrote.
    _TOR_TRACE_PREFIXES = ("state", "cached-", "diff-cache", "lock", "unverified-")

    def wipe_tor_state(self) -> int:
        """Destroy Tor's history on this machine. Onionmind's own data
        directory goes whole; a Tor Browser one keeps the torrc that makes it
        runnable and loses every trace of what it did."""
        shred_tree = _optional_callable(self.desktop_core, "shred_tree")
        shred_file = _optional_callable(self.desktop_core, "shred_file")
        if not callable(shred_file):
            return 0
        stop_tor = getattr(self.core, "stop_managed_tor", None)
        if callable(stop_tor):
            # Tor holds its own files open; it has to be down first.
            try:
                stop_tor()
            except Exception:
                pass
        directories = getattr(self.core, "tor_data_dirs", None)
        try:
            candidates = list(directories()) if callable(directories) else []
        except Exception:
            candidates = []
        # Discovery is the core's job (tor_data_dirs); this only classifies
        # what comes back, so a core that names nothing wipes nothing.
        own = Path.home() / ".onionmind" / "tor"
        removed = 0
        for raw in candidates:
            directory = Path(_as_text(raw))
            if not directory.is_dir():
                continue
            if _path_key(directory) == _path_key(own):
                removed += shred_tree(directory) if callable(shred_tree) else 0
                continue
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                name = entry.name.casefold()
                traces = name.endswith(".log") or name.startswith(self._TOR_TRACE_PREFIXES)
                if traces and entry.is_file() and shred_file(entry):
                    removed += 1
        return removed

    def _project_sessions(self, sessions: Iterable[Any]) -> list[Any]:
        """The saved sessions belonging to the open project.

        Every session records the project it ran in, so the rail can show that
        project's work only and switching projects switches the list. Sessions
        saved with no project open belong to no project and show only then.
        """
        current = _path_key(self.workspace)
        return [
            item for item in sessions if _path_key(_field(item, "workspace")) == current
        ]

    def delete_all_sessions(self) -> int:
        """Permanently remove every saved and archived conversation."""
        removed = 0
        for archived in (False, True):
            for item in self.session_bridge.list(archived=archived):
                if self.session_bridge.delete(_as_text(_field(item, "id"))):
                    removed += 1
        self.current_session = None
        self.session_objects = {}
        self.left_rail.set_sessions([])
        if removed:
            self.inspector.append_activity(
                f"Deleted {removed} saved conversation(s) from local storage"
            )
        return removed

    def archive_session(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before removing a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to remove from the list.")
            return
        session = self.session_objects.get(session_id)
        title = _as_text(_field(session, "title", "this session"))
        answer = QMessageBox.question(
            self,
            "Remove session from Sessions",
            f"Remove “{title}” from Sessions? It will remain on this machine in local archive storage.",
            QMessageBox.StandardButton.Archive if hasattr(QMessageBox.StandardButton, "Archive") else QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        accepted = (
            getattr(QMessageBox.StandardButton, "Archive", QMessageBox.StandardButton.Yes),
            QMessageBox.StandardButton.Yes,
        )
        if answer not in accepted:
            return
        archived = self.session_bridge.archive(session_id)
        if archived is None:
            self.set_status("The selected session could not be removed from the list.")
            return
        self.session_objects.pop(session_id, None)
        if self.current_session is not None and _as_text(_field(self.current_session, "id")) == session_id:
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(item, "id")): item for item in sessions}
        self.left_rail.set_sessions(
            self._project_sessions(sessions),
            _as_text(_field(self.current_session, "id")) if self.current_session else None,
        )
        self.set_status(f"Removed session from Sessions: {title}")
        self.inspector.append_activity(
            f"Session removed from the list and kept in local archive storage: {title}"
        )

    def delete_session_from_machine(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before deleting a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to delete from this machine.")
            return
        session = self.session_objects.get(session_id)
        if session is None:
            session = next(
                (
                    item
                    for item in self.session_bridge.list()
                    if _as_text(_field(item, "id")) == session_id
                ),
                None,
            )
        if session is None:
            self.set_status("That saved session is no longer available.")
            self.left_rail.set_sessions(self._project_sessions(self.session_bridge.list()))
            return
        title = _as_text(_field(session, "title", "this session"))
        if not self._confirm_permanent_deletion(
            title="Delete session from machine",
            text=f"Permanently delete “{title}”?",
            detail=(
                "This removes the session history from this machine and cannot be undone."
            ),
            confirm_label="Delete session",
        ):
            return
        if not self.session_bridge.delete(session_id):
            self.set_status("The selected session could not be deleted from this machine.")
            QMessageBox.warning(
                self,
                "Session not deleted",
                "Onionmind could not remove the local session file. Check local storage permissions, then try again.",
            )
            return
        self.session_objects.pop(session_id, None)
        if (
            self.current_session is not None
            and _as_text(_field(self.current_session, "id")) == session_id
        ):
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(item, "id")): item for item in sessions
        }
        current_id = (
            _as_text(_field(self.current_session, "id"))
            if self.current_session
            else None
        )
        self.left_rail.set_sessions(self._project_sessions(sessions), current_id)
        self.set_status(f"Deleted session from this machine: {title}")
        self.inspector.append_activity(
            f"Session permanently deleted from this machine: {title}"
        )

    def export_conversation(self) -> None:
        if not self.chat_messages:
            self.set_status("There is no conversation to export yet.")
            return
        suggested = re.sub(r"[^A-Za-z0-9._-]+", "-", self._session_title()).strip("-") or "onionmind-session"
        default_path = str((Path(self.workspace) if self.workspace else Path.home()) / f"{suggested}.md")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Onionmind conversation",
            default_path,
            "Markdown (*.md);;Text (*.txt)",
        )
        if not path:
            return
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(".md")
        document = _conversation_markdown(
            self._session_title(),
            self._describe_model(self.current_model_id()),
            self.workspace,
            self.chat_messages,
        )
        try:
            destination.write_text(document, encoding="utf-8")
        except OSError as exc:
            self.set_status(f"Could not export conversation: {exc}")
            QMessageBox.warning(self, "Export failed", f"Could not write the export.\n\n{exc}")
            return
        self.set_status(f"Conversation exported to {destination}")
        self.inspector.append_activity(f"Conversation exported locally: {destination.name}")

    def load_session(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before switching sessions.")
            return
        if not self.save_current_session():
            self.set_status(
                "The current session was not switched because its history could not be saved."
            )
            return
        session = self.session_objects.get(session_id)
        if session is None:
            session = next((item for item in self.session_bridge.list() if _as_text(_field(item, "id")) == session_id), None)
        if session is None:
            self.set_status("That saved session is no longer available.")
            return
        self.current_session = session
        self.chat_messages = _sanitize_assistant_messages(
            _field(session, "messages", ()) or ()
        )
        self.transcript.clear()
        for message in self.chat_messages:
            role = message.get("role")
            if role in ("user", "assistant"):
                content = message.get("content")
                self.transcript.add_message(role, content if isinstance(content, str) else "[local image attachment]")
            elif role == "tool":
                self.transcript.add_tool_card(
                    _as_text(message.get("tool_name") or "Tool result"),
                    [("Local tool output", _as_text(message.get("content"))[:80])],
                )
        workspace = _as_text(_field(session, "workspace"))
        if workspace and Path(workspace).is_dir() and workspace != self.workspace:
            self.select_workspace(workspace)
        model = _as_text(_field(session, "model"))
        if model:
            if self.model_combo.findData(model) < 0:
                self.set_model_options([*self.installed_model_ids, model], model)
            else:
                self.model_combo.setCurrentIndex(self.model_combo.findData(model))
        self.set_status(f"Loaded session: {_field(session, 'title', 'Saved session')}")

    def submit(self) -> None:
        if self.active_kind:
            # A send during a live run is a redirect: stop the run, keep the
            # partial answer in the transcript, and send the typed direction
            # as the next turn the moment the run finishes unwinding. An empty
            # composer keeps the old meaning: plain stop.
            if self.composer.toPlainText().strip() or self.attachments:
                self._pending_redirect = True
                self.stop_active()
                if self._pending_redirect:
                    self.set_status("Stopping the current run; your direction is sent next.")
            else:
                self.stop_active()
            return
        task = self.composer.toPlainText().strip()
        if not task and not self.attachments:
            self.set_status("Describe a task or attach a local file first.")
            self.focus_composer()
            return
        if not task:
            task = "Review the attached local files."
        search_allowed = self.mode == "chat" and self.search_consent.isChecked()
        if (self.mode == "agent" or search_allowed) and not _tor_is_enabled(self.core):
            self.set_status("Tor proxy is off. Turn it on in the toolbar before using a protected feature.")
            self.inspector.append_activity(
                "Protected run blocked because Tor was explicitly turned off; no network request was made"
            )
            return
        if self.mode == "agent":
            answer = QMessageBox.warning(
                self,
                "Run Onionmind Agent directly?",
                "Onionmind Agent and commands it runs are not confined to Tor. They may "
                "contact arbitrary hosts directly and expose this machine's network address. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.set_status("Agent run cancelled; no process was started.")
                return
        self.search_consent.setChecked(False)
        attachment_names = [Path(path).name for path in self.attachments]
        visible_task = task + ("\n\nAttached locally: " + ", ".join(attachment_names) if attachment_names else "")
        message, agent_task = self._build_user_payload(task)
        self.chat_messages.append(message)
        self.transcript.add_message("user", visible_task)
        self.composer.clear()
        self._forget_draft()
        self.clear_attachments()
        self._set_active(self.mode)
        if not self.save_current_session():
            self.transcript.add_tool_card(
                "Session storage",
                [("Not saved", "The run will continue; check local disk access")],
            )
        if self.mode == "chat":
            if search_allowed:
                self._start_chat(True)
            else:
                self._start_chat()
        else:
            self._start_harness(agent_task)

    def _build_user_payload(self, task: str) -> tuple[dict[str, Any], str]:
        message: dict[str, Any] = {"role": "user", "content": task}
        agent_notes: list[str] = []
        images: list[str] = []
        text_sections: list[str] = []
        image_bytes = 0
        attachment_text_remaining = MAX_TEXT_TOTAL_BYTES

        def embed_image(path: Path, label: str) -> None:
            nonlocal image_bytes
            if len(images) >= MAX_IMAGE_COUNT:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the {MAX_IMAGE_COUNT}-image limit was reached]"
                )
                return
            size = path.stat().st_size
            if size > MAX_IMAGE_FILE_BYTES:
                text_sections.append(
                    f"\n\n[{label} is larger than 20 MB and was not embedded]"
                )
                return
            if image_bytes + size > MAX_IMAGE_TOTAL_BYTES:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the 24 MB aggregate image limit was reached]"
                )
                return
            with path.open("rb") as handle:
                payload = handle.read(MAX_IMAGE_FILE_BYTES + 1)
            if len(payload) > MAX_IMAGE_FILE_BYTES:
                text_sections.append(
                    f"\n\n[{label} grew beyond 20 MB while being read and was not embedded]"
                )
                return
            if image_bytes + len(payload) > MAX_IMAGE_TOTAL_BYTES:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the 24 MB aggregate image limit was reached]"
                )
                return
            images.append(base64.b64encode(payload).decode("ascii"))
            image_bytes += len(payload)

        for path_string in self.attachments:
            path = Path(path_string)
            agent_notes.append(str(path))
            try:
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    embed_image(path, f"Image {path.name}")
                else:
                    if attachment_text_remaining <= 0:
                        text_sections.append(
                            f"\n\n[Attached file {path.name} was omitted after the 256 KB aggregate attachment limit]"
                        )
                        continue
                    read_limit = min(MAX_TEXT_FILE_BYTES, attachment_text_remaining)
                    with path.open("rb") as handle:
                        payload = handle.read(read_limit + 1)
                    truncated = len(payload) > read_limit
                    raw = payload[:read_limit]
                    attachment_text_remaining -= len(raw)
                    text = raw.decode("utf-8", errors="replace")
                    marker = (
                        f"\n[Attached file truncated after {len(raw)} bytes; remaining content omitted.]"
                        if truncated
                        else ""
                    )
                    text_sections.append(
                        f"\n\nAttached file {path.name}:\n```\n{text}\n```{marker}"
                    )
            except OSError as exc:
                text_sections.append(f"\n\n[Could not read attached file {path.name}: {exc}]")
        selected_context = self.inspector.selected_context_files()
        if selected_context and self.workspace:
            root = Path(self.workspace).resolve()
            remaining = 256 * 1024
            for relative_path in selected_context[:12]:
                candidate = (root / relative_path).resolve(strict=False)
                try:
                    candidate.relative_to(root)
                except ValueError:
                    text_sections.append(f"\n\n[Selected context escaped the project and was ignored: {relative_path}]")
                    continue
                try:
                    if not candidate.is_file():
                        continue
                    if candidate.suffix.lower() in IMAGE_SUFFIXES:
                        embed_image(candidate, f"Selected image {relative_path}")
                        continue
                    if remaining <= 0:
                        text_sections.append("\n\n[Additional selected context was omitted after the 256 KB local context limit]")
                        break
                    read_limit = min(MAX_TEXT_FILE_BYTES, remaining)
                    with candidate.open("rb") as handle:
                        payload = handle.read(read_limit + 1)
                    truncated = len(payload) > read_limit
                    raw = payload[:read_limit]
                    remaining -= len(raw)
                    text = raw.decode("utf-8", errors="replace")
                    marker = (
                        f"\n[Selected context truncated after {len(raw)} bytes; remaining content omitted.]"
                        if truncated
                        else ""
                    )
                    text_sections.append(
                        f"\n\nSelected project context {relative_path}:\n```\n{text}\n```{marker}"
                    )
                except OSError as exc:
                    text_sections.append(f"\n\n[Could not read selected context {relative_path}: {exc}]")
            if len(selected_context) > 12:
                text_sections.append(
                    f"\n\n[{len(selected_context) - 12} additional selected context paths were omitted after the 12-file limit]"
                )
        if text_sections:
            message["content"] = task + "".join(text_sections)
        if images:
            message["images"] = images
        agent_task = task
        if agent_notes:
            agent_task += "\n\nUser-attached local paths:\n" + "\n".join(f"- {path}" for path in agent_notes)
        if selected_context:
            agent_task += "\n\nSelected project context paths:\n" + "\n".join(f"- {path}" for path in selected_context)
        return message, agent_task

    def _set_active(self, kind: Optional[str]) -> None:
        was_running = bool(self.active_kind)
        self.active_kind = kind
        running = bool(kind)
        self.retry_button.hide()
        self.send_button.setText("Stop" if running else "Send")
        self.send_button.setAccessibleName("Stop active run" if running else "Send task")
        if kind == "agent":
            self.send_button.setToolTip(
                "Stop Onionmind Agent; child processes it started may require manual termination. "
                "A typed direction is sent when it stops."
            )
        elif kind == "chat":
            self.send_button.setToolTip(
                "Stop local generation after the current read. A typed direction is sent when it stops."
            )
        else:
            self.send_button.setToolTip("Send task (Enter)")
        self.chat_button.setEnabled(not running)
        self.agent_button.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.search_consent.setEnabled(not running)
        if running:
            self.search_consent.setChecked(False)
        self.left_rail.projects.setEnabled(not running)
        self.left_rail.sessions.setEnabled(not running)
        if not running:
            self.stop_event = None
            self.harness_process = None
        if kind is None and self._pending_redirect:
            self._pending_redirect = False
            if self.composer.toPlainText().strip() or self.attachments:
                # Deferred through the event loop: the finishing run's own
                # handlers are still unwinding on this stack.
                QTimer.singleShot(0, self.submit)
        if kind is None and was_running:
            self._alert_if_unfocused()
        self._sync_action_states()

    def _start_chat(self, allow_search: bool = False) -> None:
        self.tor_in_use = allow_search
        self.stop_event = threading.Event()
        stop_event = self.stop_event
        model = self.current_model_id()
        history = copy.deepcopy(self.chat_messages)
        block = self.transcript.add_message("assistant", "")
        block.start_thinking("Starting Tor" if allow_search else "Thinking")
        self.stream_block = block
        if allow_search:
            self.tor_probe_generation += 1
            self.tor_phase = "starting"
            self.tor_stop_event = stop_event
            self.tor_status.set_status("Checking…", "warn")
            _set_tor_action(self, "Cancel", tooltip="Cancel this Tor startup and Chat turn.")
            self.set_status("Starting background Tor without opening a browser window…")
            self.inspector.append_activity("One-turn Tor search permission granted")
        else:
            self.set_status(f"Thinking with {self._describe_model(model)}…")
            self.inspector.append_activity("Chat turn started on the local-only inference path")

        def chat_job(signals: WorkerSignals) -> dict[str, Any]:
            setattr(self.core, "MODEL", model)
            if allow_search:
                starter = getattr(self.core, "start_tor_hidden", None)
                if not callable(starter):
                    raise RuntimeError("This Onionmind core cannot start background Tor.")
                port = starter(stop_event=stop_event)
                managed = getattr(self.core, "_managed_tor_process", None)
                signals.event.emit({
                    "kind": "tor_ready",
                    "port": port,
                    "managed": managed is not None,
                })
                verified_port = self._verify_tor(stop_event)
                signals.event.emit({"kind": "tor_verified", "port": verified_port})
            if not getattr(self.core, "BACKEND", None):
                detector = getattr(self.core, "detect_backend", None)
                if callable(detector):
                    detector()
            turn_stream = getattr(self.core, "turn_stream", None)
            if not callable(turn_stream):
                raise RuntimeError("The Onionmind core does not expose streaming chat.")
            stream_filter = ThinkingStreamFilter()

            def on_text(chunk: str) -> None:
                if stop_event.is_set():
                    stream_filter.abort()
                    return
                stream_filter.feed(chunk)

            def on_event(event: dict[str, Any]) -> None:
                signals.event.emit(dict(event))

            try:
                try:
                    answer = turn_stream(
                        history,
                        on_text,
                        stop_event=stop_event,
                        on_event=on_event,
                        allow_search=allow_search,
                    )
                except TypeError as exc:
                    if "on_event" not in _as_text(exc):
                        raise
                    try:
                        answer = turn_stream(
                            history,
                            on_text,
                            stop_event=stop_event,
                            allow_search=allow_search,
                        )
                    except TypeError as compatibility_error:
                        if "allow_search" in _as_text(compatibility_error):
                            raise RuntimeError(
                                "The installed Onionmind core is too old to enforce per-turn search permission."
                            ) from compatibility_error
                        raise
            except BaseException:
                if stop_event.is_set():
                    # Cancellation is a normal completion path. Keep the
                    # privacy-filtered text received before Stop was clicked;
                    # do not turn a partial answer into an error message.
                    return {
                        "answer": stream_filter.finish(),
                        "history": history,
                        "stopped": True,
                    }
                stream_filter.abort()
                raise
            if stop_event.is_set():
                # Stop must halt further generation without erasing the
                # already-buffered, sanitized response.
                safe_answer = stream_filter.finish()
                stopped = True
            else:
                buffered_answer = stream_filter.finish()
                returned_answer = _strip_thinking(_as_text(answer))
                safe_answer = returned_answer or buffered_answer
                stopped = False
            return {"answer": safe_answer, "history": history, "stopped": stopped}

        worker = self._start_worker(chat_job)
        worker.signals.event.connect(self._chat_event)
        worker.signals.result.connect(self._chat_complete)
        worker.signals.error.connect(self._chat_failed)

    def _chat_event(self, event: dict[str, Any]) -> None:
        kind = _as_text(event.get("kind"))
        name = _as_text(event.get("name", "local tool"))
        display_name = name.replace("_", " ").strip().title()
        if kind == "tor_ready":
            port = event.get("port")
            self.tor_phase = "starting"
            self.tor_status.set_status("Checking…", "warn")
            _set_tor_action(
                self,
                "Cancel",
                tooltip="Cancel this Tor verification and Chat turn.",
            )
            self.tor_status.setToolTip(
                "A local proxy connection is available; Onionmind is verifying it as Tor. "
                "Click to cancel."
            )
            self.set_status("Verifying the Tor circuit before protected work begins…")
            self.inspector.append_activity("Local SOCKS listener ready; verifying Tor circuit")
        elif kind == "tor_verified":
            port = event.get("port")
            self.tor_phase = "running"
            self.tor_status.set_status("Ready", "good")
            _set_tor_action(
                self,
                "Turn off",
                tooltip=(
                    "Stop Onionmind's background Tor process."
                    if getattr(self.core, "_managed_tor_process", None) is not None
                    else "Disconnect Onionmind from this external proxy; the external process stays running."
                ),
            )
            self.tor_status.setToolTip(
                "Tor is verified and ready. Click to turn it off."
            )
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Thinking")
            self.set_status(f"Tor ready · thinking with {self._describe_model(self.current_model_id())}…")
            self.inspector.append_activity("Background Tor path verified")
        elif kind == "tool_started":
            arguments = event.get("arguments") or {}
            detail = _as_text(arguments.get("query")) if isinstance(arguments, dict) else ""
            state = "Running through Tor · fails closed" if name == "web_search" else "Running locally"
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Searching via Tor" if name == "web_search" else "Using local tool")
            self.transcript.add_tool_card(display_name, [(detail or "Tool request", state)])
            activity = "Tor search started" if name == "web_search" else f"Local tool started: {display_name}"
            self.inspector.append_activity(activity)
        elif kind == "tool_finished":
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Thinking")
            state = "Tor result returned" if name == "web_search" else "Finished"
            self.transcript.add_tool_card(display_name, [("Tool result", state)])
            activity = "Tor search finished" if name == "web_search" else f"Local tool finished: {display_name}"
            self.inspector.append_activity(activity)
        elif kind == "tool_refused":
            self.transcript.add_tool_card(display_name, [("Not run", "No one-turn search permission")])
            self.inspector.append_activity("Tor search request refused; no network request was made")

    def _chat_complete(self, payload: dict[str, Any]) -> None:
        self.tor_in_use = False
        self.tor_stop_event = None
        answer = _strip_thinking(payload.get("answer"))
        stopped = bool(payload.get("stopped"))
        if not answer and stopped:
            answer = "Generation stopped before an answer was written."
        elif not answer:
            answer = "The local model returned no answer."
        if self.stream_block is not None:
            self.stream_block.set_text(answer)
        raw_history = payload.get("history") or [
            *self.chat_messages,
            {"role": "assistant", "content": answer},
        ]
        self.chat_messages = _sanitize_assistant_messages(raw_history)
        if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
            self.chat_messages[-1]["content"] = answer
        if not self.chat_messages or self.chat_messages[-1].get("role") != "assistant":
            self.chat_messages.append({"role": "assistant", "content": answer})
        local_probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = local_probe() if callable(local_probe) else None
        except Exception:
            port = None
        self._show_local_tor_state(port)
        self.set_status("Chat stopped" if stopped else "Chat turn complete")
        self.inspector.append_activity(
            "Chat stopped; partial answer preserved" if stopped else "Chat turn completed locally"
        )
        self._set_active(None)
        self.save_current_session()

    def _chat_failed(self, message: str) -> None:
        self.tor_in_use = False
        self.tor_stop_event = None
        message = _brand_runtime_text(_strip_thinking(message))
        if self.stream_block is not None:
            self.stream_block.set_text(f"Local inference could not continue. {message}")
        self.chat_messages.append({"role": "assistant", "content": f"Local inference failed: {message}"})
        local_probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = local_probe() if callable(local_probe) else None
        except Exception:
            port = None
        self._show_local_tor_state(port)
        self.set_status(f"Local inference failed: {message}")
        self.inspector.append_activity(f"Chat turn failed: {message}")
        self._set_active(None)
        self.retry_button.show()
        self.save_current_session()

    def _start_harness(self, task: str) -> None:
        if not self.workspace:
            block = self.transcript.add_message(
                "assistant", "Agent mode needs a project folder. Open one with Ctrl+O, then send the task again."
            )
            self.stream_block = block
            self.set_status("Agent mode needs a project folder")
            self._set_active(None)
            return
        self.tor_in_use = True
        model = self.current_model_id()
        workspace = self.workspace
        yolo = self.yolo_consent.isChecked()
        self.harness_generation += 1
        generation = self.harness_generation
        self.stream_block = self.transcript.add_message(
            "assistant",
            "Preparing Onionmind Agent…\n\n" + self.harness_bridge.limitation,
            "Onionmind Agent",
        )
        self.set_status("Preparing Onionmind Agent…")

        def prepare(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            available, reason = self.harness_bridge.check()
            if not available:
                return {"available": False, "reason": reason}
            argv, cwd = self.harness_bridge.build(model=model, task=task,
                                                  cwd=workspace, yolo=yolo)
            return {"available": True, "argv": argv, "cwd": cwd}

        worker = self._start_worker(prepare)
        worker.signals.result.connect(
            lambda payload, value=generation: self._harness_prepared(value, payload)
        )
        worker.signals.error.connect(
            lambda message, value=generation: self._harness_start_failed(
                message, value
            )
        )

    def _harness_prepared(self, generation: int, payload: dict[str, Any]) -> None:
        if generation != self.harness_generation or self.active_kind != "agent":
            return
        if not payload.get("available"):
            self.tor_in_use = False
            reason = payload.get("reason") or "Onionmind Agent is unavailable."
            text = _brand_runtime_text(reason) + "\n\n" + self.harness_bridge.limitation
            if self.stream_block is None:
                self.stream_block = self.transcript.add_message("assistant", text, "Onionmind Agent")
            else:
                self.stream_block.set_text(text)
            self.chat_messages.append({"role": "assistant", "content": text})
            self.set_status("Onionmind Agent is unavailable")
            self.inspector.append_activity("Agent unavailable; no repository action started")
            self._set_active(None)
            self.save_current_session()
            return
        argv = list(payload.get("argv") or [])
        cwd = _as_text(payload.get("cwd") or self.workspace)
        if not argv:
            self._harness_start_failed(
                "The Agent adapter returned no executable command.", generation
            )
            return
        self.harness_output = ""
        start_text = (
            "Starting Onionmind Agent…\n"
            + self.harness_bridge.limitation
            + "\n\nAgent output (unverified until disk refresh):\n"
        )
        if self.stream_block is None:
            self.stream_block = self.transcript.add_message("assistant", start_text, "Onionmind Agent")
        else:
            self.stream_block.set_text(start_text)
        self.harness_output = self.stream_block.text
        self.terminal.show()
        self.terminal_toggle.setChecked(True)
        self.terminal.append("\n[agent] Starting Onionmind Agent\n")
        process = QProcess(self)
        self.harness_process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(cwd)
        process.readyReadStandardOutput.connect(self._read_harness_output)
        process.finished.connect(self._harness_finished)
        process.errorOccurred.connect(self._harness_process_error)
        process.start(argv[0], argv[1:])
        self.set_status("Onionmind Agent is working…")
        self.inspector.append_activity("Agent run started; output is not yet proof of disk changes")

    def _read_harness_output(self) -> None:
        if self.harness_process is None:
            return
        chunk = bytes(self.harness_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        clean = _brand_runtime_text(ANSI_ESCAPE.sub("", chunk))
        self.harness_output += clean
        if self.stream_block is not None:
            self.stream_block.append_text(clean)
        self.terminal.append(clean)
        self.transcript._scroll_later()

    def _harness_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self.active_kind != "agent":
            return
        self.tor_in_use = False
        normal = status == QProcess.ExitStatus.NormalExit
        suffix = f"\n\nAgent {'finished' if normal else 'crashed'} with exit code {exit_code}. Refreshing observed disk state."
        if self.stream_block is not None:
            self.stream_block.append_text(suffix)
        content = (self.stream_block.text if self.stream_block is not None else self.harness_output + suffix)
        self.chat_messages.append({"role": "assistant", "content": content})
        self.terminal.append(suffix + "\n")
        self.set_status(f"Agent exited with code {exit_code}; refreshing observed changes…")
        self.inspector.append_activity(f"Agent exited with code {exit_code}; disk inspection requested")
        self._set_active(None)
        self.save_current_session()
        self.refresh_workspace()

    def _harness_process_error(self, error: QProcess.ProcessError) -> None:
        if self.harness_process is None:
            return
        if error != QProcess.ProcessError.FailedToStart:
            return
        self._harness_start_failed(
            self.harness_process.errorString(), self.harness_generation
        )

    def _harness_start_failed(
        self, message: str, generation: Optional[int] = None
    ) -> None:
        if self.active_kind != "agent":
            return
        if generation is not None and generation != self.harness_generation:
            return
        message = _brand_runtime_text(message)
        self.tor_in_use = False
        text = f"Onionmind Agent could not start: {message}\n\n{self.harness_bridge.limitation}"
        if self.stream_block is None:
            self.stream_block = self.transcript.add_message("assistant", text, "Onionmind Agent")
        else:
            self.stream_block.set_text(text)
        self.chat_messages.append({"role": "assistant", "content": text})
        self.set_status(f"Agent could not start: {message}")
        self.inspector.append_activity("Agent failed before a repository action could be verified")
        self._set_active(None)
        self.save_current_session()

    def stop_active(self) -> None:
        if not self.active_kind:
            return
        if self.active_kind == "chat" and self.stop_event is not None:
            self.stop_event.set()
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Stopping")
            self.set_status("Stopping the local model after the current read…")
        elif self.active_kind == "agent":
            if self.harness_process is None:
                self.tor_in_use = False
                self.harness_generation += 1
                text = "Agent start canceled before a repository action began."
                if self.stream_block is not None:
                    self.stream_block.set_text(text)
                self.chat_messages.append({"role": "assistant", "content": text})
                self.inspector.append_activity(text)
                self.set_status(text)
                self._set_active(None)
                self.save_current_session()
            else:
                self.harness_process.terminate()
                QTimer.singleShot(1500, self._kill_harness_if_running)
                self.set_status(
                    "Stopping Onionmind Agent; spawned child processes may require manual termination."
                )

    def _kill_harness_if_running(self) -> None:
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()

    def open_model_manager(self) -> None:
        dialog = ModelManagerDialog(
            self.installed_model_ids,
            self.current_model_id(),
            self._describe_model,
            self,
            reference_normalizer=_optional_callable(self.desktop_core, "normalize_model_reference"),
            marker_for=_optional_callable(self.desktop_core, "uncensored_marker"),
            catalog_entry_label=self._catalog_entry_label,
        )
        self._model_dialog = dialog
        dialog.pullRequested.connect(lambda name: self.pull_model(name, dialog))
        dialog.catalogRequested.connect(lambda: self._fetch_model_catalog(dialog))
        dialog.exec()
        self._model_dialog = None

    def _catalog_entry_label(self, entry: Any) -> str:
        """Name on the first line, a plain description on the second. The
        refusal-removal flag is the dialog's own line, not this one."""
        fit = _optional_callable(self.desktop_core, "catalog_fit")
        describe = _optional_callable(self.desktop_core, "catalog_description")
        model_id = _as_text(_field(entry, "id"))
        fit_label = ""
        if callable(fit):
            vram, ram = self._catalog_specs
            fit_label = fit(model_id, vram, ram)[1]
        if callable(describe):
            return f"{model_id}\n{describe(entry, fit_label)}"
        downloads = _field(entry, "downloads", 0) or 0
        return f"{model_id}\n{downloads} downloads · {fit_label}".rstrip(" ·")

    def _fetch_model_catalog(self, dialog: ModelManagerDialog) -> None:
        """Fetch the popular GGUF list over Tor - fail closed, ordered by what
        this machine's RAM/VRAM can actually run."""
        fetcher = getattr(self.desktop_core, "fetch_hf_catalog", None)
        if not callable(fetcher):
            dialog.set_catalog_error("Model discovery needs the Onionmind desktop core.")
            return
        probe = getattr(self.core, "tor_proxy_port", None)
        try:
            listening = probe() if callable(probe) else None
        except Exception:
            listening = None
        port = self.update_bridge.tor_port()
        if not listening and port is None:
            dialog.set_catalog_error(
                "Tor is not up. Allow Tor search on a chat turn to start it, then "
                "try again - never over a direct connection."
            )
            return

        def job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            if port is None:
                # A listening SOCKS port is not proof of Tor; verify a circuit
                # before trusting it, exactly like the update check.
                verify = getattr(self.core, "tor_check", None)
                if callable(verify):
                    try:
                        verify()
                    except SystemExit as exc:
                        raise RuntimeError(
                            _as_text(exc) or "Tor could not be verified."
                        ) from None
            port_now = port or self.update_bridge.tor_port()
            if port_now is None:
                raise RuntimeError(
                    "The local proxy did not verify as Tor; refusing a direct model-list fetch."
                )
            memory = getattr(self.desktop_core, "machine_memory_mb", None)
            vram_probe = getattr(self.desktop_core, "gpu_vram_mb", None)
            ram = int(memory()) if callable(memory) else None
            vram = int(vram_probe()) if callable(vram_probe) else None
            return {"entries": fetcher(port_now), "vram": vram, "ram": ram}

        def done(payload: dict[str, Any]) -> None:
            self._catalog_specs = (payload.get("vram"), payload.get("ram"))
            fit = _optional_callable(self.desktop_core, "catalog_fit")
            entries = list(payload.get("entries") or [])
            if callable(fit):
                vram, ram = self._catalog_specs
                entries.sort(
                    key=lambda entry: fit(_as_text(_field(entry, "id")), vram, ram)[0]
                )
            dialog.set_catalog(entries)

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(dialog.set_catalog_error)

    def pull_model(self, name: str, dialog: ModelManagerDialog) -> None:
        pull = getattr(self.core, "pull_model", None)
        if not callable(pull):
            dialog.set_error("This Onionmind installation cannot add models yet")
            return
        stop_event = threading.Event()

        def pull_job(signals: WorkerSignals) -> bool:
            def progress(fraction: float, status: str) -> None:
                signals.progress.emit(float(fraction), _as_text(status))

            return bool(pull(name, on_progress=progress, stop_event=stop_event))

        worker = self._start_worker(pull_job)
        worker.signals.progress.connect(dialog.set_progress)
        worker.signals.error.connect(dialog.set_error)
        worker.signals.result.connect(lambda ok: self._model_pull_complete(name, dialog, ok))

    def _model_pull_complete(self, name: str, dialog: ModelManagerDialog, ok: bool) -> None:
        if not ok:
            dialog.set_error("Adding the model was stopped")
            return
        if name not in self.installed_model_ids:
            self.installed_model_ids.append(name)
        current = self.current_model_id()
        self.set_model_options(self.installed_model_ids, current)
        dialog.set_models(self.installed_model_ids, current)
        dialog.set_complete()
        self.inspector.append_activity(f"Onionmind model installed: {self._describe_model(name)}")

    def open_settings(self) -> None:
        SettingsDialog(
            self.data_root, self.harness_bridge.limitation, self.update_bridge, self
        ).exec()

    # --- Tor-routed self-update -----------------------------------------
    #
    # Permission first: the updater never opens a network connection on its
    # own. Automatic checks run only while the user has granted standing
    # permission in Settings (off by default); otherwise the network is
    # touched exclusively by pressing Check for updates or Download and
    # install. With permission granted, checks repeat for as long as the app
    # stays open - not just at startup - and always over a verified circuit.

    UPDATE_CHECK_INTERVAL_HOURS = 12

    def update_permission_enabled(self) -> bool:
        return bool(self.settings_data.get("updates_autocheck_enabled"))

    def set_update_permission(self, enabled: bool) -> None:
        self.settings_data["updates_autocheck_enabled"] = bool(enabled)
        self.settings_bridge.save(self.settings_data)
        if enabled:
            self._start_update_timer()
            # Granting permission is itself permission: check right away when
            # Tor is already verified, instead of waiting out the interval.
            self._maybe_autocheck_updates()
        elif self._update_timer is not None:
            self._update_timer.stop()

    def _start_update_timer(self) -> None:
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.setInterval(self.UPDATE_CHECK_INTERVAL_HOURS * 3600 * 1000)
            self._update_timer.timeout.connect(self._maybe_autocheck_updates)
        self._update_timer.start()

    def _set_update_notice(self, text: Optional[str]) -> None:
        self.update_status.setText(text or "Updates…")
        self.update_status.setProperty("attention", text is not None)
        style = self.update_status.style()
        style.unpolish(self.update_status)
        style.polish(self.update_status)
        self.update_status.setToolTip(
            (text + " Click to open Settings.") if text
            else "Check for a newer Onionmind build over Tor. Nothing is contacted until you ask."
        )

    def _maybe_autocheck_updates(self) -> None:
        if self.demo or not self.update_bridge.available:
            return
        if not self.update_permission_enabled():
            return  # No standing permission means no network, ever.
        if self.update_bridge.tor_port() is None:
            return
        pending = self.update_bridge.pending()
        if pending is not None:
            self.show_update_ready()
            return
        last_check = _as_text(self.settings_data.get("updates_last_check", ""))
        if last_check:
            try:
                checked_at = datetime.fromisoformat(last_check)
                age_hours = (datetime.now() - checked_at).total_seconds() / 3600
                if age_hours < self.UPDATE_CHECK_INTERVAL_HOURS:
                    return
            except ValueError:
                pass  # an unreadable timestamp means "check again", not "never check"
        bridge = self.update_bridge

        def autocheck_job(signals: WorkerSignals) -> Any:
            del signals
            bridge.housekeep()
            return bridge.check()

        def wire_autocheck(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._autocheck_updates_done)
            worker.signals.error.connect(self._autocheck_updates_failed)

        self._start_worker(autocheck_job, wire_autocheck)

    def _autocheck_updates_done(self, manifest: Any) -> None:
        self.note_update_check(manifest)
        helper_state = getattr(self.desktop_core, "update_state", None)
        state = helper_state(self.update_bridge.revision(), manifest) if callable(helper_state) else "unavailable"
        if state != "available":
            self._set_update_notice(None)
            return
        short = getattr(self.desktop_core, "short_revision", lambda value: str(value)[:7])
        label = short(manifest.revision)
        self._set_update_notice(f"Update available · {label}")
        self.inspector.append_activity(
            f"Onionmind update available: revision {label}. The check ran through Tor; install from Settings."
        )

    def _autocheck_updates_failed(self, message: str) -> None:
        # Silent on the surface - a failed background check must not nag - but
        # it stays visible in the activity inspector, which is the honest log.
        self.inspector.append_activity(f"Background update check over Tor failed: {message}")

    def note_update_check(self, manifest: Any) -> None:
        revision = _field(manifest, "revision", None) if manifest is not None else None
        self.settings_data["updates_last_check"] = datetime.now().isoformat(timespec="seconds")
        if revision:
            self.settings_data["updates_seen_revision"] = _as_text(revision)
        self.settings_bridge.save(self.settings_data)

    def show_update_ready(self) -> None:
        self._set_update_notice("Update ready — restart to install")
        self.inspector.append_activity(
            "Onionmind update downloaded, verified, and staged. Restart from Settings to install it."
        )

    def restart_for_update(self, staging: str) -> None:
        try:
            command = self.update_bridge.apply_command(staging)
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the swap helper must
            # outlive this process without holding on to its console.
            creationflags = (0x00000008 | 0x00000200) if os.name == "nt" else 0
            subprocess.Popen(command, creationflags=creationflags, close_fds=True)
        except OSError as exc:
            self.set_status(f"Could not start the update installer: {exc}")
            return
        self.inspector.append_activity("Restarting Onionmind to apply the staged update")
        QApplication.instance().quit()

    def _populate_demo(self) -> None:
        self.set_model_options(["inferno", "blaze", "ember"], "inferno")
        self.model_status.set_status("Local · Ready", "good")
        # Demo state has to agree with itself now that the pill is a control:
        # a label saying Running while the phase says off would offer Start.
        self.tor_phase = "running"
        self.tor_status.set_status("Ready", "good")
        _set_tor_action(
            self,
            "Turn off",
            tooltip="Stop Onionmind's background Tor process.",
        )
        self.workspace = str(Path.home() / "onion" / "leaflink")
        self.repo_label.setText("leaflink")
        self.repo_label.setToolTip(self.workspace)
        self.branch_label.setText("main")
        self.scope_status.setText(self.workspace)
        self.terminal.set_workspace(self.workspace)
        for project in ("membrane", "tor-watch", "ciphernote", "relay", "leaflink"):
            self.left_rail.add_project(str(Path.home() / "onion" / project), select=project == "leaflink")
        demo_sessions = [
            {"id": "s1", "title": "Refactor test helpers", "updated_at": "Today · 10:24"},
            {"id": "s2", "title": "Fix login redirect loop", "updated_at": "Yesterday"},
            {"id": "s3", "title": "Add onion address healthcheck", "updated_at": "Aug 20"},
            {"id": "s4", "title": "Update deps and lint", "updated_at": "Aug 18"},
            {"id": "s5", "title": "Investigate CI failure", "updated_at": "Aug 16"},
            {"id": "s6", "title": "Clarify CONTRIBUTING", "updated_at": "Aug 14"},
        ]
        self.left_rail.set_sessions(demo_sessions, "s1")
        self.transcript.clear()
        self.transcript.add_message(
            "user",
            "The user factory in tests is duplicated. Extract a shared builder in tests/helpers, update the callers, and run the test suite.",
        )
        self.transcript.add_message(
            "assistant",
            "I’ll inspect the existing factories and test conventions, make the smallest shared helper, then run the focused tests before the full suite.",
        )
        self.transcript.add_tool_card(
            "Read project context",
            [("tests/factories/user_factory.rb", "142 lines · OK"), ("tests/support/test_users.rb", "98 lines · OK")],
        )
        self.transcript.add_tool_card("Shell command", [("bundle exec rake test", "512 runs · exit 0")])
        self.transcript.add_message(
            "assistant",
            "All 512 tests passed. The shared UserBuilder now lives in tests/helpers/user_builder.rb and six call sites use it.\n\nObserved after the run: 4 changed files · +192 / -84. Review the actual diff in Changes.",
        )
        self.chat_messages = [
            {
                "role": "user",
                "content": "The user factory in tests is duplicated. Extract a shared builder in tests/helpers, update the callers, and run the test suite.",
            },
            {
                "role": "assistant",
                "content": "I’ll inspect the existing factories and test conventions, make the smallest shared helper, then run the focused tests before the full suite.",
            },
            {
                "role": "assistant",
                "content": "All 512 tests passed. The shared UserBuilder now lives in tests/helpers/user_builder.rb and six call sites use it.\n\nObserved after the run: 4 changed files · +192 / -84. Review the actual diff in Changes.",
            },
        ]
        self.terminal.output.setPlainText(
            "leaflink on main · local runtime 3.2.2\n"
            "> bundle exec rake test\n"
            "Run options: --seed 12345\n\n"
            "512 runs, 1532 assertions, 0 failures, 0 errors, 0 skips\n\n"
            "Coverage report generated for RSpec to coverage/.\n"
            "leaflink on main> "
        )
        demo_snapshot = {
            "root": self.workspace,
            "is_git": True,
            "branch": "main",
            "dirty": True,
            "agents_files": ["AGENTS.md"],
            "file_tree": [
                "AGENTS.md", "README.md", "app/models/user.rb", "tests/helpers/user_builder.rb",
                "tests/models/user_test.rb", "tests/controllers/users_controller_test.rb", "tests/factories/user_factory.rb",
            ],
            "tree_truncated": False,
            "summary": "4 observed changes · +192 / -84",
            "changes": [
                {"status": "M", "path": "tests/factories/user_factory.rb"},
                {"status": "M", "path": "tests/models/user_test.rb"},
                {"status": "M", "path": "tests/controllers/users_controller_test.rb"},
                {"status": "??", "path": "tests/helpers/user_builder.rb"},
            ],
            "diff": (
                "diff --git a/tests/factories/user_factory.rb b/tests/factories/user_factory.rb\n"
                "index 7e1a0c9..d4b32bf 100644\n"
                "--- a/tests/factories/user_factory.rb\n"
                "+++ b/tests/factories/user_factory.rb\n"
                "@@ -1,8 +1,5 @@\n"
                "-def build_user(overrides = {})\n"
                "-  User.new(default_user.merge(overrides))\n"
                "-end\n"
                "+require_relative '../helpers/user_builder'\n"
                "+include UserBuilder\n"
            ),
        }
        self.current_snapshot = demo_snapshot
        self.inspector.update_snapshot(demo_snapshot)
        self.inspector.append_activity("Observed Git state refreshed after Agent exit")
        self.inspector.append_activity("Agent finished with exit code 0")
        self.inspector.append_activity("Background Tor state and local model readiness reported separately")
        self.set_mode("agent")
        self.set_status("Ready · 4 observed changes · all inference local")
        self._sync_action_states()
        QTimer.singleShot(0, lambda: self.transcript.verticalScrollBar().setValue(0))

    def closeEvent(self, event: Any) -> None:
        self._save_window_layout()
        self._remember_draft()
        self.save_current_session()
        timer = getattr(self, "tor_liveness_timer", None)
        if timer is not None:
            timer.stop()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()
        self.terminal.stop()
        if self.preferences.get("clear_on_exit", False):
            # Last, so it takes what this very close just wrote with it.
            self.wipe_machine_data()
        stop_tor = getattr(self.core, "stop_managed_tor", None)
        if callable(stop_tor):
            try:
                stop_tor()
            except Exception:
                pass
        event.accept()


def _load_desktop_core() -> Any:
    try:
        return importlib.import_module("onionmind_desktop_core")
    except (ImportError, ModuleNotFoundError):
        return None


def run(core_module: Any = None, demo: bool = False) -> int:
    """Run the standalone native Onionmind workbench."""
    if core_module is None:
        core_module = importlib.import_module("onionmind")
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv[:1])
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Onionmind")
    app.setStyle("Fusion")
    _register_system_fonts(app)
    app.setStyleSheet(STYLE_SHEET)
    window = OnionmindWindow(core_module, _load_desktop_core(), demo=demo)
    app.setProperty("onionmindWindow", window)
    window.show()
    _apply_native_dark_title_bar(window)
    # A system scheme flip (scheduled dark mode, Settings) must not re-light
    # the only surface the app does not paint itself.
    style_hints = app.styleHints()
    if hasattr(style_hints, "colorSchemeChanged"):
        style_hints.colorSchemeChanged.connect(
            lambda _scheme: _apply_native_dark_title_bar(window)
        )
    if _SCREENSHOT_PATH:
        window.resize(1440, 900)

        def capture() -> None:
            path = Path(_SCREENSHOT_PATH).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            ok = window.grab().save(str(path))
            print(f"Screenshot {'saved' if ok else 'failed'}: {path}")
            app.exit(0 if ok else 2)

        QTimer.singleShot(450, capture)
    if owns_app:
        return app.exec()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onionmind native local AI workbench")
    parser.add_argument("--demo", action="store_true", help="Show deterministic populated demo state")
    parser.add_argument("--screenshot", metavar="PATH", help="Save a deterministic demo screenshot and exit")
    args = parser.parse_args(argv)
    global _SCREENSHOT_PATH
    _SCREENSHOT_PATH = args.screenshot
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return run(demo=args.demo or bool(args.screenshot))


if __name__ == "__main__":
    raise SystemExit(main())
DESKTOPUIEOF
chmod 644 "$DIR/onionmind_desktop_core.py" "$DIR/onionmind_desktop.py"

DESKTOP_ENV="$DIR/desktop-env"
DESKTOP_MARKER="$DESKTOP_ENV/.onionmind-desktop-ready"
rm -f -- "$DESKTOP_MARKER"
say "Preparing native desktop runtime"
if python3 -m venv "$DESKTOP_ENV"; then
  if ! "$DESKTOP_ENV/bin/python" -m pip install --quiet --disable-pip-version-check \
      requests PySocks PySide6-Essentials==6.11.1; then
    warn "native desktop dependency download failed; checking the existing environment"
  fi
  if "$DESKTOP_ENV/bin/python" -c 'import requests, socks, PySide6.QtWidgets'; then
    : > "$DESKTOP_MARKER"
  else
    warn "native desktop dependencies could not be imported; the classic UI remains available"
  fi
else
  warn "could not create the native desktop environment; the classic UI remains available"
fi

# --- 9. Desktop launcher -----------------------------------------------------
# .desktop files take SVG icons directly, unlike Windows shortcuts. build.py
# injects logo.svg verbatim below - it stays the single source.
say "Creating desktop launcher"
cat > "$DIR/logo.svg" <<'SVGEOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"
     fill="none" stroke="#7D4698" role="img" aria-label="Onionmind">
  <!-- Onion cross-section: each ring is both an onion layer and a Tor hop.
       The gaps rotate inward, tracing a route through the layers to the core.
       #7D4698 is Tor's purple - an onion-routed tool should wear it. A fixed
       colour, not currentColor: GitHub renders this as an <img>, where there
       is no text colour to inherit and the mark silently turns black. -->
  <g stroke-width="7" stroke-linecap="round">
    <circle cx="64" cy="64" r="52" stroke-dasharray="290.4 36.3" transform="rotate(-104 64 64)" opacity=".35"/>
    <circle cx="64" cy="64" r="40" stroke-dasharray="223.4 27.9" transform="rotate(-32 64 64)"  opacity=".55"/>
    <circle cx="64" cy="64" r="28" stroke-dasharray="156.4 19.5" transform="rotate(40 64 64)"   opacity=".78"/>
    <circle cx="64" cy="64" r="16" stroke-dasharray="89.3 11.2"  transform="rotate(112 64 64)"/>
  </g>
  <!-- the mind at the centre: local, and the only thing that never leaves -->
  <circle cx="64" cy="64" r="6" fill="#7D4698" stroke="none"/>
</svg>
SVGEOF
chmod 644 "$DIR/logo.svg"

# `onionmind` is the way in: one system-wide command for chat + Tor search.
# ollama stays underneath as the server - it stops being something you type.
# Render shell-safe literals once; generated launchers still expand their own
# runtime arguments and never interpolate the install path through sed.
DIR_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$DIR")
MODEL_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$MODEL_NAME")
# The generated launcher nudges the local tor service awake if it is not
# running. The right command differs per platform; neither starts Tor Browser.
if [ "$DISTRO" = mac ]; then
  TOR_NUDGE='nc -z 127.0.0.1 9050 2>/dev/null || echo "[tor] not running - search will refuse until: brew services start tor"'
else
  TOR_NUDGE='systemctl is-active --quiet tor 2>/dev/null || sudo -n systemctl start tor 2>/dev/null || echo "[tor] not running - search will refuse until: sudo systemctl start tor"'
fi
sudo tee /usr/local/bin/onionmind >/dev/null <<LAUNCH
#!/bin/sh
DIR=$DIR_LITERAL
$TOR_NUDGE
cd "\$HOME"             # /save <file> lands here
PYTHON="\$DIR/desktop-env/bin/python"
[ -x "\$PYTHON" ] && [ -f "\$DIR/desktop-env/.onionmind-desktop-ready" ] || PYTHON=python3
if [ "\$#" -eq 0 ]; then
  exec "\$PYTHON" "\$DIR/onionmind.py" --ui
else
  exec "\$PYTHON" "\$DIR/onionmind.py" "\$@"
fi
LAUNCH
sudo chmod 755 /usr/local/bin/onionmind

# Repository-aware coding agent, kept separate from Onionmind's local Tor chat.
DSH_REVISION=$(curl -fsSL 'https://api.github.com/repos/Codemaster64/onionmind/commits/main' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$DSH_REVISION" =~ ^[0-9a-fA-F]{40}$ ]] || die "could not resolve a fixed DSH asset revision"
DSH_BASE="https://raw.githubusercontent.com/Codemaster64/onionmind/$DSH_REVISION"
curl -fsSL "$DSH_BASE/dsh-onionmind-tor-search.js" \
  -o "$DIR/dsh-onionmind-tor-search.js"
curl -fsSL "$DSH_BASE/dsh-onionmind-tor.patch.yml" \
  -o "$DIR/dsh-onionmind-tor.patch.yml"
python3 - "$DIR/dsh-onionmind-tor.patch.yml" "$DIR/dsh-onionmind-tor-search.js" <<'PATCHPY'
from pathlib import Path
import sys

patch = Path(sys.argv[1])
plugin = Path(sys.argv[2]).as_posix().replace("'", "''")
text = patch.read_text(encoding="utf-8")
if "@ONIONMIND_DSH_PLUGIN@" not in text:
    raise SystemExit("DSH patch placeholder is missing")
patch.write_text(text.replace("@ONIONMIND_DSH_PLUGIN@", plugin), encoding="utf-8")
PATCHPY

sudo tee /usr/local/bin/onionmind-code >/dev/null <<AGENT
#!/bin/sh
DIR=$DIR_LITERAL
MODEL=$MODEL_LITERAL
export ONIONMIND_PY="\$DIR/onionmind.py"
export ONIONMIND_PYTHON=python3
if [ "\$#" -eq 0 ]; then
  echo 'usage: onionmind-code "task for the coding agent"' >&2
  exit 2
fi
NODE_VERSION=\$(node --version 2>/dev/null) || {
  echo 'DeepSeek Harness requires Node.js ^22.19 or 24+. Install a supported Node.js release first.' >&2
  exit 1
}
NODE_VERSION=\${NODE_VERSION#v}
NODE_MAJOR=\${NODE_VERSION%%.*}
NODE_REST=\${NODE_VERSION#*.}
NODE_MINOR=\${NODE_REST%%.*}
case "\$NODE_MAJOR:\$NODE_MINOR" in
  *[!0-9:]*|:) echo "Could not read the installed Node.js version: \$NODE_VERSION" >&2; exit 1 ;;
esac
if [ "\$NODE_MAJOR" -lt 24 ] && { [ "\$NODE_MAJOR" -ne 22 ] || [ "\$NODE_MINOR" -lt 19 ]; }; then
  echo "DeepSeek Harness requires Node.js ^22.19 or 24+; found \$NODE_VERSION." >&2
  exit 1
fi
# The agent's only way off this machine is Tor: onionmind.py verifies the
# circuit, puts every child on it, and refuses to start without one.
exec python3 "\$DIR/onionmind.py" --agent --model "\$MODEL" "\$*"
AGENT
sudo chmod 755 /usr/local/bin/onionmind-code

sudo tee /usr/local/bin/onionmind-update >/dev/null <<UPDATE
#!/bin/sh
DIR=$DIR_LITERAL
tmp=\$(mktemp) || exit 1
trap 'rm -f "\$tmp"' EXIT
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.sh -o "\$tmp" || exit 1
bash "\$tmp" --install-dir "\$DIR"
status=\$?
rm -f "\$tmp"
trap - EXIT
exit "\$status"
UPDATE
sudo chmod 755 /usr/local/bin/onionmind-update

if [ "$DISTRO" != mac ]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/onionmind.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Onionmind
Comment=Native local AI coding workbench with Tor search
Exec=onionmind --ui
Icon=$DIR/logo.svg
Terminal=false
Categories=Network;Utility;
DESK
  # desktop icon only if the DE actually has a Desktop dir
  DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
  if [ -d "$DESKTOP_DIR" ]; then
    cp "$HOME/.local/share/applications/onionmind.desktop" "$DESKTOP_DIR/onionmind.desktop"
    chmod +x "$DESKTOP_DIR/onionmind.desktop"
  fi
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo
say "Ready"
echo "  Desktop:     onionmind   (native local workbench)"
echo "  Coding:      onionmind-code \"task\"   (headless agent; web over Tor only)"
echo "  Updates:     onionmind-update   (code only, model untouched)"
[ "$VISION" = 1 ] && echo "  Images:      ollama run $MODEL_NAME-vision   (then give it an image path)"
echo "  Web search:  $DIR/onionmind.py \"your question\""
if [ "$DISTRO" = mac ]; then
  echo "  Launcher:    Spotlight -> onionmind   (no .app bundle yet; command first)"
else
  echo "  Shortcut:    Onionmind - double-click to open the workbench"
fi
