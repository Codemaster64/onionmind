@echo off
rem Onionmind one-click installer. This file is BOTH a batch file and the
rem full PowerShell installer, appended below the marker. Built by build.py.
set "ONIONMIND_SETUP=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = [IO.File]::ReadAllText($env:ONIONMIND_SETUP, [Text.Encoding]::UTF8); iex $c.Substring($c.IndexOf('#__ONION'+'MIND_PS__'))"
exit /b %ERRORLEVEL%
#__ONIONMIND_PS__
# Qwen3.8-27B uncensored + Tor web search, on Ollama. One paste. Re-runnable.
[CmdletBinding()]
param(
  [switch]$Audit,
  [switch]$AllowDirectNetwork,
  [switch]$Yes
)

# Deprecated compatibility installer. The supported portable bootstrap is
# offline-first; this source installer also refuses network access by default.
$ErrorActionPreference = 'Stop'
# Orca terminal: no ANSI soup in logs. $PSStyle is pwsh 7+ only - guard it or this
# whole script dies on line 4 under Windows PowerShell 5.1, which many machines default to.
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSStyle.OutputRendering = 'PlainText' }
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new() } catch {}

# Set ONIONMIND_DIR to put weights elsewhere. Keep it on an NVMe: a Storage Space / HDD
# makes imports crawl and 10x's model load time.
$Dir = if ($env:ONIONMIND_DIR) { $env:ONIONMIND_DIR } else { "$env:LOCALAPPDATA\qwen" }
$Dir = [IO.Path]::GetFullPath($Dir)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

if ($Audit) {
  Say "Deprecated compatibility-install audit (local only)"
  Write-Host "  Install directory: $Dir"
  Write-Host "  Existing core:     $(Test-Path -LiteralPath (Join-Path $Dir 'onionmind.py') -PathType Leaf)"
  Write-Host "  Ollama installed:  $([bool](Get-Command ollama.exe -ErrorAction SilentlyContinue))"
  Write-Host "  Node installed:    $([bool](Get-Command node.exe -ErrorAction SilentlyContinue))"
  Write-Host "  Git installed:     $([bool](Get-Command git.exe -ErrorAction SilentlyContinue))"
  Write-Host "Use onionmind-bootstrap.cmd from the portable bundle for the full version inventory."
  exit 0
}

Write-Host "Deprecated source installer direct-network plan" -ForegroundColor Yellow
Write-Host "  winget package sources: Ollama, Python, and Tor Browser when missing"
Write-Host "  GitHub API/raw assets, Hugging Face model files, and the configured Python package index"
Write-Host "  Model weights may be several gigabytes. Tor Browser is never opened automatically."
if (-not $AllowDirectNetwork) {
  [Console]::Error.WriteLine("Refusing network access. Use the portable bootstrap, or re-run with -AllowDirectNetwork after reviewing this plan.")
  exit 4
}
if (-not $Yes) {
  $answer = Read-Host "Continue with the deprecated direct-network installer? [y/N]"
  if ($answer -notmatch '^(?i:y|yes)$') {
    Write-Host "Canceled. No network request was started."
    exit 3
  }
}

# --- 1. Ollama -------------------------------------------------------------
$O = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $O)) {
  Say "Installing the local model engine"
  winget install --id Ollama.Ollama -e --silent --accept-package-agreements `
                 --accept-source-agreements --disable-interactivity
}
if (-not (Test-Path $O)) { throw "The local model engine could not be installed." }

function Resolve-OnionmindPython {
  $candidates = New-Object 'System.Collections.Generic.List[string]'
  $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($launcher) {
    $resolved = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0 -and $resolved) {
      $candidates.Add([string](@($resolved)[-1]))
    }
  }
  foreach ($candidate in @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:ProgramFiles 'Python312\python.exe')
  )) {
    if ($candidate) { $candidates.Add($candidate) }
  }
  $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($pythonCommand) { $candidates.Add($pythonCommand.Source) }

  foreach ($candidate in $candidates | Select-Object -Unique) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
    try {
      $versionText = & $candidate -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
      if ($LASTEXITCODE -eq 0 -and [version]$versionText -ge [version]'3.10') {
        return [IO.Path]::GetFullPath($candidate)
      }
    } catch { }
  }
  return $null
}

$py = Resolve-OnionmindPython
if (-not $py) {
  Say "Installing Python 3.12 for the native workbench"
  winget install --id Python.Python.3.12 -e --silent --accept-package-agreements `
                 --accept-source-agreements --disable-interactivity
  $py = Resolve-OnionmindPython
}
if (-not $py) {
  throw "Python 3.10 or newer is required. Install Python, then rerun Onionmind Setup."
}
try { & $py -m pip install --user --disable-pip-version-check tkinterdnd2 2>$null } catch { }

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

# Detect an already-running local proxy without starting a browser or contacting
# the internet. Onionmind starts tor.exe itself, hidden, only after one-turn search
# permission is granted in the app.
function Tor-Up { foreach ($p in 9150,9050) {
    if (Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue) { return $true } }
  return $false }
if (Tor-Up) { Say "Local Tor proxy detected" }
else { Write-Host "  Tor is off - Onionmind stays local-only until you allow search for a turn" -ForegroundColor Yellow }

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
[IO.Directory]::CreateDirectory($Dir) | Out-Null
$weightsPath = Join-Path $Dir $file
$modelFilePath = Join-Path $Dir 'Modelfile'

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
  $got = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $want) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    throw "$([IO.Path]::GetFileName($Path)) downloaded corrupt (sha256 $got, expected $want) - deleted it; rerun to try again"
  }
  Say "verified $([IO.Path]::GetFileName($Path))"
}

Say "Downloading $file (resumable, ~10-16GB)"
# ponytail: curl.exe -C - resumes a dropped download; no retry logic of our own
$weightsUrl = "https://huggingface.co/$repo/resolve/main/$file"
curl.exe -L -C - --fail -o $weightsPath $weightsUrl
Verify-Download $weightsPath $weightsUrl

# --- 7. Model --------------------------------------------------------------
# num_gpu 99 = all layers on GPU; ollama's auto-split is too conservative and silently
# leaves VRAM unused. It needs desktop headroom too: if speed swings while browsers are
# open, the model is spilling to shared memory - drop this to ~56.
# TEMPLATE is required: a bare GGUF import gets `{{ .Prompt }}`, which makes the model
# echo prompts and leak system text instead of chatting.
@"
FROM $weightsPath
PARAMETER num_gpu 99
PARAMETER num_ctx 16384
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
"@ | Set-Content -LiteralPath $modelFilePath -Encoding UTF8
Say "Registering model"
& $O create $name -f $modelFilePath
if ($LASTEXITCODE -ne 0) { throw "Model registration failed ($LASTEXITCODE) - see output above" }

# --- 7b. Vision (27B only - the mmproj is built for that architecture) ------
if ($Vision) {
# The mmproj is the vision tower in its own file - architecture-specific, not
# quant-specific, so this one projector binds to any Qwen3.8-27B build (verified
# against the 3.69bpw MTP model it is paired with here). Shares the base blob, so
# it costs ~900MB on top, not another full model.
$vis = "Qwen3.8-27B-Uncensored-vision-f16.gguf"
$visUrl = "https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$vis"
$visionPath = Join-Path $Dir $vis
$visionModelFilePath = Join-Path $Dir 'Modelfile.vision'
curl.exe -L -C - --fail --noproxy '*' -o $visionPath $visUrl
Verify-Download $visionPath $visUrl
(Get-Content -LiteralPath $modelFilePath -Raw).Replace("FROM $weightsPath", "FROM $weightsPath`nFROM $visionPath") |
  Set-Content -LiteralPath $visionModelFilePath -Encoding UTF8
Say "Registering vision model"
& $O create "$name-vision" -f $visionModelFilePath
  if ($LASTEXITCODE -ne 0) { Write-Host "    vision model failed to build - text model is fine" -ForegroundColor Yellow }
}

# --- 8. Tor search tool ----------------------------------------------------
Say "Installing Python deps (requests, PySocks)"
& $py -m pip install --quiet --disable-pip-version-check requests PySocks
if ($LASTEXITCODE -ne 0) { throw "Python dependencies could not be installed." }

$search = @'
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
'@
# Point the tool at whichever model was installed - on the fast tier the model is NOT
# named inferno, and the embedded default would reference a model that does
# not exist. Plain .Replace(), not a regex: this file has CRLF endings and a $
# anchor cannot match before the carriage return. The shell installer uses sed.
$search = $search.Replace('MODEL  = "inferno"', 'MODEL  = "' + $name + '"')
# Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1, and a BOM ahead
# of the shebang breaks ./onionmind.py on Linux. WriteAllText with $false does not.
$searchPath = Join-Path $Dir 'onionmind.py'
[System.IO.File]::WriteAllText($searchPath, $search, $Utf8NoBom)

# The native desktop workbench is kept in focused modules, then embedded here by
# build.py so the one-click installer remains complete and reproducible.
$desktopCore = @'
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
    "strip_thinking",
    "sanitize_messages",
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
            "messages": sanitize_messages(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_THINK_TAG = re.compile(r"<\s*(/?)\s*think(?:\s[^>]*)?>", re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


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
        # If generation ended while spelling an opening tag, keep only the
        # completed visible prefix. This also covers transcripts that contain
        # an earlier complete block followed by a truncated later block.
        partial = tail.casefold().find("<think")
        visible.append(tail[:partial] if partial >= 0 else tail)
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
    "actions stop safely. Agent network access is separate from Tor search."
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
'@
$desktopUi = @'
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
    QCheckBox,
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
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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


_THINK_TAG = re.compile(r"<\s*(/?)\s*think(?:\s[^>]*)?>", re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


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
        partial = tail.casefold().find("<think")
        visible.append(tail[:partial] if partial >= 0 else tail)
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
    """Hide ``<think>`` blocks without leaking split tag fragments.

    The model transport is free to divide text at any byte boundary.  Keep only
    the small amount of undecided tag text between calls; reasoning itself is
    discarded instead of accumulated.  Tool use can cause several model
    responses to share one callback, so opening tags remain detectable after
    ordinary answer text has already streamed.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._state = "leading"
        self._pending = ""
        self._finished = False

    @staticmethod
    def _trailing_marker_prefix(text: str, marker: str) -> str:
        maximum = min(len(text), len(marker) - 1)
        for length in range(maximum, 0, -1):
            if text.endswith(marker[:length]):
                return text[-length:]
        return ""

    def feed(self, chunk: Any) -> str:
        """Return only newly visible answer text from one transport chunk."""
        if self._finished:
            return ""
        data = _as_text(chunk)
        visible: list[str] = []

        while data:
            if self._state == "visible":
                combined = self._pending + data
                self._pending = ""
                open_at = combined.find(self._OPEN)
                if open_at < 0:
                    suffix = self._trailing_marker_prefix(combined, self._OPEN)
                    if suffix:
                        visible.append(combined[:-len(suffix)])
                        self._pending = suffix
                    else:
                        visible.append(combined)
                    break
                visible.append(combined[:open_at])
                data = combined[open_at + len(self._OPEN):]
                self._state = "thinking"
                continue

            if self._state == "leading":
                self._pending += data
                data = ""
                content = self._pending.lstrip()
                if not content:
                    continue
                if self._OPEN.startswith(content):
                    if content == self._OPEN:
                        self._pending = ""
                        self._state = "thinking"
                    continue
                if content.startswith(self._OPEN):
                    data = content[len(self._OPEN):]
                    self._pending = ""
                    self._state = "thinking"
                    continue
                data = self._pending
                self._pending = ""
                self._state = "visible"
                continue

            combined = self._pending + data
            self._pending = ""
            close_at = combined.find(self._CLOSE)
            if close_at < 0:
                self._pending = self._trailing_marker_prefix(combined, self._CLOSE)
                break
            data = combined[close_at + len(self._CLOSE):]
            self._state = "visible"

        return "".join(visible)

    def finish(self) -> None:
        """Close a completed stream without exposing an undecided tag prefix."""
        self._pending = ""
        self._state = "finished"
        self._finished = True

    def abort(self) -> None:
        """Drop buffered model output after stop or failure."""
        self.finish()


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


def _ui_animations_enabled() -> bool:
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

    def list(self) -> list[Any]:
        if self.store is not None:
            try:
                return [self._clean_session(item) for item in self.store.list()]
            except Exception:
                return []
        try:
            return [
                self._clean_session(item)
                for item in json.loads(self.fallback.value("sessions", "[]"))
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
        "Agent network access is separate from Tor search."
    )

    def __init__(self, desktop_core: Any) -> None:
        self.desktop_core = desktop_core
        self.spec = None
        if desktop_core is not None and hasattr(desktop_core, "HarnessSpec"):
            try:
                self.spec = desktop_core.HarnessSpec()
            except Exception:
                self.spec = None

    @property
    def limitation(self) -> str:
        if self.spec is not None:
            value = _as_text(getattr(self.spec, "limitation", "")) or _as_text(
                getattr(self.desktop_core, "HARNESS_LIMITATION", "")
            ) or self.FALLBACK_LIMITATION
            return _brand_runtime_text(value)
        return self.FALLBACK_LIMITATION

    def check(self) -> tuple[bool, str]:
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
        if self.spec is not None:
            command = self.spec.build(model=model, task=task, cwd=cwd)
            return [_as_text(part) for part in _field(command, "argv", ())], _as_text(_field(command, "cwd", cwd))
        executable = shutil.which("ollama") or "ollama"
        return [executable, "launch", "dsh", "--model", model, "--", "--profile", "headless", task], cwd


class LeftRail(QWidget):
    newTaskRequested = Signal()
    openFolderRequested = Signal()
    sessionSelected = Signal(str)
    projectSelected = Signal(str)
    modelsRequested = Signal()
    settingsRequested = Signal()
    exportRequested = Signal()
    archiveRequested = Signal(str)

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
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        project_header.addWidget(add_project)
        layout.addLayout(project_header)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(240)
        self.projects.setAccessibleName("Projects")
        self.projects.itemClicked.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        layout.addWidget(self.projects)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color:#3a3732;")
        layout.addWidget(divider)
        session_header = QHBoxLayout()
        session_label = QLabel("SESSIONS")
        session_label.setObjectName("sectionTitle")
        archive = QToolButton()
        archive.setObjectName("bareButton")
        archive.setIcon(_icon("archive"))
        archive.setToolTip("Archive selected session")
        archive.setAccessibleName("Archive selected session")
        archive.clicked.connect(self._archive_selected)
        archive.setEnabled(False)
        self.archive_button = archive
        session_header.addWidget(session_label)
        session_header.addStretch(1)
        session_header.addWidget(archive)
        layout.addLayout(session_header)
        self.sessions = QListWidget()
        self.sessions.setAccessibleName("Saved sessions")
        self.sessions.itemClicked.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.currentItemChanged.connect(
            lambda current, previous: self.archive_button.setEnabled(current is not None)
        )
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

    def set_conversation_available(self, available: bool) -> None:
        self.export_button.setEnabled(bool(available))

    def add_project(self, path: str, select: bool = True) -> None:
        if not path:
            return
        normalized = os.path.normcase(os.path.abspath(path))
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if os.path.normcase(_as_text(item.data(Qt.ItemDataRole.UserRole))) == normalized:
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
            "Prompts and model inference stay on this machine. Search queries are the explicit exception and use Tor when Chat invokes search. Agent network access is a separate boundary."
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
        copy_label = QLabel(
            "Installed model identifiers stay visible. Pulling asks the local model service "
            "to download directly from its configured registry; that download is not Tor-routed."
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
        tor = QLabel("Only Chat search queries use Tor; a failed Tor check never falls back to direct search.")
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
        data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.data_root = Path(data_location or (Path.home() / ".onionmind")) / "desktop"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions")
        self.workspace_bridge = WorkspaceBridge(desktop_core)
        self.harness_bridge = HarnessBridge(desktop_core)
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
        self.model_status = StatusPill("Model", "Checking", "busy")
        toolbar_layout.addWidget(self.model_status)
        self.tor_status = StatusPill("Tor", "Off", "idle")
        self.tor_status.setToolTip(
            "Local background Tor state. Onionmind starts it without a browser window only after you allow search for a turn."
        )
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
        self.left_rail.openFolderRequested.connect(self.open_folder)
        self.left_rail.projectSelected.connect(self.select_workspace)
        self.left_rail.sessionSelected.connect(self.load_session)
        self.left_rail.modelsRequested.connect(self.open_model_manager)
        self.left_rail.settingsRequested.connect(self.open_settings)
        self.left_rail.exportRequested.connect(self.export_conversation)
        self.left_rail.archiveRequested.connect(self.archive_session)
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

        checker = getattr(self.core, "tor_proxy_port", None)
        if not callable(checker):
            self.tor_status.set_status("Off", "idle")
            return
        self.tor_probe_generation += 1
        generation = self.tor_probe_generation
        self.tor_phase = "probing"

        def tor_probe(signals: WorkerSignals) -> Any:
            del signals
            return checker()

        tor_worker = self._start_worker(tor_probe)
        tor_worker.signals.result.connect(
            lambda port, value=generation: self._tor_probe_complete(port, value)
        )
        tor_worker.signals.error.connect(
            lambda message, value=generation: self._tor_probe_failed(message, value)
        )

    def _start_worker(self, fn: Callable[[WorkerSignals], Any]) -> SafeWorker:
        worker = SafeWorker(fn, self.core)
        self._workers.add(worker)

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

    def _tor_probe_failed(self, message: str, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self.tor_phase = "off"
        self.tor_status.set_status("Off", "idle")
        self.tor_status.setToolTip(
            message + " Onionmind did not make an external request; search remains off."
        )
        self.inspector.append_activity("Background Tor is off; Chat remains local-only")

    def _tor_probe_complete(self, port: Any, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self._show_local_tor_state(port)

    def _show_local_tor_state(self, port: Any) -> None:
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
        if port and (managed_running or verified):
            self.tor_phase = "running"
            self.tor_status.set_status(f"Running · {port}", "good")
            if managed_running:
                self.tor_status.setToolTip(
                    "Onionmind's Tor process is running in the background; no Tor Browser or console window is open."
                )
                self.inspector.append_activity(f"Onionmind-owned background Tor running on local port {port}")
            else:
                self.tor_status.setToolTip(
                    "A pre-existing local proxy was verified as Tor. Onionmind did not start or adopt it."
                )
                self.inspector.append_activity(f"Pre-existing local Tor proxy verified on port {port}")
        elif port:
            self.tor_phase = "proxy"
            self.tor_status.set_status(f"Proxy · {port}", "warn")
            self.tor_status.setToolTip(
                "A local SOCKS listener was detected but not externally verified. Search still fails closed."
            )
            self.inspector.append_activity(f"Unverified local SOCKS listener detected on port {port}")
        else:
            self.tor_phase = "off"
            self.tor_status.set_status("Off", "idle")
            self.tor_status.setToolTip(
                "Tor is off. Onionmind starts it without a browser window only after one-turn search permission."
            )
            self.inspector.append_activity("Background Tor is off; Chat remains local-only")

    def _poll_tor_liveness(self) -> None:
        """Keep the only Tor indicator honest using local process/socket state."""
        if self.tor_phase not in ("running", "proxy"):
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
            self.search_consent.show()
            self.disclosure.setText("Private local chat · Tor search needs one-turn permission")
            self.composer.setPlaceholderText("Ask Onionmind anything…")
        else:
            self.approval_state.show()
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
        recent = [p for p in recent if os.path.normcase(p) != os.path.normcase(self.workspace)]
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
        if self.workspace and os.path.normcase(_as_text(snapshot.get("root"))) != os.path.normcase(self.workspace):
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
        self.left_rail.sessions.clearSelection()
        self.set_status("New task ready")
        self.focus_composer()

    def _session_title(self) -> str:
        first = next((_as_text(m.get("content")) for m in self.chat_messages if m.get("role") == "user"), "New session")
        first = re.sub(r"\s+", " ", first).strip()
        return first[:48] + ("…" if len(first) > 48 else "")

    def save_current_session(self) -> bool:
        if not self.chat_messages:
            return True
        try:
            # This is the final persistence boundary. A live tool round keeps
            # its raw structure in the worker's private history until it has
            # completed; only the copy owned by the UI is cleaned here.
            self.chat_messages = _sanitize_assistant_messages(self.chat_messages)
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
            self.set_status("Stop the active run before archiving a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to archive.")
            return
        session = self.session_objects.get(session_id)
        title = _as_text(_field(session, "title", "this session"))
        answer = QMessageBox.question(
            self,
            "Archive local session",
            f"Archive “{title}”? It will leave the active session list but remain in local archive storage.",
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
            self.set_status("The selected session could not be archived.")
            return
        self.session_objects.pop(session_id, None)
        if self.current_session is not None and _as_text(_field(self.current_session, "id")) == session_id:
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(item, "id")): item for item in sessions}
        self.left_rail.set_sessions(sessions, _as_text(_field(self.current_session, "id")) if self.current_session else None)
        self.set_status(f"Archived session: {title}")
        self.inspector.append_activity(f"Session archived locally: {title}")

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
            self.stop_active()
            return
        task = self.composer.toPlainText().strip()
        if not task and not self.attachments:
            self.set_status("Describe a task or attach a local file first.")
            self.focus_composer()
            return
        if not task:
            task = "Review the attached local files."
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
        search_allowed = self.mode == "chat" and self.search_consent.isChecked()
        self.search_consent.setChecked(False)
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
        self.search_consent.setEnabled(not running)
        if running:
            self.search_consent.setChecked(False)
        self.left_rail.projects.setEnabled(not running)
        self.left_rail.sessions.setEnabled(not running)
        if not running:
            self.stop_event = None
            self.harness_process = None
        self._sync_action_states()

    def _start_chat(self, allow_search: bool = False) -> None:
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
            self.tor_status.set_status("Starting", "busy")
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
                delta = stream_filter.feed(chunk)
                if delta:
                    signals.text.emit(delta)

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
                stream_filter.abort()
                raise
            stream_filter.finish()
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
        if kind == "tor_ready":
            port = event.get("port")
            if event.get("managed"):
                self.tor_phase = "running"
                self.tor_status.set_status(f"Running · {port}" if port else "Running", "good")
                self.tor_status.setToolTip(
                    "Tor is running as a background process; no Tor Browser or console window was opened."
                )
            else:
                self.tor_phase = "proxy"
                self.tor_status.set_status(f"Proxy · {port}" if port else "Proxy", "warn")
                self.tor_status.setToolTip(
                    "An existing local SOCKS listener was reused and will be verified before a query is sent."
                )
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Thinking")
            self.set_status(f"Background Tor ready · thinking with {self._describe_model(self.current_model_id())}…")
            self.inspector.append_activity("Background Tor ready; no browser window opened")
        elif kind == "tor_verified":
            port = event.get("port")
            self.tor_phase = "running"
            self.tor_status.set_status(f"Running · {port}" if port else "Running", "good")
            self.tor_status.setToolTip(
                "The background SOCKS path was verified as Tor after explicit search permission."
            )
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
        answer = _strip_thinking(payload.get("answer"))
        if not answer:
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
        self.set_status("Chat turn complete")
        self.inspector.append_activity("Chat turn completed locally")
        self._set_active(None)
        self.save_current_session()

    def _chat_failed(self, message: str) -> None:
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
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Stopping")
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
        self.tor_status.set_status("Running · 9150", "good")
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
        self.save_current_session()
        timer = getattr(self, "tor_liveness_timer", None)
        if timer is not None:
            timer.stop()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()
        self.terminal.stop()
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
'@
$desktopCorePath = Join-Path $Dir 'onionmind_desktop_core.py'
$desktopUiPath = Join-Path $Dir 'onionmind_desktop.py'
[System.IO.File]::WriteAllText($desktopCorePath, $desktopCore, $Utf8NoBom)
[System.IO.File]::WriteAllText($desktopUiPath, $desktopUi, $Utf8NoBom)

$desktopEnv = Join-Path $Dir 'desktop-env'
$desktopMarker = Join-Path $desktopEnv '.onionmind-desktop-ready'
Remove-Item -LiteralPath $desktopMarker -Force -ErrorAction SilentlyContinue
if ($py) {
  Say "Preparing native desktop runtime"
  & $py -m venv $desktopEnv
  $desktopEnvironmentCreated = ($LASTEXITCODE -eq 0)
  $desktopEnvironmentReady = $false
  if ($desktopEnvironmentCreated) {
    $desktopPython = Join-Path $desktopEnv 'Scripts\python.exe'
    if (Test-Path -LiteralPath $desktopPython -PathType Leaf) {
      & $desktopPython -m pip install --quiet --disable-pip-version-check requests PySocks PySide6-Essentials==6.11.1
      $desktopPipSucceeded = ($LASTEXITCODE -eq 0)
      if (-not $desktopPipSucceeded) {
        Write-Host "  Native desktop dependency download failed; checking the existing environment." -ForegroundColor Yellow
      }
      & $desktopPython -c 'import requests, socks, PySide6.QtWidgets'
      if ($LASTEXITCODE -eq 0) {
        [System.IO.File]::WriteAllText($desktopMarker, "ready`n", $Utf8NoBom)
        $desktopEnvironmentReady = $true
      }
    }
  }
  if (-not $desktopEnvironmentReady) {
    if ($desktopEnvironmentCreated) {
      Write-Host "  Native desktop dependencies could not be imported; the classic UI remains available." -ForegroundColor Yellow
    } else {
      Write-Host "  Could not create the native desktop environment; the classic UI remains available." -ForegroundColor Yellow
    }
  }
}

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
$iconPath = Join-Path $Dir 'onionmind.ico'
[IO.File]::WriteAllBytes($iconPath, [Convert]::FromBase64String($OnionIco))

# What the shortcut and the `onionmind` command run. Tor Browser is never opened;
# the app owns any hidden Tor process it starts after explicit search permission.
$launcherPath = Join-Path $Dir 'onionmind-launch.ps1'
@'
param([switch]$UI)
$Host.UI.RawUI.WindowTitle = 'Onionmind'
Set-Location ~          # /save <file> lands in the home dir
$desktopPython = Join-Path $PSScriptRoot 'desktop-env\Scripts\python.exe'
$desktopPythonw = Join-Path $PSScriptRoot 'desktop-env\Scripts\pythonw.exe'
$desktopMarker = Join-Path $PSScriptRoot 'desktop-env\.onionmind-desktop-ready'
$desktopReady = Test-Path -LiteralPath $desktopMarker -PathType Leaf
$onionmindPython = Join-Path $PSScriptRoot 'onionmind.py'
if ($UI -or ($args.Count -eq 0)) {
  if ($desktopReady -and (Test-Path -LiteralPath $desktopPythonw -PathType Leaf)) {
    & $desktopPythonw $onionmindPython --ui
  } else {
    & pythonw $onionmindPython --ui
  }
} else {
  if ($desktopReady -and (Test-Path -LiteralPath $desktopPython -PathType Leaf)) {
    & $desktopPython $onionmindPython @args
  } else {
    & python $onionmindPython @args
  }
}
'@ | Set-Content -LiteralPath $launcherPath -Encoding UTF8

# `onionmind` is the way in: same engine, callable from any terminal. ollama
# stays underneath as the server - it stops being something you type.
# Repository-aware coding agent, kept separate from Onionmind's local Tor chat.
$dshRevision = [string](Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/Codemaster64/onionmind/commits/main').sha
if ($dshRevision -notmatch '^[0-9a-fA-F]{40}$') { throw 'Could not resolve a fixed DSH asset revision.' }
$dshBase = "https://raw.githubusercontent.com/Codemaster64/onionmind/$dshRevision"
$dshPluginPath = Join-Path $Dir 'dsh-onionmind-tor-search.js'
$dshPatchPath = Join-Path $Dir 'dsh-onionmind-tor.patch.yml'
Invoke-WebRequest -UseBasicParsing "$dshBase/dsh-onionmind-tor-search.js" -OutFile $dshPluginPath
$dshPatch = (Invoke-WebRequest -UseBasicParsing "$dshBase/dsh-onionmind-tor.patch.yml").Content
if (-not $dshPatch.Contains('@ONIONMIND_DSH_PLUGIN@')) { throw 'Downloaded DSH patch placeholder is missing.' }
$dshPlugin = ($dshPluginPath -replace '\\', '/').Replace("'", "''")
[IO.File]::WriteAllText(
  $dshPatchPath,
  $dshPatch.Replace('@ONIONMIND_DSH_PLUGIN@', $dshPlugin),
  $Utf8NoBom
)
@'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$Model = '@ONIONMIND_MODEL@'
if (-not $Arguments -or $Arguments.Count -eq 0) {
  Write-Host 'Usage: onionmind-code "task for the coding agent"' -ForegroundColor Yellow
  exit 2
}
$nodeCommand = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
  Write-Error 'DeepSeek Harness requires Node.js ^22.19 or 24+. Install a supported Node.js release first.'
  exit 1
}
try { $nodeVersion = [version]((& $nodeCommand.Source --version).TrimStart([char]'v')) }
catch { Write-Error 'Could not read the installed Node.js version.'; exit 1 }
if (-not ($nodeVersion.Major -ge 24 -or ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -ge 19))) {
  Write-Error "DeepSeek Harness requires Node.js ^22.19 or 24+; found $nodeVersion."
  exit 1
}
$task = $Arguments -join ' '
Write-Host 'Agent network note: Harness traffic is direct; only Onionmind chat search uses Tor.' -ForegroundColor DarkYellow
$consent = Read-Host 'Run DeepSeek Harness with direct-network capability? [y/N]'
if ($consent -notmatch '^(?i:y|yes)$') { Write-Host 'Canceled.'; exit 3 }
& ollama launch dsh --model $Model -- --profile headless $task
exit $LASTEXITCODE
'@.Replace('@ONIONMIND_MODEL@', $name) |
  Set-Content -LiteralPath (Join-Path $Dir 'onionmind-code-launch.ps1') -Encoding UTF8
@"
@echo off
title Onionmind Code
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-code-launch.ps1" %*
"@ | Set-Content -LiteralPath (Join-Path $Dir 'onionmind-code.cmd') -Encoding ASCII
@"
@echo off
title Onionmind
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content -LiteralPath (Join-Path $Dir 'onionmind.cmd') -Encoding ASCII
@"
@echo off
title Onionmind Chat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-launch.ps1" -UI %*
"@ | Set-Content -LiteralPath (Join-Path $Dir 'onionmind-chat.cmd') -Encoding ASCII

@"
@echo off
title Onionmind Update
set "ONIONMIND_DIR=%~dp0"
echo This contacts raw.githubusercontent.com directly to download the named updater.
set /p "ONIONMIND_UPDATE_OK=Continue? [y/N] "
if /I not "%ONIONMIND_UPDATE_OK%"=="y" if /I not "%ONIONMIND_UPDATE_OK%"=="yes" exit /b 3
powershell -NoProfile -ExecutionPolicy Bypass -Command "`$u=Join-Path `$env:TEMP 'onionmind-update.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.ps1' -OutFile `$u; & `$u -InstallDir `$env:ONIONMIND_DIR -AllowDirectNetwork -Yes; exit `$LASTEXITCODE"
"@ | Set-Content -LiteralPath (Join-Path $Dir 'onionmind-update.cmd') -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $Dir)) {
  [Environment]::SetEnvironmentVariable('Path', "$userPath;$Dir", 'User')  # registry, no setx truncation
  Say "onionmind command installed - open a NEW terminal and just type: onionmind"
}

$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Onionmind.lnk'))
$lnk.TargetPath     = 'powershell.exe'
$lnk.Arguments      = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`" -UI"
$lnk.WindowStyle    = 7
$lnk.WorkingDirectory = $Dir
$lnk.IconLocation   = "$iconPath,0"
$lnk.Description    = 'Native local AI coding workbench with Tor search'
$lnk.Save()

Write-Host ""
Say "Ready"
Write-Host "  Desktop:     onionmind   (native local workbench)"
Write-Host "  Chat alias:  onionmind-chat"
Write-Host "  Coding:      onionmind-code `"task`"   (headless Harness; direct network)"
Write-Host "  Updates:     onionmind-update   (code only, model untouched)"
if ($Vision) { Write-Host "  Images:      $name-vision   (vision model)" }
Write-Host "  Web search:  python `"$searchPath`" `"your question`""
Write-Host "               (Tor starts hidden only after one-turn search permission)"
Write-Host "  Shortcut:    Onionmind - double-click to open the workbench"
