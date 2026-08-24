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

AUDIT=0
ALLOW_NETWORK=0
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --audit) AUDIT=1; shift ;;
    --allow-network) ALLOW_NETWORK=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help)
      echo "usage: $0 [--audit] [--allow-network --yes]"
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

DIR="$HOME/onionmind"

# Resolve the desired local model without contacting a repository. The exact
# weight URL is then available for the consent screen.
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
       FILE=LFM2.5-2.6B-heretic-Q4_K_M.gguf; MODEL_NAME=spark ;;
  4b) REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf; MODEL_NAME=ember ;;
  9b) REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf; MODEL_NAME=blaze ;;
  *) die "ONIONMIND_MODEL must be auto, lfm, 4b or 9b (got '$WANT')" ;;
esac
MODEL_URL="https://huggingface.co/$REPO/resolve/main/$FILE"

if [ "$AUDIT" = 1 ]; then
  echo "Onionmind Android local audit (no network, writes, downloads, or background service starts)"
  printf '  %-24s %s\n' "Termux" "$TERMUX_VERSION"
  printf '  %-24s %s\n' "install directory" "$([ -d "$DIR" ] && echo present || echo missing) ($DIR)"
  for package_name in python git cmake clang curl tor; do
    package_version=$(dpkg-query -W -f='${Status} ${Version}' "$package_name" 2>/dev/null || true)
    case "$package_version" in
      "install ok installed "*) state="installed (${package_version#install ok installed })" ;;
      *) state=MISSING ;;
    esac
    printf '  %-24s %s\n' "package $package_name" "$state"
  done
  if command -v python >/dev/null 2>&1; then
    python_state=$(python - <<'PY' 2>/dev/null || true
from importlib import metadata
import re

def version(name):
    value = metadata.version(name)
    return value, tuple(int(x) for x in re.findall(r"\d+", value)[:3])

rv, r = version("requests")
sv, s = version("PySocks")
ok = (2, 32) <= r < (3,) and (1, 7) <= s < (2,)
print(f"requests={rv}, PySocks={sv} ({'SUPPORTED' if ok else 'OUTDATED/INCOMPATIBLE'})")
PY
    )
    python_state=${python_state:-MISSING/INCOMPATIBLE}
  else
    python_state=MISSING
  fi
  printf '  %-24s %s\n' "requests + PySocks" "$python_state"
  printf '  %-24s %s\n' "llama-server" "$([ -x "$DIR/llama.cpp/build/bin/llama-server" ] && echo present || echo MISSING)"
  printf '  %-24s %s\n' "selected local model" "$MODEL_NAME ($FILE)"
  printf '  %-24s %s\n' "weight file" "$([ -s "$DIR/models/$FILE" ] && echo present || echo MISSING)"
  printf '  %-24s %s\n' "Tor process" "$(pgrep -x tor >/dev/null 2>&1 && echo running || echo stopped) (not changed)"
  printf '  %-24s %s\n' "remote freshness" "UNKNOWN by design; apply permits remote access"
  exit 0
fi

echo "Direct-network plan (nothing has contacted the network yet):"
echo "  - configured Termux package repositories: refresh metadata only if a prerequisite is missing"
grep -rhE '^[[:space:]]*deb[[:space:]]' "$PREFIX/etc/apt/sources.list" "$PREFIX/etc/apt/sources.list.d" 2>/dev/null | sed 's/^/      /' || true
echo "  - https://pypi.org/simple/requests/ and https://pypi.org/simple/pysocks/"
echo "      install only if supported Python modules are missing or out of range"
echo "  - https://github.com/ggml-org/llama.cpp"
echo "      shallow clone only if no local llama.cpp source/server exists"
echo "  - $MODEL_URL"
echo "      direct model download only if $DIR/models/$FILE is absent"
echo "  Tor is NOT started. These direct requests can reveal the phone's IP and"
echo "  Onionmind-related destinations to the access network and providers."
if [ "$ALLOW_NETWORK" != 1 ] || [ "$ASSUME_YES" != 1 ]; then
  [ -t 0 ] || die "network consent required; rerun interactively or pass BOTH --allow-network and --yes"
  printf 'Continue with exactly the network actions above? [y/N] '
  read -r answer
  case "$answer" in y|Y|yes|YES) ;; *) die "aborted before all network activity" ;; esac
fi

PKGS=(python git cmake clang curl tor)
MISSING=()
for package_name in "${PKGS[@]}"; do
  dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q '^install ok installed$' || MISSING+=("$package_name")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  say "Installing missing Termux packages only: ${MISSING[*]}"
  pkg update -y >/dev/null
  pkg install -y "${MISSING[@]}"
else
  say "Termux prerequisites already installed; skipping pkg"
fi

python_runtime_ready() {
  python - <<'PY' 2>/dev/null
from importlib import metadata
import re

def version(name):
    return tuple(int(x) for x in re.findall(r"\d+", metadata.version(name))[:3])

assert (2, 32) <= version("requests") < (3,)
assert (1, 7) <= version("PySocks") < (2,)
import requests, socks
PY
}
if python_runtime_ready; then
  say "Python network modules already satisfy supported ranges; skipping pip"
else
  python -m pip install --disable-pip-version-check 'requests>=2.32,<3' 'PySocks>=1.7,<2'
  python_runtime_ready || die "requests/PySocks installation did not produce a supported runtime"
fi

mkdir -p "$DIR/models"

# --- 1. llama-server: build once, ~15-20 min on a phone ----------------------
if [ ! -x "$DIR/llama.cpp/build/bin/llama-server" ]; then
  say "Building llama-server (one-time - plug in a charger)"
  if [ ! -d "$DIR/llama.cpp/.git" ]; then
    [ ! -e "$DIR/llama.cpp" ] || die "$DIR/llama.cpp exists but is not a git checkout; move it aside and rerun"
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$DIR/llama.cpp"
  else
    say "Using existing llama.cpp checkout; skipping GitHub clone"
  fi
  cmake -S "$DIR/llama.cpp" -B "$DIR/llama.cpp/build" \
        -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_OPENMP=OFF >/dev/null
  cmake --build "$DIR/llama.cpp/build" --target llama-server -j"$(nproc)"
fi

# --- 2. model by RAM: 9b on 12GB flagships, LFM on small phones --------------
say "Model: $WANT ($FILE) - ${RAM_MB}MB RAM detected"
[ -s "$DIR/models/$FILE" ] || curl -L -C - --fail -o "$DIR/models/$FILE" \
      "$MODEL_URL"

# --- 3. the search agent (build.py injects the canonical copy below) ---------
cat > "$DIR/onionmind.py" <<'PYEOF'
#!/usr/bin/env python3
"""Onionmind - a local uncensored model with web search over Tor.

  onionmind.py "one-shot question"    # NOTE: lands in your shell history
  onionmind.py                        # interactive - queries stay out of history

Needs a local Tor service, or Tor Browser installed on Windows for hidden background Tor.
"""
import sys, re, html, json, os, secrets, socket, subprocess, time, urllib.parse, requests

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
PORTS  = (9150, 9050) if os.name == "nt" else (9050, 9150)
# Windows prefers Tor Browser's hidden daemon; Unix prefers the system Tor daemon.
# Tor Browser's own UA. A unique UA is a fingerprint; blending into the herd is the point.
UA = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
# DuckDuckGo's onion service. Preferred over the clearnet endpoint for two reasons:
# it never leaves the Tor network (no exit node sees the query at all), and the
# clearnet endpoint returns 403 to most Tor exits, which looks like "search is broken".
ENDPOINTS = ("https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/",
             "https://html.duckduckgo.com/html/")
# Reasoning models spend the budget thinking BEFORE answering. Some local models
# can consume the former 8192-token ceiling without reaching their visible answer.
# Keep the request context at least as large as the generation budget.
NUM_PREDICT = 16384
NUM_CTX = 16384

_port = None
_managed_tor_process = None


def _proxies(port, isolate):
    # Distinct SOCKS credentials => Tor builds a SEPARATE circuit. Without this every
    # search shares one exit node and they can be trivially linked to each other.
    cred = f"{secrets.token_hex(8)}:x@" if isolate else ""
    return {s: f"socks5h://{cred}127.0.0.1:{port}" for s in ("http", "https")}
    # socks5h (not socks5) also resolves DNS through Tor; plain socks5 leaks every hostname.


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


def start_tor_hidden(timeout=30, stop_event=None):
    """Start Tor Browser's Tor daemon without opening a browser or console window.

    This is called only after the user opts into Tor search. Existing Tor proxies
    are reused and never adopted or stopped by Onionmind.
    """
    global _managed_tor_process
    existing = tor_proxy_port()
    if existing:
        return existing
    if _managed_tor_process is not None:
        stop_managed_tor()
    if os.name != "nt":
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
        _managed_tor_process = subprocess.Popen(
            [tor_exe, "--defaults-torrc", defaults, "-f", torrc,
             "--DisableNetwork", "0", "--SocksPort", "9150 IsolateSOCKSAuth"],
            cwd=browser,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                stop_managed_tor()
                raise RuntimeError("Background Tor start was stopped.")
            port = tor_proxy_port()
            if port:
                return port
            if _managed_tor_process.poll() is not None:
                break
            time.sleep(0.25)
        stop_managed_tor()
        raise RuntimeError("The background Tor process did not become ready.")
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


def tor_check():
    """Pin the Tor port, or exit. Fails closed - never falls back to a direct connection."""
    global _port
    _port = None
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
    sys.exit("No verified Tor proxy is available. Enable Tor search again or start your local Tor service.")


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


def _ask_ollama(messages, allow_search=False):
    payload = {"model": MODEL, "messages": messages, "stream": False,
               "options": {"num_predict": NUM_PREDICT, "num_ctx": NUM_CTX}}
    if allow_search:
        payload["tools"] = TOOLS
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800,
                          json=payload)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Ollama is not running on 127.0.0.1:11434. Start it, then retry.")
    if r.status_code == 404:
        sys.exit(f"Model {MODEL!r} is not installed. See what is: ollama list")
    if not r.ok:
        sys.exit(f"Ollama returned {r.status_code}: {r.text[:200]}")
    return r.json()["message"]


def _ask_ollama_stream(messages, on_text, stop_event=None, allow_search=False):
    """Stream one Ollama response while retaining tool-call compatibility."""
    payload = {"model": MODEL, "messages": messages, "stream": True,
               "options": {"num_predict": NUM_PREDICT, "num_ctx": NUM_CTX}}
    if allow_search:
        payload["tools"] = TOOLS
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800, stream=True,
                          json=payload)
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
            if chunk.get("tool_calls"):
                message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
            if event.get("done"):
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
                print(f"[model] backend: {name}", file=sys.stderr)
                return
        except Exception:
            pass
    sys.exit("No model server on 11434 (ollama) or 8080 (llama-server). Start one.")


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
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": f"tc{i}", "type": "function",
                                        "function": {"name": f["function"]["name"],
                                                     "arguments": json.dumps(f["function"].get("arguments") or {})}}
                                       for i, f in enumerate(calls)]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _ask_llama(messages, allow_search=False):
    payload = {"messages": _to_openai(messages), "stream": False,
               "max_tokens": NUM_PREDICT}
    if allow_search:
        payload["tools"] = TOOLS
    try:
        r = requests.post(LLAMA, proxies=NOPROXY, timeout=1800,
                          json=payload)
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


def turn(messages, stop_event=None, allow_search=False):
    """Run one user turn; external search is available only after explicit opt-in."""
    for _ in range(6):                            # ponytail: hard cap, not a retry policy
        if stop_event is not None and stop_event.is_set():
            return "(stopped)"
        msg = (_ask_llama(messages, allow_search=allow_search)
               if BACKEND == "llama-server"
               else _ask_ollama(messages, allow_search=allow_search))
        messages.append(msg)
        calls = msg.get("tool_calls")
        if not calls:
            answer = strip_thinking(msg.get("content") or "")
            if not answer:
                return (f"(the model used its whole {NUM_PREDICT}-token response budget "
                        "thinking and never reached an answer; try a more direct prompt or "
                        "raise NUM_PREDICT and NUM_CTX together)")
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
            return answer or (f"(the model used its whole {NUM_PREDICT}-token response budget "
                              "thinking and never reached an answer; try a more direct prompt or "
                              "raise NUM_PREDICT and NUM_CTX together)")
        for c in calls:
            if stop_event is not None and stop_event.is_set():
                return "(stopped)"
            fn = c["function"]
            args = fn.get("arguments") or {}
            denied = fn["name"] == "web_search" and not allow_search
            if on_event and denied:
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
        if not messagebox.askyesno(
                "Direct model download",
                "The local Ollama service will download this model directly from its "
                "configured registry. This is not Tor-routed and exposes this machine's "
                "network address. Continue?",
                parent=root):
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
            tor_port = tor_proxy_port()
            models = installed_models() if BACKEND == "ollama" else []
            root.after(0, lambda: populate_models(models))
            tor_note = f"Tor proxy {tor_port} available" if tor_port else "Tor search off"
            root.after(0, lambda: set_status(f"ready · {MODEL} · {tor_note}", "#9ef0b0"))
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
            ready_text = f"ready · {MODEL} · Tor running · {tor_port}" if tor_port else f"ready · {MODEL} · Tor off"
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
        """Open DeepSeek Harness in its own agent session via Ollama."""
        if not messagebox.askyesno(
                "Run DeepSeek Harness directly?",
                "DeepSeek Harness and commands it runs are not confined to Tor. They may "
                "contact arbitrary hosts directly and expose this machine's network address. Continue?",
                parent=root):
            return
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            env = os.environ.copy()
            env["ONIONMIND_PY"] = os.path.abspath(__file__)
            env["ONIONMIND_PYTHON"] = "python"
            subprocess.Popen(["ollama", "launch", "dsh", "--model", MODEL,
                              "--", "--profile", "headless"],
                             creationflags=flags, env=env)
            set_status("coding agent launching…", "#9ef0b0")
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Coding agent unavailable",
                "Could not start DeepSeek Harness through Ollama:\n\n" + str(exc))

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
    try:
        root.mainloop()
    finally:
        stop_managed_tor()


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
    if "--ui" in sys.argv:
        run_ui()
        raise SystemExit
    allow_search_once = "--allow-search" in sys.argv
    cli_args = [a for a in sys.argv[1:] if a != "--allow-search"]
    try:
        detect_backend()
        history = []
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
sed -i "s|^MODEL  = .*|MODEL  = \"$MODEL_NAME\"|" "$DIR/onionmind.py"

# --- 4. `onionmind`: starts only the loopback model server -------------------
cat > "$PREFIX/bin/onionmind" <<LAUNCH
#!/data/data/com.termux/files/usr/bin/bash
DIR="\$HOME/onionmind"
command -v termux-wake-lock >/dev/null && termux-wake-lock   # Android kills background apps
if ! curl -sf --noproxy '*' -m 2 http://127.0.0.1:8080/health >/dev/null; then
  echo "[model] llama-server starting (first model load takes a minute)"
  nohup "\$DIR/llama.cpp/build/bin/llama-server" \
        -m "$DIR/models/$FILE" --host 127.0.0.1 --port 8080 -c 16384 \
        > "\$DIR/llama-server.log" 2>&1 &
fi
if ! (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null; then
  echo "[tor] not running; Onionmind will not start it. Start Tor explicitly before using search." >&2
fi
cd "\$HOME"                     # /save <file> lands here
exec python3 "\$DIR/onionmind.py" "\$@"
LAUNCH
chmod 755 "$PREFIX/bin/onionmind"

say "Ready"
echo "  Chat:        onionmind"
echo "  Tor:         not started by Onionmind; start it explicitly before chat/search"
echo "  Keep alive:  disable battery optimisation for Termux, or Android will kill it"
