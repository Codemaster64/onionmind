#!/usr/bin/env bash
# Onionmind for Android - light models, entirely on the phone, inside Termux.
# Get Termux from F-Droid (the Play Store build is abandoned): https://f-droid.org
# Then here:  bash install-onionmind-android.sh
# Engine is llama.cpp's llama-server - ollama has no Android build. Tor comes
# from the Termux tor package (Orbot in Power User mode works too - same port).
set -euo pipefail
say() { printf '\033[36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -n "${TERMUX_VERSION:-}" ] || die "run this inside Termux (the F-Droid build, not Play Store)"

say "Installing build tools, python and tor"
pkg update -y >/dev/null
pkg install -y python git cmake clang curl tor

DIR="$HOME/onionmind"
mkdir -p "$DIR/models"

# --- 1. llama-server: build once, ~15-20 min on a phone ----------------------
if [ ! -x "$DIR/llama.cpp/build/bin/llama-server" ]; then
  say "Building llama-server (one-time - plug in a charger)"
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$DIR/llama.cpp"
  cmake -S "$DIR/llama.cpp" -B "$DIR/llama.cpp/build" \
        -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_OPENMP=OFF >/dev/null
  cmake --build "$DIR/llama.cpp/build" --target llama-server -j"$(nproc)"
fi

# --- 2. model by RAM: 9b on 12GB flagships, LFM on small phones --------------
WANT="${ONIONMIND_MODEL:-auto}"
RAM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
if [ "$WANT" = auto ]; then
  if [ "$RAM_MB" -ge 11000 ]; then WANT=9b
  elif [ "$RAM_MB" -ge 7000 ]; then WANT=4b
  else WANT=lfm
  fi
fi
case "$WANT" in
  lfm) REPO=Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF
       FILE=LFM2.5-2.6B-heretic-Q4_K_M.gguf ;;
  4b) REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf ;;
  9b) REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf ;;
  *) die "ONIONMIND_MODEL must be auto, lfm, 4b or 9b (got '$WANT')" ;;
esac
say "Model: $WANT ($FILE) - ${RAM_MB}MB RAM detected"
[ -s "$DIR/models/$FILE" ] || curl -L -C - --fail -o "$DIR/models/$FILE" \
      "https://huggingface.co/$REPO/resolve/main/$FILE"

# --- 3. the search agent (build.py injects the canonical copy below) ---------
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
    # A stale-but-set port must not survive a failed reverification: callers
    # would keep "verifying" against a proxy that just answered "not Tor".
    _port = None
    # Telling a Windows user to run systemctl is telling them nothing. Tor
    # Browser is how Tor gets onto a Windows box and its bundled tor binds 9150
    # by itself, so name the thing that actually works on the platform underfoot.
    if os.name == "nt":
        sys.exit("No Tor proxy on 9050/9150. Start Tor Browser and click "
                 "Connect - its bundled Tor binds 9150 - then try again.")
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
                    port = start_tor_hidden(stop_event=stop_event)
                    root.after(0, lambda: set_status(f"Tor running · {port}", "#9ef0b0"))
                answer = turn_stream(
                    history,
                    lambda chunk: root.after(0, lambda chunk=chunk: stream_update(chunk)),
                    stop_event,
                    allow_search=search_allowed)
            except (Exception, SystemExit) as exc:
                answer = "Error: " + user_error(exc)
            root.after(0, lambda: stream_finish(answer))
            busy = False
            tor_port = tor_proxy_port()
            ready_text = (f"ready · {MODEL} · Tor running · {tor_port}" if tor_port
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
MODEL_NAME=blaze
[ "$WANT" = 4b ] && MODEL_NAME=ember
sed -i "s|^MODEL  = .*|MODEL  = \"$MODEL_NAME\"|" "$DIR/onionmind.py"

# --- 4. `onionmind`: the way in. Starts llama-server and tor if needed -------
cat > "$PREFIX/bin/onionmind" <<LAUNCH
#!/data/data/com.termux/files/usr/bin/bash
DIR="\$HOME/onionmind"
command -v termux-wake-lock >/dev/null && termux-wake-lock   # Android kills background apps
if ! curl -sf --noproxy '*' -m 2 http://127.0.0.1:8080/health >/dev/null; then
  echo "[model] llama-server starting (first model load takes a minute)"
  nohup "\$DIR/llama.cpp/build/bin/llama-server" \
        -m "$DIR/models/$FILE" --host 127.0.0.1 --port 8080 -c 8192 \
        > "\$DIR/llama-server.log" 2>&1 &
fi
(exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null || \
  nohup tor > "\$DIR/tor.log" 2>&1 &
cd "\$HOME"                     # /save <file> lands here
exec python3 "\$DIR/onionmind.py" "\$@"
LAUNCH
chmod 755 "$PREFIX/bin/onionmind"

say "Ready"
echo "  Chat:        onionmind"
echo "  First boot:  tor builds its circuits slowly on a phone - give it a minute"
echo "  Keep alive:  disable battery optimisation for Termux, or Android will kill it"
