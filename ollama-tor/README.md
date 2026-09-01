# Ollama Tor

`ollama-tor` is a standalone companion for Ollama Launch integrations. It
verifies a local Tor circuit, starts an ephemeral HTTP proxy that can dial
remote hosts only through Tor, and launches the selected integration with that
proxy inherited by its process tree.

It is extracted from Onionmind's agent-routing design but does not import or
install Onionmind. The folder is a complete Python package and can be moved to
its own repository unchanged.

## Install

Requirements:

- Python 3.10 or newer
- [Ollama](https://ollama.com/) for `launch` mode
- Tor Browser connected on port 9150, or a Tor daemon on port 9050

From this directory:

```text
python -m pip install .
```

For an isolated command-line install, use `pipx install .` instead.

There are no runtime Python dependencies. The SOCKS5 client, Tor check, HTTP
bridge, and containment shims use the standard library.

## Use

Check Tor before launching anything:

```text
ollama-tor check
```

Launch any integration supported by your installed Ollama version:

```text
ollama-tor launch codex --model qwen3.8:27b
ollama-tor launch opencode --model qwen3-coder
ollama-tor launch claude --model qwen3.8:27b
```

Or wrap another Ollama-backed agent directly:

```text
ollama-tor exec -- your-agent --model local-model
```

Use a nonstandard Tor SOCKS port by putting the option before the command:

```text
ollama-tor --tor-port 19050 launch codex
```

`OLLAMA_TOR_PORT=19050` is the environment-variable equivalent. A
comma-separated value tries multiple ports in order.

## What happens

Before the child process exists, `ollama-tor`:

1. connects to each configured loopback SOCKS5 port;
2. resolves and requests `check.torproject.org` through that SOCKS connection;
3. refuses to launch unless the response says the connection is Tor;
4. starts a random loopback HTTP-proxy port;
5. launches the child with HTTP, HTTPS, and all-proxy variables pointing there;
6. clears `NO_PROXY`, enables Node's proxy environment support, and prepends
   Python and Node socket guards; and
7. closes the proxy when the launched process exits.

Remote DNS names are sent to Tor in the SOCKS5 request; they are not resolved
with the machine's normal DNS. Every remote TCP connection uses fresh random
SOCKS credentials, which asks Tor to isolate it onto a separate circuit.

Loopback remains reachable so the integration can talk to the local Ollama API
on `127.0.0.1:11434`. Remote destinations are logged by hostname and port—not
URL path or request body—to the platform state directory:

- Windows: `%LOCALAPPDATA%\ollama-tor\network.log`
- Linux: `$XDG_STATE_HOME/ollama-tor/network.log`, or
  `~/.local/state/ollama-tor/network.log`
- macOS: `~/Library/Logs/ollama-tor/network.log`

Override it with `--log-file PATH`, or disable it with `--no-log`.

## Security boundary

This is application-level routing, not a system firewall.

It covers clients that honor standard proxy variables. It also injects guards
that reject direct TCP, UDP, and DNS access from ordinary Python and Node child
processes. If a supported client ignores proxy variables, it fails instead of
silently connecting directly.

It cannot contain every executable an agent may start. A native binary that
ignores proxy variables, a runtime other than Python or Node, a process that
clears the injected environment, an absolute-path call to `ping` or `nslookup`,
or code using native FFI can still connect directly. A local forwarding service
is also inside the trusted loopback boundary. Use an OS firewall, container,
network namespace, or Whonix-style gateway when bypass resistance must cover
arbitrary code.

The wrapper governs the launched integration, not the already-running Ollama
daemon. Local inference stays local. Model pulls, pushes, sign-in, and cloud
model requests originate from the Ollama daemon and are **not** made Tor-only by
this wrapper. Configure the daemon or enforce routing at the operating-system
layer for those operations.

Tor hides the source IP from remote destinations; it does not hide Tor use from
the local network, conceal identifying query content, or defeat a global
traffic-correlation adversary.

## Why a companion instead of an Ollama plugin manifest?

Ollama currently exposes a fixed set of `ollama launch` integrations rather
than a third-party plugin registry. `ollama-tor` wraps that supported command,
so it works with the integration names offered by the user's installed Ollama
without patching Ollama or writing into its configuration.

## Develop and verify

```text
python -m pip install -e ".[test]"
python -m pytest
python -m build
```

The automated suite uses fake local SOCKS and HTTP peers. It does not require a
live Tor circuit or make internet requests. `ollama-tor check` is the explicit
live verification.
