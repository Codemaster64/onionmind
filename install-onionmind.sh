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
  PKGS=(tor python-requests python-pysocks curl "$OLLAMA_PKG")
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
  sudo apt-get install -y tor python3-requests python3-socks curl ca-certificates
  # Ollama is not in apt. This is upstream's documented installer; fetched to a file
  # first so it can be read before it runs, rather than piped blind into a shell.
  if ! command -v ollama >/dev/null 2>&1; then
    say "Installing Ollama (upstream script -> /tmp/ollama-install.sh)"
    curl -fsSL https://ollama.com/install.sh -o /tmp/ollama-install.sh
    sh /tmp/ollama-install.sh
  fi
fi
command -v ollama >/dev/null || die "ollama not on PATH after install"

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
  say "Model: Qwen3.8-27B (uncensored)"
else
  if [ "$VRAM" -ge 6000 ]; then
    REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF; FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf; SZ=9B
  else
    REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF; FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf; SZ=4B
  fi
  MODEL_NAME="onionmind-$(echo $SZ | tr A-Z a-z)"   # name it for what it IS
  say "Model: Qwen3.5-$SZ (uncensored) - fits entirely in VRAM"
  [ "$VRAM" -lt 8000 ] && warn "No small Qwen3.8 exists, so this is one generation back but far faster."
  warn "Vision skipped (27B-only). Set ONIONMIND_MODEL=27b for Qwen3.8-27B instead."
fi

# --- 6. Weights -------------------------------------------------------------
sudo mkdir -p "$DIR"
sudo chown "$(id -u):$(id -g)" "$DIR"
sudo chmod 755 "$DIR"                  # traversable by the ollama service user
say "Downloading $FILE (resumable, ~10-16GB)"
# ponytail: curl -C - resumes a dropped download; no retry logic of our own
curl -L -C - --fail --noproxy '*' -o "$DIR/$FILE" \
     "https://huggingface.co/$REPO/resolve/main/$FILE"
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
curl -L -C - --fail --noproxy '*' -o "$DIR/$VIS"      "https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$VIS"
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
import sys, re, html, json, secrets, urllib.parse, requests

for _s in (sys.stdout, sys.stderr):              # Windows console defaults to cp1252,
    try: _s.reconfigure(encoding="utf-8")        # which mangles en-dashes and km2
    except Exception: pass

OLLAMA = "http://127.0.0.1:11434/api/chat"
LLAMA  = "http://127.0.0.1:8080/v1/chat/completions"   # llama.cpp llama-server
BACKEND = None
MODEL  = "inferno"
NOPROXY = {"http": None, "https": None}          # ollama is local - never via Tor
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
NUM_PREDICT = 8192

_port = None


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


def _clean(x):
    return html.unescape(re.sub(r"<[^>]+>", "", x)).strip()


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


def _ask_ollama(messages):
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800,
                          json={"model": MODEL, "messages": messages, "tools": TOOLS,
                                "stream": False, "options": {"num_predict": NUM_PREDICT}})
    except requests.exceptions.ConnectionError:
        sys.exit(f"Ollama is not running on 127.0.0.1:11434. Start it, then retry.")
    if r.status_code == 404:
        sys.exit(f"Model {MODEL!r} is not installed. See what is: ollama list")
    if not r.ok:
        sys.exit(f"Ollama returned {r.status_code}: {r.text[:200]}")
    return r.json()["message"]


def detect_backend():
    """Prefer ollama; fall back to llama.cpp's llama-server. Ollama has no
    Android build, so phones run llama-server with the same GGUFs."""
    global BACKEND
    for url, name in (("http://127.0.0.1:11434/api/version", "ollama"),
                      ("http://127.0.0.1:8080/health", "llama-server")):
        try:
            if requests.get(url, proxies=NOPROXY, timeout=3).ok:
                BACKEND = name
                print(f"[model] backend: {name}", file=sys.stderr)
                return
        except Exception:
            pass
    sys.exit("No model server on 11434 (ollama) or 8080 (llama-server). Start one.")


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
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": f"tc{i}", "type": "function",
                                        "function": {"name": f["function"]["name"],
                                                     "arguments": json.dumps(f["function"].get("arguments") or {})}}
                                       for i, f in enumerate(calls)]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _ask_llama(messages):
    try:
        r = requests.post(LLAMA, proxies=NOPROXY, timeout=1800,
                          json={"messages": _to_openai(messages), "tools": TOOLS,
                                "stream": False, "max_tokens": NUM_PREDICT})
    except requests.exceptions.ConnectionError:
        sys.exit("llama-server is not running on 127.0.0.1:8080. Start it, then retry.")
    if not r.ok:
        sys.exit(f"llama-server returned {r.status_code}: {r.text[:200]}")
    m = r.json()["choices"][0]["message"]
    msg = {"role": "assistant", "content": m.get("content") or ""}
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


def turn(messages):
    """Run one user turn to completion, letting the model search as often as it needs."""
    for _ in range(6):                            # ponytail: hard cap, not a retry policy
        msg = _ask_llama(messages) if BACKEND == "llama-server" else _ask_ollama(messages)
        messages.append(msg)
        calls = msg.get("tool_calls")
        if not calls:
            answer = strip_thinking(msg.get("content") or "")
            if not answer:
                return ("(the model used its whole token budget thinking and never reached "
                        f"an answer - raise NUM_PREDICT above {NUM_PREDICT} in this script)")
            return answer
        for c in calls:
            fn = c["function"]
            args = fn.get("arguments") or {}
            result = web_search(args.get("query", "")) if fn["name"] == "web_search" \
                     else f"(unknown tool {fn['name']})"
            messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
    return "(gave up after 6 tool rounds)"


def _save(history, path):
    """Write the conversation so far to a file - the print workflow's front end.
    The file lives wherever the user put it; power-off deletes it with the rest."""
    lines = []
    for m in history:
        if m.get("role") == "user":
            lines.append("you> " + m["content"])
        elif m.get("role") == "assistant":
            c = strip_thinking(m.get("content") or "")
            if c:
                lines.append("onion> " + c)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")
    print(f"[saved] {path} ({len(lines)} entries)")


if __name__ == "__main__":
    detect_backend()
    tor_check()
    history = []
    if len(sys.argv) > 1:
        history.append({"role": "user", "content": " ".join(sys.argv[1:])})
        print("\n" + turn(history))
    else:
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
sudo tee /usr/local/bin/onionmind >/dev/null <<'LAUNCH'
#!/bin/sh
DIR=@DIR@
systemctl is-active --quiet tor 2>/dev/null || sudo -n systemctl start tor 2>/dev/null \
  || echo "[tor] not running - search will refuse until: sudo systemctl start tor"
cd "$HOME"             # /save <file> lands here
exec python3 "$DIR/onionmind.py" "$@"
LAUNCH
sudo sed -i "s|@DIR@|$DIR|" /usr/local/bin/onionmind
sudo chmod 755 /usr/local/bin/onionmind

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/onionmind.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Onionmind
Comment=Local uncensored model with web search over Tor
Exec=onionmind
Icon=$DIR/logo.svg
Terminal=true
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
echo "  Chat:        onionmind   (or double-click the desktop icon)"
[ "$VISION" = 1 ] && echo "  Images:      ollama run $MODEL_NAME-vision   (then give it an image path)"
echo "  Web search:  $DIR/onionmind.py \"your question\""
echo "  Desktop:     Onionmind - double-click to chat"
