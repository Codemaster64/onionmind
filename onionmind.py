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
