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
  Say "Installing Ollama"
  winget install --id Ollama.Ollama -e --silent --accept-package-agreements `
                 --accept-source-agreements --disable-interactivity
}
if (-not (Test-Path $O)) { throw "Ollama install failed - get it from https://ollama.com" }

# --- 2. Tor Browser (provides the SOCKS proxy on 9150) ---------------------
$TorExe = "$env:USERPROFILE\Desktop\Tor Browser\Browser\firefox.exe"
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
  foreach ($c in @($TorExe, "$env:LOCALAPPDATA\Tor Browser\Browser\firefox.exe",
                   "$env:PROGRAMFILES\Tor Browser\Browser\firefox.exe")) {
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
#   fast           - always the small model, whatever the hardware
#   27b            - always Qwen3.8-27B, even if it runs on CPU at 1-2 tok/s
# There is NO small Qwen3.8: as of Aug 2026 the family is 27B and a 2.4T MoE, nothing else,
# and no generation newer than 3.8 exists. Qwen3.5 is the newest line WITH small dense
# models, so that is what "fast" means here.
# MTP builds keep the multi-token-prediction head; ollama uses it for speculative decoding.
$want = if ($env:ONIONMIND_MODEL) { $env:ONIONMIND_MODEL.ToLower() } else { 'auto' }
if ($want -notin @('auto','fast','27b')) { throw "ONIONMIND_MODEL must be auto, fast or 27b (got '$want')" }
if ($want -eq 'auto') { $want = if ($vram -ge 8000) { '27b' } else { 'fast' } }

$Vision = ($want -eq '27b')          # the mmproj is built for the 27B architecture
if ($want -eq '27b') {
  if     ($vram -ge 17000) { $repo='hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF'; $file='Qwen3.8-27B-abliterated-mtp-Q4_K_M.gguf' }
  elseif ($vram -ge 12000) { $repo='soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf'
                             $file='qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf' }
  else                     { $repo='hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF'; $file='Qwen3.8-27B-abliterated-IQ2_M.gguf' }
  if ($vram -lt 8000) { Write-Host "    ${vram} MiB VRAM: the 27B will run mostly on CPU (~1-2 tok/s)." -ForegroundColor Yellow }
  $name = "qwen38-uncensored"
  Say "Model: Qwen3.8-27B (uncensored)"
} else {
  if ($vram -ge 6000) { $repo='mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF'; $file='Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf'; $sz='9B' }
  else                { $repo='mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF'; $file='Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf'; $sz='4B' }
  $name = "qwen35-$($sz.ToLower())-uncensored"   # name it for what it IS, not 3.8
  Say "Model: Qwen3.5-$sz (uncensored) - fits entirely in VRAM"
  if ($vram -lt 8000) {
    Write-Host "    No small Qwen3.8 exists, so this is one generation back but far faster." -ForegroundColor Yellow
  }
  Write-Host "    Vision skipped (27B-only). Set ONIONMIND_MODEL=27b for Qwen3.8-27B instead." -ForegroundColor Yellow
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
  Say "Starting Ollama server"
  Start-Process -FilePath $O -ArgumentList 'serve' -WindowStyle Hidden
  foreach ($i in 1..30) { Start-Sleep 2; if (Ollama-Up) { break } }
}
if (-not (Ollama-Up)) { throw "Ollama server did not come up on 11434" }

# --- 6. Weights ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Say "Downloading $file (resumable, ~10-16GB)"
# ponytail: curl.exe -C - resumes a dropped download; no retry logic of our own
curl.exe -L -C - --fail -o "$Dir\$file" "https://huggingface.co/$repo/resolve/main/$file"

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
if ($LASTEXITCODE -ne 0) { throw "ollama create failed ($LASTEXITCODE) - see output above" }

# --- 7b. Vision (27B only - the mmproj is built for that architecture) ------
if ($Vision) {
# The mmproj is the vision tower in its own file - architecture-specific, not
# quant-specific, so this one projector binds to any Qwen3.8-27B build (verified
# against the 3.69bpw MTP model it is paired with here). Shares the base blob, so
# it costs ~900MB on top, not another full model.
$vis = "Qwen3.8-27B-Uncensored-vision-f16.gguf"
curl.exe -L -C - --fail --noproxy '*' -o "$Dir\$vis" `
  "https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$vis"
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
import sys, re, html, secrets, urllib.parse, requests

for _s in (sys.stdout, sys.stderr):              # Windows console defaults to cp1252,
    try: _s.reconfigure(encoding="utf-8")        # which mangles en-dashes and km2
    except Exception: pass

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL  = "qwen38-uncensored"
NOPROXY = {"http": None, "https": None}          # ollama is local - never via Tor
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


def turn(messages):
    """Run one user turn to completion, letting the model search as often as it needs."""
    for _ in range(6):                            # ponytail: hard cap, not a retry policy
        msg = _ask_ollama(messages)
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


if __name__ == "__main__":
    tor_check()
    history = []
    if len(sys.argv) > 1:
        history.append({"role": "user", "content": " ".join(sys.argv[1:])})
        print("\n" + turn(history))
    else:
        print("Chat - it searches over Tor when it needs to. Ctrl-C to quit.\n")
        while True:
            try:
                q = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q:
                history.append({"role": "user", "content": q})
                print("\n" + turn(history) + "\n")
'@
# Point the tool at whichever model was installed - on the fast tier the model is NOT
# named qwen38-uncensored, and the embedded default would reference a model that does
# not exist. Plain .Replace(), not a regex: this file has CRLF endings and a $
# anchor cannot match before the carriage return. The shell installer uses sed.
$search = $search.Replace('MODEL  = "qwen38-uncensored"', 'MODEL  = "' + $name + '"')
# Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1, and a BOM ahead
# of the shebang breaks ./onionmind.py on Linux. WriteAllText with $false does not.
[System.IO.File]::WriteAllText("$Dir\onionmind.py", $search, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Say "Ready"
Write-Host "  Chat:        $O run $name"
if ($Vision) { Write-Host "  Images:      $O run $name-vision   (then give it an image path)" }
Write-Host "  Web search:  python `"$Dir\onionmind.py`" `"your question`""
Write-Host "               (Tor Browser must stay open - it owns the SOCKS proxy on 9150)"
