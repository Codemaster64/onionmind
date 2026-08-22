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

# --- 2. model by RAM: the 9b on 12GB flagships, the 4b below ------------------
WANT="${ONIONMIND_MODEL:-auto}"
RAM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
if [ "$WANT" = auto ]; then
  [ "$RAM_MB" -ge 11000 ] && WANT=9b || WANT=4b
fi
case "$WANT" in
  4b) REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf ;;
  9b) REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF
      FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf ;;
  *) die "ONIONMIND_MODEL must be auto, 4b or 9b (got '$WANT')" ;;
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
import sys, re, html, json, secrets, urllib.parse, requests

for _s in (sys.stdout, sys.stderr):              # Windows console defaults to cp1252,
    try: _s.reconfigure(encoding="utf-8")        # which mangles en-dashes and km2
    except Exception: pass

OLLAMA = "http://127.0.0.1:11434/api/chat"
LLAMA  = "http://127.0.0.1:8080/v1/chat/completions"   # llama.cpp llama-server
BACKEND = None
MODEL  = "onionmind-27b"
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
sed -i "s|^MODEL  = .*|MODEL  = \"onionmind-$WANT\"|" "$DIR/onionmind.py"

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
