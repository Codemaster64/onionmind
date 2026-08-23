# Qwen3.8-27B uncensored + Tor web search, on Ollama. One paste. Re-runnable.
# Works in Orca terminals, Windows Terminal, or plain pwsh - nothing here prompts.
$ErrorActionPreference = 'Stop'
# Orca terminal: no ANSI soup in logs. $PSStyle is pwsh 7+ only - guard it or this
# whole script dies on line 4 under Windows PowerShell 5.1, which many machines default to.
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSStyle.OutputRendering = 'PlainText' }
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new() } catch {}

# Set ONIONMIND_DIR to put weights elsewhere. Keep it on an NVMe: a Storage Space / HDD
# makes imports crawl and 10x's model load time.
$Dir = if ($env:ONIONMIND_DIR) { $env:ONIONMIND_DIR } else { "$env:LOCALAPPDATA\qwen" }
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# --- 1. Ollama -------------------------------------------------------------
$O = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $O)) {
  Say "Installing the local model engine"
  winget install --id Ollama.Ollama -e --silent --accept-package-agreements `
                 --accept-source-agreements --disable-interactivity
}
if (-not (Test-Path $O)) { throw "The local model engine could not be installed." }
try { python -m pip install --user --disable-pip-version-check tkinterdnd2 2>$null } catch { }

# --- 2. Tor Browser (provides the SOCKS proxy on 9150) ---------------------
# GetFolderPath, not $env:USERPROFILE\Desktop: OneDrive Known Folder Move
# relocates the Desktop on most Windows 11 installs, and the shortcut code at
# the bottom of this script already gets that right.
$TorExe = "$([Environment]::GetFolderPath('Desktop'))\Tor Browser\Browser\firefox.exe"
if (-not (Test-Path $TorExe)) {
  Say "Installing Tor Browser"
  try {
    winget install --id TorProject.TorBrowser -e --silent --accept-package-agreements `
                   --accept-source-agreements --disable-interactivity
  } catch { Write-Host "  Tor install failed - search will refuse to run until you install it" -ForegroundColor Yellow }
}

# Tor Browser only exposes SOCKS on 9150 while it is RUNNING, so start it and wait for
# the circuit. A tor daemon on 9050 counts too - if one is already up, skip all this.
function Tor-Up { foreach ($p in 9150,9050) {
    if (Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue) { return $true } }
  return $false }
if (-not (Tor-Up)) {
  foreach ($c in @($TorExe,
                   "$env:USERPROFILE\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:USERPROFILE\OneDrive\Desktop\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Tor Browser\Browser\firefox.exe",
                   "$env:LOCALAPPDATA\Programs\Tor Browser\Browser\firefox.exe",
                   "$env:PROGRAMFILES\Tor Browser\Browser\firefox.exe",
                   "${env:ProgramFiles(x86)}\Tor Browser\Browser\firefox.exe")) {
    if (Test-Path $c) { Say "Starting Tor Browser"; Start-Process $c; break }
  }
  foreach ($i in 1..40) { Start-Sleep 3; if (Tor-Up) { break } }
}
if (Tor-Up) { Say "Tor proxy is up" }
else { Write-Host "  Tor not listening yet - open Tor Browser and click Connect before searching" -ForegroundColor Yellow }

# --- 3. Pick the build that fits this GPU ----------------------------------
$vram = 0
try { $vram = [int](@(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits)[0]) } catch {}
Say "GPU VRAM: $vram MiB"
if ($vram -eq 0) { Write-Host "  No NVIDIA GPU - will run on CPU (slow)" -ForegroundColor Yellow }

# ONIONMIND_MODEL picks what gets installed:
#   auto (default) - the 27B where it fits in VRAM, the fast small model where it doesn't
#   fast           - always the mobile-sized LFM model, whatever the hardware
#   mobile        - alias for fast
#   27b            - always Qwen3.8-27B, even if it runs on CPU at 1-2 tok/s
# There is NO small Qwen3.8: as of Aug 2026 the family is 27B and a 2.4T MoE, nothing else,
# and no generation newer than 3.8 exists. Qwen3.5 is the newest line WITH small dense
# models, so that is what "fast" means here.
# MTP builds keep the multi-token-prediction head; ollama uses it for speculative decoding.
$want = if ($env:ONIONMIND_MODEL) { $env:ONIONMIND_MODEL.ToLower() } else { 'auto' }
if ($want -notin @('auto','fast','mobile','27b')) { throw "ONIONMIND_MODEL must be auto, fast, mobile or 27b (got '$want')" }
if ($want -eq 'mobile') { $want = 'fast' }
if ($want -eq 'auto') { $want = if ($vram -ge 8000) { '27b' } else { 'fast' } }

$Vision = ($want -eq '27b')          # the mmproj is built for the 27B architecture
if ($want -eq '27b') {
  if     ($vram -ge 17000) { $repo='hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF'; $file='Qwen3.8-27B-abliterated-mtp-Q4_K_M.gguf' }
  elseif ($vram -ge 12000) { $repo='soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf'
                             $file='qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf' }
  else                     { $repo='hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF'; $file='Qwen3.8-27B-abliterated-IQ2_M.gguf' }
  if ($vram -lt 8000) { Write-Host "    ${vram} MiB VRAM: the 27B will run mostly on CPU (~1-2 tok/s)." -ForegroundColor Yellow }
  $name = "inferno"
  Say "Model: INFERNO (27B)"
} else {
  $repo='Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF'; $file='LFM2.5-2.6B-heretic-Q4_K_M.gguf'; $sz='2.6B'
  $name = 'spark'
  Say "Model: SPARK ($sz) - mobile/fast profile"
  if ($vram -lt 8000) {
    Write-Host "    Using LFM2.5 for much faster local responses." -ForegroundColor Yellow
  }
  Write-Host "    Vision is available with Inferno. Set ONIONMIND_MODEL=27b to install it." -ForegroundColor Yellow
}

# --- 4. Tuning (persisted, survives reboots) -------------------------------
[Environment]::SetEnvironmentVariable('OLLAMA_FLASH_ATTENTION','1',   'User')
[Environment]::SetEnvironmentVariable('OLLAMA_KV_CACHE_TYPE',  'q8_0','User')
$env:OLLAMA_FLASH_ATTENTION='1'; $env:OLLAMA_KV_CACHE_TYPE='q8_0'

# --- 5. Server -------------------------------------------------------------
function Ollama-Up {
  try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 -Proxy $null | Out-Null; $true }
  catch { $false }
}
if (-not (Ollama-Up)) {
  Say "Starting the local model engine"
  Start-Process -FilePath $O -ArgumentList 'serve' -WindowStyle Hidden
  foreach ($i in 1..30) { Start-Sleep 2; if (Ollama-Up) { break } }
}
if (-not (Ollama-Up)) { throw "The local model engine did not start." }

# --- 6. Weights ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

# Huggingface publishes each LFS object's sha256 in X-Linked-ETag, so a 16GB
# download can be checked against the digest the host itself serves - no hash
# table to maintain here. Integrity only, not supply-chain pinning: it catches
# the truncated or corrupted file that otherwise shows up much later as an
# inscrutable model-load error. Set ONIONMIND_SKIP_VERIFY=1 to opt out.
function Verify-Download($Path, $Url) {
  if ($env:ONIONMIND_SKIP_VERIFY -eq '1') { return }
  # curl.exe, not Invoke-WebRequest: the digest header is served by huggingface.co
  # on the hop BEFORE the redirect to the CDN, and IWR only exposes the final
  # response's headers. curl -I -L prints every hop. It is also the same tool the
  # download itself uses, so the two agree on proxies and TLS.
  $want = ''
  try {
    $hdrs = & curl.exe -fsSLI $Url 2>$null
    $m = $hdrs | Select-String -Pattern '^X-Linked-ETag:\s*"?([0-9a-f]{64})"?' |
         Select-Object -Last 1
    if ($m) { $want = $m.Matches[0].Groups[1].Value }
  } catch { return }                       # no digest published; nothing to check
  if ([string]::IsNullOrWhiteSpace($want)) { return }
  $got = (Get-FileHash $Path -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $want) {
    Remove-Item $Path -Force -ErrorAction SilentlyContinue
    throw "$(Split-Path $Path -Leaf) downloaded corrupt (sha256 $got, expected $want) - deleted it; rerun to try again"
  }
  Say "verified $(Split-Path $Path -Leaf)"
}

Say "Downloading $file (resumable, ~10-16GB)"
# ponytail: curl.exe -C - resumes a dropped download; no retry logic of our own
$weightsUrl = "https://huggingface.co/$repo/resolve/main/$file"
curl.exe -L -C - --fail -o "$Dir\$file" $weightsUrl
Verify-Download "$Dir\$file" $weightsUrl

# --- 7. Model --------------------------------------------------------------
# num_gpu 99 = all layers on GPU; ollama's auto-split is too conservative and silently
# leaves VRAM unused. It needs desktop headroom too: if speed swings while browsers are
# open, the model is spilling to shared memory - drop this to ~56.
# TEMPLATE is required: a bare GGUF import gets `{{ .Prompt }}`, which makes the model
# echo prompts and leak system text instead of chatting.
@"
FROM $Dir\$file
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
"@ | Set-Content "$Dir\Modelfile" -Encoding UTF8
Say "Registering model"
& $O create $name -f "$Dir\Modelfile"
if ($LASTEXITCODE -ne 0) { throw "Model registration failed ($LASTEXITCODE) - see output above" }

# --- 7b. Vision (27B only - the mmproj is built for that architecture) ------
if ($Vision) {
# The mmproj is the vision tower in its own file - architecture-specific, not
# quant-specific, so this one projector binds to any Qwen3.8-27B build (verified
# against the 3.69bpw MTP model it is paired with here). Shares the base blob, so
# it costs ~900MB on top, not another full model.
$vis = "Qwen3.8-27B-Uncensored-vision-f16.gguf"
$visUrl = "https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$vis"
curl.exe -L -C - --fail --noproxy '*' -o "$Dir\$vis" $visUrl
Verify-Download "$Dir\$vis" $visUrl
(Get-Content "$Dir\Modelfile" -Raw).Replace("FROM $Dir\$file", "FROM $Dir\$file`nFROM $Dir\$vis") |
  Set-Content "$Dir\Modelfile.vision" -Encoding UTF8
Say "Registering vision model"
& $O create "$name-vision" -f "$Dir\Modelfile.vision"
  if ($LASTEXITCODE -ne 0) { Write-Host "    vision model failed to build - text model is fine" -ForegroundColor Yellow }
}

# --- 8. Tor search tool ----------------------------------------------------
$py = (Get-Command python -EA SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -EA SilentlyContinue).Source }
if ($py) {
  Say "Installing Python deps (requests, PySocks)"
  & $py -m pip install --quiet --disable-pip-version-check requests PySocks
} else {
  Write-Host "  Python not found - install it, then: pip install requests PySocks" -ForegroundColor Yellow
}

$search = @'
#!/usr/bin/env python3
"""Onionmind - a local uncensored model with web search over Tor.

  onionmind.py "one-shot question"    # NOTE: lands in your shell history
  onionmind.py                        # interactive - queries stay out of history

Needs Tor Browser open (it owns SOCKS on 9150) or a tor daemon on 9050.
"""
import sys, re, html, json, secrets, urllib.parse, requests

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
PORTS  = (9150, 9050)                            # 9150 = Tor Browser, 9050 = tor daemon
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
    sys.exit("No Tor proxy on 9150/9050. Open Tor Browser and leave it running.")


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


def _ask_ollama_stream(messages, on_text, stop_event=None):
    """Stream one Ollama response while retaining tool-call compatibility."""
    try:
        r = requests.post(OLLAMA, proxies=NOPROXY, timeout=1800, stream=True,
                          json={"model": MODEL, "messages": messages, "tools": TOOLS,
                                "stream": True, "options": {"num_predict": NUM_PREDICT}})
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
            if not answer:
                return ("(the model used its whole token budget thinking and never reached "
                        f"an answer - raise NUM_PREDICT above {NUM_PREDICT} in this script)")
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


def turn_stream(messages, on_text, stop_event=None):
    """Run a turn with live text for Ollama; llama-server uses the safe fallback."""
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
            return answer or ("(the model used its whole token budget thinking and never "
                              f"reached an answer - raise NUM_PREDICT above {NUM_PREDICT} "
                              "in this script)")
        for c in calls:
            if stop_event is not None and stop_event.is_set():
                return "(stopped)"
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
            content = m.get("content") if isinstance(m.get("content"), str) else "[image]"
            lines.append("you> " + content)
        elif m.get("role") == "assistant":
            c = strip_thinking(m.get("content") or "")
            if c:
                lines.append("onion> " + c)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")
    print(f"[saved] {path} ({len(lines)} entries)")


def run_ui():
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
        """Open DeepSeek Harness in its own agent session via Ollama."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            env = os.environ.copy()
            env["ONIONMIND_PY"] = os.path.abspath(__file__)
            env["ONIONMIND_PYTHON"] = "python"
            patch = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "dsh-onionmind-tor.patch.yml")
            subprocess.Popen(["ollama", "launch", "dsh", "--model", MODEL,
                              "--", "--patch", patch],
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
    root.mainloop()


if __name__ == "__main__":
    if "--tor-search" in sys.argv:
        query = " ".join(a for a in sys.argv[1:] if a != "--tor-search").strip()
        if not query:
            raise SystemExit("usage: onionmind.py --tor-search <query>")
        tor_check()
        print(web_search(query), end="")
        raise SystemExit
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
'@
# Point the tool at whichever model was installed - on the fast tier the model is NOT
# named inferno, and the embedded default would reference a model that does
# not exist. Plain .Replace(), not a regex: this file has CRLF endings and a $
# anchor cannot match before the carriage return. The shell installer uses sed.
$search = $search.Replace('MODEL  = "inferno"', 'MODEL  = "' + $name + '"')
# Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1, and a BOM ahead
# of the shebang breaks ./onionmind.py on Linux. WriteAllText with $false does not.
[System.IO.File]::WriteAllText("$Dir\onionmind.py", $search, (New-Object System.Text.UTF8Encoding($false)))

# --- 9. Desktop shortcut -----------------------------------------------------
# The icon is logo.svg rendered to .ico at repo build time (build.py injects it
# base64 below, keeping logo.svg the single source). A shortcut needs a real
# .ico - Windows will not use the SVG.
Say "Creating desktop shortcut"
# build.py: onionmind.ico payload between these markers - regenerate with it, never by hand
$OnionIco = @'
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
l+3x7A8EP1Sf+Wnkjl4F1PsorfkI6VMV4wMdBW5nyI/CyeT7wam+vwQGIEO4cAUqhh2svOSRIBoa
rApSo/Nrz6ukE/oAC1esPNByJbqL4EfZDbgTYoUdHh8mNQ7JtKhFj0eKDUnSywe29P/oQE/U6Q4C
A9DN/DRyR6/8ZK/hTtgZobUODg7ReZJPTMuL2pyPb8yS3NiBSmAAsohbMrvIC8mhBH0wmAb7Xcwi
VxGEpCK9RiK8aitvXLU/q+7mOoEByBEqK+fLka2tA1WifSgJOghA/5yKq08HggbkBiGwBtpbjfia
dcGePjcIDECOUlk5Xx66af2AAlIDocVBTOiXC5l1RghsU4rWk6PWOiG5HqNWbfCr7l+AvwQGoAdx
TcWsvAEk+8FzipNaFUsheoO5CFlzN3I7QJsFsMkDNjnEm9bL5Ka7F17brZmJAfYEBmA/YH7lfPnh
xo1FW7QuIkmFxJSvifMEOI9A+RrIEwSpoaUgR0Kz2CWbT4gEACgoEMsOoVVSO+hwtEyAdUIxdUio
VoR5W8Gm/NYXDu69bX9VLgoICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI
CAgICAgICAgICAgICAgICAgICAgICAgICAgIyAr/H/UeYmBkAMUBAAAAAElFTkSuQmCCKAAAADAA
AABgAAAAAQAgAAAAAAAAJAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJJJ
bQeXQnsbmUR9LZhDfTmYRX5Dmkh+R5pIfkeXRnxCmEh9OZdGgCyXQnsbqlWABgAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAIBAgASVR3gkmEV+Q5dGfViYRX5ZmEV+WZhFflmYRX5ZmEV+WZhFflmYRX5ZmEV+WZhF
flmYRX5Zl0Z9WJdGfEKZQnwjqlWqAwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAACAQIAEnEaALJpHflOYRX5ZmEV+WZhFflmYRX5ZmEV+WZdGfViW
RnxQmEZ+TZhGfk2ZRnxQl0Z9WJhFflmYRX5ZmEV+WZhFflmYRX5Zmkd+U5dGgCyAQIAEAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJVGex2XRXtRmEV+WZhFflmYRX5Z
l0d9VpZHfT2ZQnwjmUSID/8AAAEAAAAAAAAAAAAAAAAAAAAA/wD/AY9QgBCZQnwjm0d9PZdHfVaY
RX5ZmEV+WZhFflmZRnxQm0mAHAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACqVVUDlkR7
OJhFflmYRX5ZmEV+WZlGfFCZRoAogFWABgAAAAAAAAAAAAAAAJJJbQeZR4UZn0V8JZtGfCGZR3oZ
nU6JDQAAAAAAAAAAAAAAAKpVgAaZRoAol0V7UZhFflmYRX5ZmEV+WZlGfTeqVVUDAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAALZJbQeZRYBGmEV+WZhFflmYRn5XmUR9LYBAgAQAAAAAqlVVA5lGgCiYRn5XmUZ+
f5dFfoyXRX6Ml0V+jJdFfoyXRX6Ml0V+jJhGfHmZRXtVmEd9L4BAgAQAAAAAgECABJZIei6YRn5X
mEV+WZhFflmYRn5FkkltBwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAn0CACJhFfEqYRX5ZmEV+WZhGfk2WS3gRAAAAAICA
gAKaRoA6l0Z+gJdFfoyXRX6Ml0V+jJdFfoyXRX6Ml0V+jJdFfoyXRX6Ml0V+jJdFfoyXRX6Ml0V+
jJlGfn+ZR35BmTNmBQAAAACWS4cRmEZ+TZhFflmYRX5ZlkZ+SZJJbQcAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACqVVUDmUWARphFflmY
RX5ZlkeARJJJbQcAAAAAmUJ8I5lGfHWXRX6Ml0V+jJdFfoyXRX6MmEV9cppHflOZR35Bl0R9MZlG
fTeXSHxAmEh8UphFfHeXRX6Ml0V+jJdFfoyXRX6MmUV9eplCfCMAAAAAkkltB5hGfkWYRX5ZmEV+
WZtGfkWAgIACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAACYQ305mEV+WZhFflmWRHxEgECABAAAAACZRn03mEZ+ipdFfoyXRX6MmUZ+hJdEfkee
PXkVAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACPQIAQmEZ6RZdGfoCXRX6Ml0V+jJhG
fYuXSHxAAAAAAJkzZgWYRn5FmEV+WZhFflmZRn03AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJlEgB6YRX5ZmEV+WZpGfEyqVYAGAAAAAJhFfEqXRX6M
l0V+jJhGf4uaR35TlUCADAAAAACAgIACk0V7NJpGfWKXRX2JmEZ+kphFfZeXRn2HmkZ9YplHejL/
AAABAAAAAIBAgAiWRnxQmEZ/i5dFfoyXRX6MmEeASAAAAACSSW0HmEZ+TZhFflmYRX5Zm0mAHAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAmTNmBZpFflGYRX5ZmEZ+
V59QgBAAAAAAlUd+QZdFfoyXRX6MmEV+iJxJfTEAAAAAqlWqA5lFe1WYRn2rmEZ+x5hGfseYRn7H
mEZ+x5hGfseYRn7HmEZ+x5hGfseYRn2rmEZ8VP+AgAIAAAAAmEmAKplHfYWXRX6Ml0V+jJdHgDYA
AAAAjkeAEphGfleYRX5ZmUZ8UIBAgAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAmUR9LZhFflmYRX5ZnEaALAAAAACZSXwjmEZ9i5dFfoyZR32Fm0Z8IQAAAACZTYAemEZ8
nJhGfseYRn7HmEZ+x5hGfseZRn3AmUd9rJlHfayXRn3CmEZ+x5hGfseYRn7HmEZ+x5hFfJ6XSIAg
AAAAAJZEeCKYRX6Il0V+jJlHf4mWRHgiAAAAAJZIei6YRX5ZmEV+WZdGeiwAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAQIAEmEZ8VJhFflmWRnxQqlWqA5kzZgWZRX16l0V+jJhG
f4uYSXkqAAAAAJNGgCiXR3y7mEZ+x5hGfseZR327lkR9cJZIgyeqVaoDAAAAAAAAAACqVaoDk0aA
KJlHfXCZR327mEZ+x5hGfseYR364lkiDJwAAAACWRn0zmEZ/i5dFfoyZRnx1gICAAoBAgASXRXtR
mEV+WZpHflOqVaoDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACVR3gkmEV+WZhFflmW
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
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAP///////wAA////////AAD///////8AAP//wAP//wAA//4AAH//AAD/
+AAAH/8AAP/wA8AP/wAA/8A4HAP/AAD/gIABAf8AAP8CAABA/wAA/gQAACB/AAD+CA/wEH8AAPwQ
IAQIPwAA+CCAAQQfAAD4QQAAgh8AAPACAYBADwAA8IQP8CEPAADwAAEAIA8AAOEIAAAwhwAA4QAB
AfCHAADhEIPB+IcAAOAQhmH4BwAA4hAMMIhHAADiEQgQiEcAAOIRCBCIRwAA4hAMMAhHAADgEIZh
CAcAAOEQg8EIhwAA4QAAAACHAADhCAAAEIcAAPAAAAAADwAA8IQMMCEPAADwAgGAYA8AAPhBAADy
HwAA+CCAAfwfAAD8MCAH+D8AAP44D/PwfwAA//wAA+B/AAD//gADwP8AAP//gAMB/wAA///4HAP/
AAD//8PAD/8AAP//wAAf/wAA///AAH//AAD//8AD//8AAP///////wAA////////AAD///////8A
ACgAAAAgAAAAQAAAAAEAIAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACO
OXEJl0iAIJlGfTeXRnxCmkh+R5dGfEyZR35BlUN9NZVHeCSSSW0HAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAACcR4ASmUSAPJpGfViYRX5ZmEV+WZhFflmYRnxUmEh8UphFflmYRX5ZmEV+WZhGflebR309
lEN5EwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAACAQIAElkR7OJhFflmYRX5ZmUV8RplGgCifUIAQAAAAAQAAAAAAAAAAqlWqA49Q
gBCZRoAomEd8SJhFflmaRn1Ymkh9NYBVgAYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAmUR3D5lHfkuYRX5Zl0R+R5tDehf///8BlkR4IphEe0+XR3xl
l0V9dphFe3KYR31olkR+S59FfCX///8Bl0Z0FppGfkmYRX5Zl0Z8TKJGiwsAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJVAgAyZRnxQmEV+WZpFgDCAAIAClkiALphG
fHmXRX6Ml0V+jJdFfoyXR36CmUZ+hJdFfoyXRX6Ml0V+jJlGfHWXRH0x/wD/AZhHfS+YRX5ZlkZ8
UJJJgA4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACSSW0HmkZ8TJhFflmUQ3km
mWaZBZlGfl+XRX6Ml0V9iZdHfVaYRXwlmWaZBQAAAAAAAAAAmWaZBZZBfCeaR31WmUd/iZdFfoyX
RntbqlWABpZBfCeYRX5ZmUd+S4BAgAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJdH
gDaYRX5Zm0iALpJJbQeZRn1ul0V+jJhHfWiZQIAUpEmADpdGfViYRn2LmEd9qZhHfamZRn6KmEN7
V5lEdw+OR4ASmUd+a5dFfoyYRn5tmTNmBZdEfTGYRX5ZlkR7OAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAACZTYAUmkZ9WJZGfkn/AP8BmEV9XJdFfoyaRn1Yv0BABJhGfFSYRn6+mEZ+x5hGfseX
R3y7mUd9u5hGfseYRn7Hl0Z9vZhGfFS/QEAEmEh+WZdFfoybR31egACAApdEfkeYRX5ZjkeAEgAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAJhGgD6YRX5Zkkl5FZdEfTGXRX6MmUV8a79AQASWRn51mEZ+
x5hHfbeXRn1YnEeAEgAAAAAAAAAAlEN5E5dGfViYR364mEZ+x5hGfXS/QIAEmUZ+aZdFfoyZSn0t
m0N6F5hFflmZRHs8AAAAAAAAAAAAAAAAAAAAAAAAAACSSW0HmEZ+V5hHfEiAgIACl0V9dpdFfYmO
R4ASmEN7V5hGfseXRXyilEN5E5dGgCyXR32FlEN5E5hGfkWYRn2Pl0aALJRDeROZRX6imEZ+x5ZE
gCKZQIAUl0V9iZlGfXj///8BmUWARppGfViOOXEJAAAAAAAAAAAAAAAAAAAAAJVHeCSYRX5Zlkh8
J5ZBfCeXRX6MmUh+VZ9QgBCYRn2+mEd9t5xHgBKYR31emEZ9+phGff+XRH5HmEd+lJhGff+YRn36
l0d+XZVAagyXRX07/wAAAQAAAACaR31Wl0V+jJZEeCKZRoAomEV+WZdIgCAAAAAAAAAAAAAAAAAA
AAAAmkh9NZhFflmPUIAQmkZ8TJdFfoyfRXwlmEV+WZhGfseYQ3tXmUR9LZhGffqYR33rmEV8SgAA
AACqVVUDlkV8TphHfeuYRn36l0aALAAAAAAAAAAAAAAAAJ9FfCWXRX6MmER7T5ZLeBGYRX5ZmUZ9
NwAAAAAAAAAAAAAAAAAAAACXRnxCmEV+WapVqgOYRn1qmEZ+ipkzZgWZRX6MmEZ+x5ZLhxGYRn2V
mEZ9/5dGfEz/AAABl0Z+W5lHfVr/AAABl0Z8TJhGff+YRnycAAAAAAAAAAAAAAAAqlWABpdFfoyX
R3xl/wAAAZhFflmYRX5DAAAAAAAAAAAAAAAAAAAAAJdGfEyXRX5RAAAAAJhGfXSXR36CAAAAAJlG
faeWRn23AAAAAJhGfsuYRn33mTNmBZhFfVyYRn3/mEZ9/5dGfluAgIACmEZ98ZhGfdIAAAAAl0Z8
gJhHfncAAAAAl0d+gplGfHUAAAAAmEaAVJhHfEgAAAAAAAAAAAAAAAAAAAAAlkZ+SZhGfFQAAAAA
mEV8d5dGfoAAAAAAmUZ9p5ZGfbcAAAAAmEZ91JhGfe//AAABmEV9XJhGff+YRn3/l0Z+W5kzZgWY
Rn33l0V9ygAAAACYR323mEV8pgAAAACZRn6EmEV9cgAAAACYRHxSmUd+SwAAAAAAAAAAAAAAAAAA
AACYRX5DmkZ9WAAAAAGZRn1mmEZ/i5lmmQWZRX6MmEZ+x59QgBCZRn2dmEZ9/5hFfEqAgIACmEV9
XJhFfVz/AAABl0Z8TJhGff+YR36UnEeAEphGfseYRn2LmWaZBZhGfYuYR31oqlWqA5hFflmZR35B
AAAAAAAAAAAAAAAAAAAAAJZEeziYRX5Zj0CAEJhHfk+XRX6MlUeAJJlHfVqYRn7HmEN7V5ZIgC6Y
Rn36l0Z96plHfkuZM2YF/wAAAZlHfkuXRn3qmEZ9+plEfS2YRn5XmEZ+x5hGfleaQ4Aml0V+jJlH
fkuPUIAQmEV+WZVDfTUAAAAAAAAAAAAAAAAAAAAAm0Z8IZhFflmWQXwnlUeAJJdFfoyaR35TmUR3
D5hGfr6YR323lEN5E5dFfWCYRn37mEZ9/5hGffaYRn3wmEZ9/5hGffqZRn5fnEeAEphHfbeYRn2+
j0CAEJdHfVaXRX6MmkOAJplGgCiYRX5ZlUd4JAAAAAAAAAAAAAAAAAAAAACZTYAKmEV+WZhGfkWA
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
'@
[IO.File]::WriteAllBytes("$Dir\onionmind.ico", [Convert]::FromBase64String($OnionIco))

# What the shortcut and the `onionmind` command run: start Tor Browser if it
# isn't up (search fails closed without it; the chat doesn't need it), give
# SOCKS up to 45s, then the chat - queries typed at you> never touch history.
@'
param([switch]$UI)
$Host.UI.RawUI.WindowTitle = 'Onionmind'
$tor = Get-Process firefox -ErrorAction SilentlyContinue |
       Where-Object { $_.Path -like '*Tor Browser*' } | Select-Object -First 1
if (-not $tor) {
  # GetFolderPath('Desktop') first: OneDrive Known Folder Move relocates the
  # Desktop on most Windows 11 setups, so $env:USERPROFILE\Desktop is simply
  # wrong there and Tor Browser was never found. The rest are winget's and the
  # installer's real destinations.
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
Set-Location ~          # /save <file> lands in the home dir
if ($UI -or ($args.Count -eq 0)) {
  & pythonw "$PSScriptRoot\onionmind.py" --ui
} else {
  & python "$PSScriptRoot\onionmind.py" @args
}
'@ | Set-Content "$Dir\onionmind-launch.ps1" -Encoding UTF8

# `onionmind` is the way in: same engine, callable from any terminal. ollama
# stays underneath as the server - it stops being something you type.
# Repository-aware coding agent, kept separate from Onionmind's local Tor chat.
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor-search.js' -OutFile "$Dir\dsh-onionmind-tor-search.js"
$dshPatch = (Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor.patch.yml').Content
$dshPatch.Replace('@ONIONMIND_DSH_PLUGIN@', (($Dir + '\dsh-onionmind-tor-search.js') -replace '\\', '/')) |
  Set-Content "$Dir\dsh-onionmind-tor.patch.yml" -Encoding UTF8
@'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$Model = '@ONIONMIND_MODEL@'
$tor = Get-Process firefox -ErrorAction SilentlyContinue |
       Where-Object { $_.Path -like '*Tor Browser*' } | Select-Object -First 1
if (-not $tor) {
  # GetFolderPath('Desktop') first: OneDrive Known Folder Move relocates the
  # Desktop on most Windows 11 setups, so $env:USERPROFILE\Desktop is simply
  # wrong there and Tor Browser was never found. The rest are winget's and the
  # installer's real destinations.
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
$torPort = $null
for ($i = 0; $i -lt 45; $i++) {
  foreach ($port in 9150, 9050) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
      $torPort = $port
      break
    }
  }
  if ($torPort) { break }
  Start-Sleep 1
}
if (-not $torPort) {
  Write-Host 'Tor: NOT READY - open Tor Browser and click Connect.' -ForegroundColor Red
  exit 1
}
Write-Host ("Tor: UP (SOCKS {0})" -f $torPort) -ForegroundColor Green
$env:ONIONMIND_PY = Join-Path $PSScriptRoot 'onionmind.py'
$env:ONIONMIND_PYTHON = 'python'
$patch = Join-Path $PSScriptRoot 'dsh-onionmind-tor.patch.yml'
& ollama launch dsh --model $Model -- --patch $patch @Arguments
exit $LASTEXITCODE
'@.Replace('@ONIONMIND_MODEL@', $name) |
  Set-Content "$Dir\onionmind-code-launch.ps1" -Encoding UTF8
@"
@echo off
title Onionmind Code
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content "$Dir\onionmind-code.cmd" -Encoding ASCII
@"
@echo off
title Onionmind
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content "$Dir\onionmind.cmd" -Encoding ASCII
@"
@echo off
title Onionmind Chat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content "$Dir\onionmind-chat.cmd" -Encoding ASCII

@"
@echo off
title Onionmind Update
powershell -NoProfile -ExecutionPolicy Bypass -Command "`$u=Join-Path `$env:TEMP 'onionmind-update.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.ps1' -OutFile `$u; & `$u -InstallDir '%~dp0'; exit `$LASTEXITCODE"
"@ | Set-Content "$Dir\onionmind-update.cmd" -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$Dir*") {
  [Environment]::SetEnvironmentVariable('Path', "$userPath;$Dir", 'User')  # registry, no setx truncation
  Say "onionmind command installed - open a NEW terminal and just type: onionmind"
}

$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Onionmind.lnk'))
$lnk.TargetPath     = 'powershell.exe'
$lnk.Arguments      = "-NoProfile -ExecutionPolicy Bypass -File `"$Dir\onionmind-launch.ps1`" -UI"
$lnk.WorkingDirectory = $Dir
$lnk.IconLocation   = "$Dir\onionmind.ico,0"
$lnk.Description    = 'Local uncensored model + Tor web search'
$lnk.Save()

Write-Host ""
Say "Ready"
Write-Host "  Harness:     onionmind   (DeepSeek Harness via Ollama + Tor)"
Write-Host "  Chat:        onionmind-chat   (local desktop chat)"
Write-Host "  Coding:      onionmind-code   (same Harness launcher)"
Write-Host "  Updates:     onionmind-update   (code only, model untouched)"
if ($Vision) { Write-Host "  Images:      $name-vision   (vision model)" }
Write-Host "  Web search:  python `"$Dir\onionmind.py`" `"your question`""
Write-Host "               (Tor Browser must stay open - it owns the SOCKS proxy on 9150)"
Write-Host "  Desktop:     Onionmind - double-click to chat"
