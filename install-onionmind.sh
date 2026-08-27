#!/usr/bin/env bash
# Qwen3.8-27B uncensored + Tor web search, on Ollama. Arch and Ubuntu/Debian. One paste.
# Re-runnable: skips what is already done and resumes partial downloads.
set -euo pipefail

# Weights live outside $HOME deliberately. Ollama runs as User=ollama under systemd:
#   - Arch's unit sets ProtectHome=yes, making /home structurally invisible to the
#     service. A ~/ path fails with a bare "no such file" and no chmod fixes it.
#   - Ubuntu's vendor unit has no ProtectHome, but home dirs are often mode 750.
# /var/lib is writable and visible on both (Arch's ProtectSystem=full only locks
# /usr /boot /etc), so one path works everywhere.
DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root - it calls sudo where needed"
command -v systemctl >/dev/null || die "needs systemd"

if   command -v pacman  >/dev/null 2>&1; then DISTRO=arch
elif command -v apt-get >/dev/null 2>&1; then DISTRO=debian
else die "unsupported distro - needs pacman or apt-get"; fi
say "Distro family: $DISTRO"

# --- 1. GPU -----------------------------------------------------------------
VRAM=0
if command -v nvidia-smi >/dev/null 2>&1; then
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
if [ "$DISTRO" = arch ]; then
  case $GPU in nvidia) OLLAMA_PKG=ollama-cuda ;; amd) OLLAMA_PKG=ollama-rocm ;; *) OLLAMA_PKG=ollama ;; esac
  PKGS=(tor python-requests python-pysocks python-tk curl "$OLLAMA_PKG")
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
# and systemd restarts it.
systemctl is-active --quiet tor || { say "Starting tor"; sudo systemctl enable --now tor; }
tor_up() { (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null && { exec 3<&- 3>&-; return 0; }; return 1; }
for _ in $(seq 1 40); do tor_up && break; sleep 2; done
if tor_up; then say "Tor SOCKS up on 9050"
else warn "tor not listening - 'systemctl status tor'; search will refuse until it is"; fi

# --- 4. Ollama tuning + service --------------------------------------------
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

for _ in $(seq 1 40); do
  curl -sf --noproxy '*' -m 3 http://127.0.0.1:11434/api/version >/dev/null && break
  sleep 2
done
curl -sf --noproxy '*' -m 3 http://127.0.0.1:11434/api/version >/dev/null \
  || die "ollama did not come up on 11434 ('journalctl -u ollama -n50' to see why)"

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
sudo mkdir -p "$DIR"
sudo chown "$(id -u):$(id -g)" "$DIR"
sudo chmod 755 "$DIR"                  # traversable by the ollama service user

# Huggingface publishes each LFS object's sha256 in X-Linked-ETag, so a 16GB
# download can be checked against the digest the host itself serves - no hash
# table to maintain here. Integrity only, not supply-chain pinning: it catches
# the truncated or corrupted file that otherwise shows up much later as an
# inscrutable model-load error. ONIONMIND_SKIP_VERIFY=1 opts out.
verify() {  # file url
  [ "${ONIONMIND_SKIP_VERIFY:-0}" = 1 ] && return 0
  command -v sha256sum >/dev/null 2>&1 || return 0
  want=$(curl -fsSLI "$2" 2>/dev/null | tr -d '\r' | awk 'tolower($1) == "x-linked-etag:" {gsub(/"/, "", $2); print $2}' | tail -1)
  case "$want" in
    *[!0-9a-f]* | "") return 0 ;;      # nothing published; nothing to check
  esac
  got=$(sha256sum "$1" | cut -d' ' -f1)
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
import sys, os, re, html, json, secrets, socket, socketserver, threading, time, urllib.parse, requests

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
# DuckDuckGo's onion service. Preferred over the clearnet endpoint for two reasons:
# it never leaves the Tor network (no exit node sees the query at all), and the
# clearnet endpoint returns 403 to most Tor exits, which looks like "search is broken".
ENDPOINTS = ("https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/",
             "https://html.duckduckgo.com/html/")
# Reasoning models spend the budget thinking BEFORE answering. A 9B needed 5514 tokens
# to reach its first word; capped lower it returns an empty string, which reads as a
# refusal but is just truncation.
NUM_PREDICT = 16384
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


def _proxies(port, isolate):
    # Distinct SOCKS credentials => Tor builds a SEPARATE circuit. Without this every
    # search shares one exit node and they can be trivially linked to each other.
    cred = f"{secrets.token_hex(8)}:x@" if isolate else ""
    return {s: f"socks5h://{cred}127.0.0.1:{port}" for s in ("http", "https")}
    # socks5h (not socks5) also resolves DNS through Tor; plain socks5 leaks every hostname.


def tor_check():
    """Pin the Tor port, or exit. Fails closed - never falls back to a direct connection."""
    global _port
    for port in PORTS:
        try:
            r = requests.get("https://check.torproject.org/api/ip",
                             proxies=_proxies(port, False), timeout=30).json()
        except Exception:
            continue
        if r.get("IsTor"):
            _port = port
            print(f"[tor] active, exit {r.get('IP')} (port {port})", file=sys.stderr)
            return
        print(f"[tor] port {port} responded but is NOT Tor - refusing", file=sys.stderr)
    sys.exit("No Tor proxy on 9050/9150. Try: sudo systemctl start tor")


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
    if not _port:
        # Without this the proxy URL is "...:None" and the model is told the search
        # broke on a parse error rather than on Tor being down. Fails closed either way.
        return "(search unavailable: no verified Tor proxy this session)"
    err = None
    for url in ENDPOINTS:                        # onion first, clearnet as fallback
        for _ in range(2):                       # each attempt gets a fresh circuit
            try:
                resp = requests.post(url, data={"q": query}, headers={"User-Agent": UA},
                                     proxies=_proxies(_port, True), timeout=90)
                resp.raise_for_status()
            except Exception as e:
                err = e
                continue
            hits = parse_results(resp.text, n)
            if hits:                             # empty 200 == rate-limited or reshaped
                print(f"[tor] searched {query!r} -> {len(hits)} results", file=sys.stderr)
                return "\n".join(f"- {t}\n  {s}\n  {u}" for t, s, u in hits)
            err = "empty result page"
    print(f"[tor] search failed for {query!r}: {err}", file=sys.stderr)
    return f"(search failed after trying both endpoints on fresh circuits: {err})"


def strip_thinking(text):
    """Return the answer, or '' if the model never finished thinking.

    Splitting on '</think>' alone silently returns the raw monologue when the tag
    is missing, so a truncated reply looks like a real answer.
    """
    if "</think>" in text:
        return text.split("</think>")[-1].strip()
    if "<think>" in text:
        return ""                                # ran out of budget mid-thought
    return text.strip()


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


def _ask_ollama(messages, num_predict=NUM_PREDICT, think=None, allow_tools=True):
    body = {"model": MODEL, "messages": _wire_messages(messages), "stream": False,
            "options": {"num_predict": num_predict}}
    if allow_tools:
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
                       think=None, allow_tools=True):
    """Stream one Ollama response while retaining tool-call compatibility."""
    body = {"model": MODEL, "messages": _wire_messages(messages), "stream": True,
            "options": {"num_predict": num_predict}}
    if allow_tools:
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
    """Keep implementation names out of the product-facing desktop UI."""
    return (str(exc).replace("Ollama", "model service")
            .replace("ollama", "model service")
            .replace("Qwen3.8", "INFERNO")
            .replace("Qwen3.5", "MODEL"))


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


def _ask_llama(messages, num_predict=NUM_PREDICT, think=None, allow_tools=True):
    body = {"messages": _to_openai(messages), "stream": False,
            "max_tokens": num_predict}
    if allow_tools:
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
                               think=False, allow_tools=False)
    elif on_text is not None:
        recovered = _ask_ollama_stream(
            recovery_history, on_text, stop_event, num_predict=FINAL_NUM_PREDICT,
            think=False, allow_tools=False)
    else:
        recovered = _ask_ollama(recovery_history, num_predict=FINAL_NUM_PREDICT,
                                think=False, allow_tools=False)

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


def turn(messages, stop_event=None):
    """Run one user turn to completion, letting the model search as often as it needs."""
    for _ in range(6):                            # ponytail: hard cap, not a retry policy
        if stop_event is not None and stop_event.is_set():
            return "(stopped)"
        msg = _ask_llama(messages) if BACKEND == "llama-server" else _ask_ollama(messages)
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
            result = web_search(args.get("query", "")) if fn["name"] == "web_search" \
                     else f"(unknown tool {fn['name']})"
            messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
    return "(gave up after 6 tool rounds)"


def turn_stream(messages, on_text, stop_event=None, on_event=None):
    """Run a turn with live text and optional structured tool activity.

    The extra callback is deliberately optional so existing CLI, installer, and
    Android callers keep the same interface.  Native desktop clients can use it
    to render real tool state without scraping transcript text.
    """
    if BACKEND != "ollama":
        return turn(messages, stop_event)
    for _ in range(6):
        if stop_event is not None and stop_event.is_set():
            return "(stopped)"
        msg = _ask_ollama_stream(messages, on_text, stop_event)
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
            if on_event:
                on_event({"kind": "tool_started", "name": fn.get("name", "unknown"),
                          "arguments": args})
            result = web_search(args.get("query", "")) if fn["name"] == "web_search" \
                     else f"(unknown tool {fn['name']})"
            if on_event:
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
# The installed chat model is num_ctx 8192 (install-onionmind.ps1) - fine for a
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
        # local port that forwards to the clearnet would ride exactly this path
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
    so "it searched the clearnet by accident" is not a reachable state.
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
    blanked because an inherited one is a hole straight to the clearnet - undici,
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
DSH_PATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "dsh-onionmind-tor.patch.yml")


def agent_argv(model=None, task=None, executable="ollama"):
    """The harness command. Its network boundary is agent_env(), not this line.

    ponytail: dsh-onionmind-tor.patch.yml would point the harness's OWN search
    provider at Tor, but ollama's launcher rejects --patch today and rejecting it
    means no agent at all. Set ONIONMIND_DSH_PATCH=1 once upstream accepts it.
    Nothing leaks meanwhile: that provider is a node http client, so the proxy
    and the socket shims put it on Tor like everything else the agent runs.
    """
    argv = [executable, "launch", "dsh", "--model", model or MODEL, "--"]
    if os.environ.get("ONIONMIND_DSH_PATCH") == "1" and os.path.exists(DSH_PATCH):
        argv += ["--patch", DSH_PATCH]
    if task:                                     # no task = interactive session
        argv += ["--profile", "headless", task]
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
    tor_check()                                  # fails closed before anything starts
    env = dict(os.environ if env is None else env)
    # The search plugin shells back into this file; without these it silently
    # reports itself unavailable and the harness falls back to its own provider.
    env["ONIONMIND_PY"] = os.path.abspath(__file__)
    env["ONIONMIND_PYTHON"] = sys.executable or ("python" if os.name == "nt" else "python3")
    env.update(_proxy_env(f"http://127.0.0.1:{start_tor_bridge()}"))
    return _contain_env(env)


def run_agent(task=None, model=None):
    """Run the coding agent over Tor and return its exit code."""
    import subprocess

    env = agent_env()                            # SystemExit if Tor is not verified
    print("[onionmind] agent web: Tor only. Its search goes over Tor, everything it")
    print("[onionmind]      runs inherits the proxy, and its python and node may only")
    print("[onionmind]      open loopback sockets. Refuses to start when Tor is down.")
    print(f"[onionmind]      Everything it sends out is logged to {NET_LOG}")
    print()
    return subprocess.call(agent_argv(model, task), env=env)


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


def run_code(workdir, ctx=None):
    """Qwen Code on the local model, with Tor the only way out, in a real terminal."""
    import shutil
    import subprocess

    ctx = ctx or code_ctx()
    qwen = shutil.which("qwen")
    if not qwen:
        sys.exit("Qwen Code is missing. Install it with:\n"
                 "  npm install -g @qwen-code/qwen-code")
    # CreateProcess cannot execute the .cmd shim npm writes on Windows.
    launch = ([os.environ.get("COMSPEC", "cmd.exe"), "/c", qwen]
              if qwen.lower().endswith((".cmd", ".bat")) else [qwen])

    detect_backend()
    tor_check()                                  # fails closed before anything starts
    proxy = f"http://127.0.0.1:{start_tor_bridge()}"
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
    env = os.environ.copy()
    env.update(OPENAI_API_KEY="onionmind", OPENAI_MODEL=model, OPENAI_BASE_URL=base)
    env.update(_proxy_env(proxy))
    _contain_env(env)                            # refuse sockets that skip the proxy

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
    hint = tk.Label(actions, text="Answers stay on this PC · searches use Tor",
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
        busy = True
        stop_event.clear()
        send.configure(state="disabled")
        stop.configure(state="normal")
        set_status("thinking…", dim)
        stream_begin()

        def work():
            nonlocal busy
            try:
                answer = turn_stream(
                    history,
                    lambda chunk: root.after(0, lambda chunk=chunk: stream_update(chunk)),
                    stop_event)
            except (Exception, SystemExit) as exc:
                answer = "Error: " + user_error(exc)
            root.after(0, lambda: stream_finish(answer))
            busy = False
            root.after(0, lambda: send.configure(state="normal"))
            root.after(0, lambda: stop.configure(state="disabled"))
            root.after(0, lambda: set_status("ready", "#9ef0b0"))

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
        tor_check()
        print(web_search(query), end="")
        raise SystemExit
    if "--mcp" in sys.argv:
        run_mcp()
        raise SystemExit
    if "--code" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--code"]
        if "--model" in rest:
            i = rest.index("--model")
            MODEL = rest.pop(i + 1)
            rest.pop(i)
        run_code(os.path.abspath(rest[0] if rest else os.getcwd()))
        raise SystemExit
    if "--agent" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--agent"]
        model = None
        if len(args) >= 2 and args[0] == "--model":
            model, args = args[1], args[2:]      # task is everything after it
        raise SystemExit(run_agent(" ".join(args).strip() or None, model))
    if "--ui" in sys.argv:
        run_ui()
        raise SystemExit
    detect_backend()
    tor_check()
    history = []
    if len(sys.argv) > 1:
        history.append({"role": "user", "content": " ".join(sys.argv[1:])})
        print("\n" + turn(history))
    else:
        # AI Act Art. 50(1): the interface itself must say it is an AI.
        print("You are talking to an AI. It can be wrong; you are responsible for what you do with the output.")
        print("Chat - it searches over Tor when it needs to. /save <file> exports the")
        print("conversation. Ctrl-C to quit.\n")
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
            if q:
                history.append({"role": "user", "content": q})
                print("\n" + turn(history) + "\n")
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
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping
from uuid import uuid4


__all__ = [
    "ONIONMIND_TIERS",
    "ModelDisplay",
    "describe_model",
    "model_displays",
    "SettingsStore",
    "ChatSession",
    "SessionStore",
    "WorkspaceChange",
    "WorkspaceSnapshot",
    "WorkspaceInspector",
    "HARNESS_LIMITATION",
    "HarnessAvailability",
    "HarnessCommand",
    "HarnessSpec",
    "parse_terminal_command",
]


PathInput = str | os.PathLike[str]


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
    canonical = tier is not None and raw_id.casefold() == tier.casefold()
    display_name = tier if canonical else f"{tier} · {raw_id}" if tier else raw_id
    return ModelDisplay(raw_id=raw_id, tier=tier, display_name=display_name, tag=tag)


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
            "messages": copy.deepcopy(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _message_dicts(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"message {index} must be a mapping")
        copied.append(copy.deepcopy(dict(message)))
    return copied


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
        _atomic_write_json(target, normalized.to_dict())

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


HARNESS_LIMITATION = (
    "Onionmind Agent is an early-access local coding workflow. It starts in the "
    "selected working directory, while its own tools govern what it can access. "
    "Interactive approval prompts are not available in this build, so protected "
    "actions stop safely. The agent reaches the web only through Tor: it verifies "
    "a circuit before it starts and refuses to run without one."
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
    QAction,
    QColor,
    QDesktopServices,
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
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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
    QStackedWidget,
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


STYLE_SHEET = r"""
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
QLabel { background: transparent; }
QLabel#brand { color: #f5efe7; font-size: 11pt; font-weight: 650; }
QLabel#sectionTitle { color: #aaa39a; font-size: 8.5pt; font-weight: 650; }
QLabel#muted, QLabel#meta, QLabel#disclosure { color: #aaa39a; }
QLabel#title { color: #f2ece4; font-weight: 650; }
QLabel#avatarUser { background: #74579a; color: #f9f5ef; border-radius: 16px; font-weight: 650; }
QLabel#avatarAssistant { background: #282327; color: #b791c9; border: 1px solid #624c6c; border-radius: 16px; font-weight: 700; }
QLabel#attachmentLabel { color: #cdbbd5; background: #2c252e; border: 1px solid #493d4e; border-radius: 3px; padding: 3px 7px; }
QLabel#success { color: #84c08f; }
QLabel#danger { color: #d88675; }
QLabel#accent { color: #c3a1d3; }
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
    font-size: 9pt;
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
QScrollBar:vertical { background: #191816; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #49443e; min-height: 30px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #5b554e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #191816; height: 10px; }
QScrollBar::handle:horizontal { background: #49443e; min-width: 30px; border-radius: 4px; margin: 2px; }
QToolTip { background: #2a2825; color: #f2ece4; border: 1px solid #514c45; padding: 5px; }
"""


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


def _friendly_error(core: Any, exc: BaseException) -> str:
    text = _as_text(exc) or exc.__class__.__name__
    helper = getattr(core, "user_error", None)
    if callable(helper):
        try:
            text = _as_text(helper(exc)) or text
        except Exception:
            pass
    return _brand_runtime_text(text)


def _icon(name: str, size: int = 18) -> QIcon:
    """Render Onionmind's compact, platform-neutral monochrome icon language."""
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor("#c9c1b7"))
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
        painter.setBrush(QColor("#c9c1b7"))
        painter.drawRoundedRect(QRectF(5, 5, 8, 8), 1.2, 1.2)
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
    """Register platform fonts and keep the platform's proportional UI size."""
    system_point_size = app.font().pointSizeF()
    candidates = [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/consolab.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
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


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    text = Signal(str)
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


# Tor Browser owns the SOCKS port on Windows/macOS; Linux usually has a tor daemon.
# Same candidate list the installer walks, so the button starts what the installer set up.
_TOR_BROWSER_PATHS = (
    "{home}/Desktop/Tor Browser/Browser/firefox.exe",
    "{home}/OneDrive/Desktop/Tor Browser/Browser/firefox.exe",
    "{local}/Tor Browser/Browser/firefox.exe",
    "{local}/Programs/Tor Browser/Browser/firefox.exe",
    "C:/Program Files/Tor Browser/Browser/firefox.exe",
    "C:/Program Files (x86)/Tor Browser/Browser/firefox.exe",
    "/Applications/Tor Browser.app/Contents/MacOS/firefox",
)


def _tor_launch_command() -> Optional[list[str]]:
    """The command that brings a SOCKS proxy up, or None if nothing is installed."""
    daemon = shutil.which("tor")
    if daemon and sys.platform not in ("win32", "darwin"):
        return [daemon]
    home = Path.home().as_posix()
    local = Path(os.environ.get("LOCALAPPDATA", home)).as_posix()
    for template in _TOR_BROWSER_PATHS:
        candidate = Path(template.format(home=home, local=local))
        if candidate.exists():
            return [str(candidate)]
    return [daemon] if daemon else None


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


class ComposerEdit(QTextEdit):
    sendRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAcceptDrops(True)
        self.setAccessibleName("Task composer")
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
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
        self.body.setAccessibleName(f"{who.text()} message")
        column.addWidget(self.body)
        outer.addLayout(column, 1)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.body.setText(text)
        self._sync_body_height()

    def append_text(self, text: str) -> None:
        self._text += text
        self.body.setText(self._text)
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

    def create(self, title: str, model: str, workspace: Optional[str], messages: Iterable[dict[str, Any]]) -> Any:
        if self.store is not None:
            return self.store.create(title=title, model=model, workspace=workspace, messages=tuple(messages))
        now = _now_iso()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "model": model,
            "workspace": workspace,
            "messages": list(messages),
            "created_at": now,
            "updated_at": now,
        }

    def list(self) -> list[Any]:
        if self.store is not None:
            try:
                return list(self.store.list())
            except Exception:
                return []
        try:
            return list(json.loads(self.fallback.value("sessions", "[]")))
        except (TypeError, ValueError):
            return []

    def save(self, session: Any, *, title: str, model: str, workspace: Optional[str], messages: list[dict[str, Any]]) -> Any:
        if self.store is not None:
            if dataclasses.is_dataclass(session):
                session = dataclasses.replace(
                    session,
                    title=title,
                    model=model,
                    workspace=workspace,
                    messages=list(messages),
                )
            else:
                for key, value in {
                    "title": title,
                    "model": model,
                    "workspace": workspace,
                    "messages": tuple(messages),
                }.items():
                    setattr(session, key, value)
            return self.store.save(session)
        payload = dict(session)
        payload.update(
            title=title,
            model=model,
            workspace=workspace,
            messages=list(messages),
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
        "Onionmind Agent is an early-access local coding workflow. Interactive approval "
        "prompts are not available in this build, so protected actions stop safely. "
        "The agent reaches the web only through Tor and does not start without it."
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

    def _launcher(self, model: str, task: str) -> Optional[list[str]]:
        """``onionmind.py --agent``: the one place Tor is verified and enforced.

        Launching the harness directly would inherit this window's environment,
        which has no proxy and no socket containment - the agent would have
        direct web access whether Tor is up or not.
        """
        script = _as_text(getattr(self.core, "__file__", ""))
        if not script or not callable(getattr(self.core, "run_agent", None)):
            return None
        return [sys.executable, os.path.abspath(script), "--agent", "--model", model, task]

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

    def build(self, *, model: str, task: str, cwd: str) -> tuple[list[str], str]:
        launcher = self._launcher(model, task)
        if launcher is None:                     # check() refuses first; belt and braces
            raise RuntimeError(
                "Onionmind Agent has no Tor-verified launcher, so it will not start."
            )
        return launcher, cwd


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
        models.setAccessibleName("Manage local models")
        models.clicked.connect(self.modelsRequested)
        self.models_button = models
        settings = QPushButton("Settings")
        settings.setObjectName("railAction")
        settings.setIcon(_icon("settings"))
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

    def clear_session_selection(self) -> None:
        self.sessions.clearSelection()
        self.sessions.setCurrentRow(-1)

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

    def __init__(
        self,
        models: list[str],
        current: str,
        label_for_model: Optional[Callable[[str], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label_for_model = label_for_model or (lambda value: "ONIONMIND MODEL")
        self.setWindowTitle("Onionmind models")
        self.setModal(True)
        self.resize(520, 390)
        layout = QVBoxLayout(self)
        heading = QLabel("Onionmind models")
        heading.setObjectName("brand")
        copy_label = QLabel("Choose from models installed on this machine, or add another Onionmind model locally.")
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
        row = QHBoxLayout()
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("Onionmind model name, for example BLAZE")
        self.model_name.setAccessibleName("Onionmind model to add")
        self.pull_button = QPushButton("Add model")
        self.pull_button.setObjectName("primaryButton")
        self.pull_button.setAccessibleName("Add Onionmind model")
        self.pull_button.clicked.connect(self._request_pull)
        self.pull_button.setEnabled(False)
        self.model_name.textChanged.connect(self._sync_pull_button)
        self.model_name.returnPressed.connect(self._request_pull)
        row.addWidget(self.model_name, 1)
        row.addWidget(self.pull_button)
        layout.addLayout(row)
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

    def _request_pull(self) -> None:
        name = self.model_name.text().strip()
        if not name:
            self.progress.setFormat("Enter an Onionmind model name")
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
    def __init__(self, data_root: Path, agent_limitation: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.setWindowTitle("Onionmind settings")
        self.resize(540, 350)
        outer = QVBoxLayout(self)
        heading = QLabel("Boundaries and storage")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.addRow("Inference", QLabel("Onionmind inference on this machine"))
        tor = QLabel("Chat search and the coding agent both leave over Tor; a failed Tor check never falls back to a direct request.")
        tor.setWordWrap(True)
        form.addRow("Tor", tor)
        agent = QLabel(_brand_runtime_text(agent_limitation))
        agent.setWordWrap(True)
        form.addRow("Agent", agent)
        form.addRow("Telemetry", QLabel("No Onionmind telemetry or account"))
        storage = QLabel(str(data_root))
        storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage.setWordWrap(True)
        form.addRow("Session storage", storage)
        outer.addLayout(form)
        outer.addStretch(1)
        self.storage_feedback = QLabel()
        self.storage_feedback.setObjectName("meta")
        self.storage_feedback.setWordWrap(True)
        outer.addWidget(self.storage_feedback)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_folder = actions.addButton("Open storage folder", QDialogButtonBox.ButtonRole.ActionRole)
        open_folder.setAccessibleName("Open Onionmind storage folder")
        open_folder.clicked.connect(self._open_storage_folder)
        actions.rejected.connect(self.reject)
        outer.addWidget(actions)

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
        self._tor_process: Optional[subprocess.Popen[bytes]] = None
        self._tor_ready = False
        self._tor_busy = False
        data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.data_root = Path(data_location or (Path.home() / ".onionmind")) / "desktop"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions")
        self.workspace_bridge = WorkspaceBridge(desktop_core)
        self.harness_bridge = HarnessBridge(desktop_core, core)
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
        self._project_delete_pending: Optional[str] = None
        self._rail_requested = True
        self._inspector_requested = True
        self._model_dialog: Optional[ModelManagerDialog] = None
        self._build_window()
        self._install_shortcuts()
        if demo:
            self._populate_demo()
        else:
            self._restore_state()
            self._probe_services()

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
        self.tor_status = StatusPill("Tor", "Checking", "busy")
        self.tor_status.make_clickable()
        self.tor_status.clicked.connect(self.toggle_tor)
        self.tor_status.setToolTip("Click to start or stop Tor. Tor search state is separate from Onionmind inference")
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
            "This early-access Agent cannot answer interactive approval prompts, so protected actions stop instead of continuing"
        )
        self.approval_state.setAccessibleName(
            "Onionmind Agent protected actions stop safely"
        )
        controls.addWidget(self.approval_state)
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
        model = _as_text(self.settings_data.get("model") or getattr(self.core, "MODEL", "inferno"))
        self.set_model_options([model], model)
        recent = self.settings_data.get("recent_projects") or []
        for path in reversed(recent[:8]):
            if path:
                self.left_rail.add_project(_as_text(path), select=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(session, "id")): session for session in sessions}
        self.left_rail.set_sessions(sessions)
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

        checker = getattr(self.core, "tor_check", None)
        if not callable(checker):
            self._set_tor_state("Not checked", "idle")
            return

        def tor_probe(signals: WorkerSignals) -> Any:
            del signals
            checker()
            return getattr(self.core, "_port", None)

        tor_worker = self._start_worker(tor_probe)
        tor_worker.signals.result.connect(self._tor_probe_complete)
        tor_worker.signals.result.connect(lambda _: self.inspector.append_activity("Tor readiness verified separately"))
        tor_worker.signals.error.connect(lambda message: self._tor_probe_failed(message))

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
        threading.Thread(
            target=worker.run,
            name="onionmind-desktop-worker",
            daemon=True,
        ).start()
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

    def _tor_probe_complete(self, port: Any) -> None:
        self._set_tor_state(f"Ready · {port}" if port else "Ready", "good")
        self.tor_status.setToolTip("Tor is up. Click to stop it. Search runs through Tor or not at all.")

    def _tor_probe_failed(self, message: str) -> None:
        self._set_tor_state("Not ready", "bad")
        self.tor_status.setToolTip(
            message + " Search fails closed and does not fall back to a direct request. Click to start Tor."
        )
        self.inspector.append_activity("Tor not ready; Chat search will fail closed")

    def _set_tor_state(self, text: str, state: str) -> None:
        self._tor_ready = state == "good"
        self._tor_busy = state == "busy"
        self.tor_status.set_status(text, state)

    def toggle_tor(self) -> None:
        if self.demo or self._tor_busy:
            return
        if self._tor_ready:
            self.stop_tor()
        else:
            self.start_tor()

    def start_tor(self) -> None:
        checker = getattr(self.core, "tor_check", None)
        if not callable(checker):
            return
        if self._tor_process is None or self._tor_process.poll() is not None:
            command = _tor_launch_command()
            if command is None:
                self._set_tor_state("Not installed", "bad")
                self.tor_status.setToolTip("No tor daemon or Tor Browser found. Install one, then click again.")
                self.set_status("No Tor daemon or Tor Browser found on this machine.")
                return
            try:
                self._tor_process = subprocess.Popen(command)
            except OSError as exc:
                self._set_tor_state("Not ready", "bad")
                self.set_status(f"Could not start Tor: {exc}")
                return
        self._set_tor_state("Starting", "busy")
        self.inspector.append_activity("Starting Tor")

        def wait_for_tor(signals: WorkerSignals) -> Any:
            del signals
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                try:
                    checker()
                    return getattr(self.core, "_port", None)
                except KeyboardInterrupt:
                    raise
                except BaseException:  # tor_check exits rather than raising when the proxy is down
                    time.sleep(3)
            raise RuntimeError("Tor did not come up in time.")

        def wire(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._tor_probe_complete)
            worker.signals.result.connect(lambda _: self.inspector.append_activity("Tor started"))
            worker.signals.error.connect(self._tor_probe_failed)

        self._start_worker(wait_for_tor, wire)

    def stop_tor(self) -> None:
        process = self._tor_process
        if process is None or process.poll() is not None:
            self._tor_process = None
            self.set_status("Tor is running outside Onionmind; stop it where you started it.")
            return
        process.terminate()
        self._tor_process = None
        # Clear the pinned port so a search after this fails closed instead of
        # dialling a proxy that is on its way down.
        try:
            self.core._port = None
        except AttributeError:
            pass
        self._set_tor_state("Stopped", "idle")
        self.tor_status.setToolTip("Tor is stopped. Click to start it. Search fails closed until it is up.")
        self.inspector.append_activity("Tor stopped")

    def _describe_model(self, raw_id: str) -> str:
        helper = getattr(self.desktop_core, "describe_model", None) if self.desktop_core else None
        if callable(helper):
            try:
                display = helper(raw_id)
                tier = _as_text(_field(display, "tier", "")).upper()
                if tier:
                    return tier
            except Exception:
                pass
        lower = raw_id.lower()
        tiers = ("spark", "ember", "blaze", "inferno", "cinder", "wildfire", "flashpoint", "phoenix", "nova", "pyre")
        tier = next((name.upper() for name in tiers if name in lower), "")
        if tier:
            return tier
        tag = raw_id.rsplit(":", 1)[-1] if ":" in raw_id else ""
        size = tag.upper() if re.fullmatch(r"\d+(?:\.\d+)?B", tag, re.IGNORECASE) else ""
        return "ONIONMIND CUSTOM" + (f" · {size}" if size else "")

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

    def set_mode(self, mode: str) -> None:
        mode = "chat" if mode.lower() == "chat" else "agent"
        self.chat_button.setChecked(mode == "chat")
        self.agent_button.setChecked(mode == "agent")
        self.mode = mode
        if mode == "chat":
            self.approval_state.hide()
            self.disclosure.setText("Private on-device chat · search is the only Tor-routed exception")
            self.composer.setPlaceholderText("Ask Onionmind anything…")
        else:
            self.approval_state.show()
            self.disclosure.setText("Early access · Agent web access is Tor-only and refuses to run without it")
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

    def _sync_action_states(self) -> None:
        has_draft = bool(self.composer.toPlainText().strip() or self.attachments)
        self.send_button.setEnabled(bool(self.active_kind) or has_draft)
        self.left_rail.set_conversation_available(bool(self.chat_messages))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        width = self.width()
        compact_toolbar = width < 1100
        self.brand_box.setFixedWidth(165 if width < 900 else 205)
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
            self.workspace = None
            self.current_snapshot = {}
            self.repo_label.setText("No project")
            self.repo_label.setToolTip("")
            self.branch_label.setText("Open a folder")
            self.scope_status.setText("No project selected")
            self.terminal.set_workspace(str(Path.home()))
            self.inspector.update_snapshot({})
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
        self.left_rail.set_sessions(sessions, session_id)
        self.set_status("Added saved session")
        self.inspector.append_activity("Saved session added locally")

    def _session_title(self) -> str:
        first = next((_as_text(m.get("content")) for m in self.chat_messages if m.get("role") == "user"), "New session")
        first = re.sub(r"\s+", " ", first).strip()
        return first[:48] + ("…" if len(first) > 48 else "")

    def save_current_session(self) -> bool:
        if not self.chat_messages:
            return True
        try:
            title = self._session_title()
            model = self.current_model_id()
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
            self.left_rail.set_sessions(self.session_bridge.list(), session_id)
            return True
        except Exception as exc:
            message = f"Session history could not be saved: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return False

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
        self.left_rail.set_sessions(sessions, _as_text(_field(self.current_session, "id")) if self.current_session else None)
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
            self.left_rail.set_sessions(self.session_bridge.list())
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
        self.left_rail.set_sessions(sessions, current_id)
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
        lines = [f"# {self._session_title()}", "", f"- Model: `{self._describe_model(self.current_model_id())}`"]
        if self.workspace:
            lines.append(f"- Workspace: `{self.workspace}`")
        lines.extend(("", "---", ""))
        for message in self.chat_messages:
            role = _as_text(message.get("role", "message"))
            heading = {"user": "Developer", "assistant": "Onionmind", "tool": "Local tool"}.get(role, role.title())
            content = message.get("content")
            if not isinstance(content, str):
                content = "[local image attachment]"
            lines.extend((f"## {heading}", "", content, ""))
        try:
            destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
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
        self.chat_messages = [dict(message) for message in (_field(session, "messages", ()) or ())]
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
            self.stop_active()
            return
        task = self.composer.toPlainText().strip()
        if not task and not self.attachments:
            self.set_status("Describe a task or attach a local file first.")
            self.focus_composer()
            return
        if not task:
            task = "Review the attached local files."
        attachment_names = [Path(path).name for path in self.attachments]
        visible_task = task + ("\n\nAttached locally: " + ", ".join(attachment_names) if attachment_names else "")
        message, agent_task = self._build_user_payload(task)
        self.chat_messages.append(message)
        self.transcript.add_message("user", visible_task)
        self.composer.clear()
        self.clear_attachments()
        self._set_active(self.mode)
        if not self.save_current_session():
            self.transcript.add_tool_card(
                "Session storage",
                [("Not saved", "The run will continue; check local disk access")],
            )
        if self.mode == "chat":
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
        self.active_kind = kind
        running = bool(kind)
        self.send_button.setText("Stop" if running else "Send")
        self.send_button.setAccessibleName("Stop active run" if running else "Send task")
        if kind == "agent":
            self.send_button.setToolTip(
                "Stop Onionmind Agent; child processes it started may require manual termination"
            )
        elif kind == "chat":
            self.send_button.setToolTip("Stop local generation after the current read")
        else:
            self.send_button.setToolTip("Send task (Enter)")
        self.chat_button.setEnabled(not running)
        self.agent_button.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.left_rail.projects.setEnabled(not running)
        self.left_rail.sessions.setEnabled(not running)
        if not running:
            self.stop_event = None
            self.harness_process = None
        self._sync_action_states()

    def _start_chat(self) -> None:
        self.stop_event = threading.Event()
        stop_event = self.stop_event
        model = self.current_model_id()
        history = copy.deepcopy(self.chat_messages)
        block = self.transcript.add_message("assistant", "")
        self.stream_block = block
        self.set_status(f"Streaming from {self._describe_model(model)}…")
        self.inspector.append_activity("Chat turn started on the local inference path")

        def chat_job(signals: WorkerSignals) -> dict[str, Any]:
            setattr(self.core, "MODEL", model)
            if not getattr(self.core, "BACKEND", None):
                detector = getattr(self.core, "detect_backend", None)
                if callable(detector):
                    detector()
            turn_stream = getattr(self.core, "turn_stream", None)
            if not callable(turn_stream):
                raise RuntimeError("The Onionmind core does not expose streaming chat.")
            raw = ""
            visible = ""

            def on_text(chunk: str) -> None:
                nonlocal raw, visible
                raw += _as_text(chunk)
                if "<think>" in raw and "</think>" not in raw:
                    candidate = ""
                elif "</think>" in raw:
                    candidate = raw.split("</think>")[-1]
                else:
                    candidate = raw
                if len(candidate) > len(visible):
                    delta = candidate[len(visible):]
                    visible = candidate
                    signals.text.emit(delta)

            def on_event(event: dict[str, Any]) -> None:
                signals.event.emit(dict(event))

            try:
                answer = turn_stream(history, on_text, stop_event=stop_event, on_event=on_event)
            except TypeError as exc:
                if "on_event" not in _as_text(exc):
                    raise
                answer = turn_stream(history, on_text, stop_event)
            return {"answer": _as_text(answer), "history": history}

        worker = self._start_worker(chat_job)
        worker.signals.text.connect(self._append_stream)
        worker.signals.event.connect(self._chat_event)
        worker.signals.result.connect(self._chat_complete)
        worker.signals.error.connect(self._chat_failed)

    def _append_stream(self, text: str) -> None:
        if self.stream_block is not None:
            self.stream_block.append_text(text)
            self.transcript._scroll_later()

    def _chat_event(self, event: dict[str, Any]) -> None:
        kind = _as_text(event.get("kind"))
        name = _as_text(event.get("name", "local tool"))
        display_name = name.replace("_", " ").strip().title()
        if kind == "tool_started":
            arguments = event.get("arguments") or {}
            detail = _as_text(arguments.get("query")) if isinstance(arguments, dict) else ""
            state = "Running through Tor · fails closed" if name == "web_search" else "Running locally"
            self.transcript.add_tool_card(display_name, [(detail or "Tool request", state)])
            activity = "Tor search started" if name == "web_search" else f"Local tool started: {display_name}"
            self.inspector.append_activity(activity)
        elif kind == "tool_finished":
            state = "Tor result returned" if name == "web_search" else "Finished"
            self.transcript.add_tool_card(display_name, [("Tool result", state)])
            activity = "Tor search finished" if name == "web_search" else f"Local tool finished: {display_name}"
            self.inspector.append_activity(activity)

    def _chat_complete(self, payload: dict[str, Any]) -> None:
        answer = payload.get("answer") or "The local model returned no answer."
        if self.stream_block is not None:
            self.stream_block.set_text(answer)
        self.chat_messages = payload.get("history") or [*self.chat_messages, {"role": "assistant", "content": answer}]
        if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
            self.chat_messages[-1]["content"] = answer
        if not self.chat_messages or self.chat_messages[-1].get("role") != "assistant":
            self.chat_messages.append({"role": "assistant", "content": answer})
        self.set_status("Chat turn complete")
        self.inspector.append_activity("Chat turn completed locally")
        self._set_active(None)
        self.save_current_session()

    def _chat_failed(self, message: str) -> None:
        message = _brand_runtime_text(message)
        if self.stream_block is not None:
            self.stream_block.set_text(f"Local inference could not continue. {message}")
        self.chat_messages.append({"role": "assistant", "content": f"Local inference failed: {message}"})
        self.set_status(f"Local inference failed: {message}")
        self.inspector.append_activity(f"Chat turn failed: {message}")
        self._set_active(None)
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
        model = self.current_model_id()
        workspace = self.workspace
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
            argv, cwd = self.harness_bridge.build(model=model, task=task, cwd=workspace)
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
            self.set_status("Stopping the local model after the current read…")
        elif self.active_kind == "agent":
            if self.harness_process is None:
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
        )
        self._model_dialog = dialog
        dialog.pullRequested.connect(lambda name: self.pull_model(name, dialog))
        dialog.exec()
        self._model_dialog = None

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
        SettingsDialog(self.data_root, self.harness_bridge.limitation, self).exec()

    def _populate_demo(self) -> None:
        self.set_model_options(["inferno", "blaze", "ember"], "inferno")
        self.model_status.set_status("Local · Ready", "good")
        self._set_tor_state("Connected", "good")
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
        self.inspector.append_activity("Onionmind inference and Tor readiness reported separately")
        self.set_mode("agent")
        self.set_status("Ready · 4 observed changes · all inference local")
        self._sync_action_states()
        QTimer.singleShot(0, lambda: self.transcript.verticalScrollBar().setValue(0))

    def closeEvent(self, event: Any) -> None:
        self.save_current_session()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()
        self.terminal.stop()
        if self._tor_process is not None and self._tor_process.poll() is None:
            self._tor_process.terminate()
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
sudo tee /usr/local/bin/onionmind >/dev/null <<LAUNCH
#!/bin/sh
DIR=$DIR_LITERAL
systemctl is-active --quiet tor 2>/dev/null || sudo -n systemctl start tor 2>/dev/null \
  || echo "[tor] not running - search will refuse until: sudo systemctl start tor"
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

echo
say "Ready"
echo "  Desktop:     onionmind   (native local workbench)"
echo "  Coding:      onionmind-code \"task\"   (headless agent; web over Tor only)"
echo "  Updates:     onionmind-update   (code only, model untouched)"
[ "$VISION" = 1 ] && echo "  Images:      ollama run $MODEL_NAME-vision   (then give it an image path)"
echo "  Web search:  $DIR/onionmind.py \"your question\""
echo "  Shortcut:    Onionmind - double-click to open the workbench"
