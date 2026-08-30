import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import EchoHandler, RunningServer

from ollama_tor.containment import routed_environment, shim_directory


def environment() -> dict[str, str]:
    base = dict(os.environ)
    base.update(
        {
            "HTTP_PROXY": "http://leak.invalid",
            "NO_PROXY": "*",
            "PYTHONPATH": "existing-python-path",
            "NODE_OPTIONS": "--trace-warnings",
        }
    )
    return routed_environment(
        "http://127.0.0.1:19080",
        19050,
        log_path=Path("network.log"),
        base=base,
    )


def test_environment_overrides_proxy_bypasses_and_preserves_existing_options():
    routed = environment()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        assert routed[name] == "http://127.0.0.1:19080"
    assert routed["NO_PROXY"] == ""
    assert routed["NODE_USE_ENV_PROXY"] == "1"
    assert routed["OLLAMA_TOR_ACTIVE"] == "1"
    assert routed["OLLAMA_TOR_SOCKS_PORT"] == "19050"
    assert str(shim_directory()) in routed["PYTHONPATH"]
    assert "existing-python-path" in routed["PYTHONPATH"]
    assert "no_direct_net.cjs" in routed["NODE_OPTIONS"]
    assert "--trace-warnings" in routed["NODE_OPTIONS"]


def test_python_child_rejects_direct_tcp_and_dns_before_network_io():
    script = """
import socket
errors = []
for operation in (
    lambda: socket.create_connection(('198.51.100.8', 443), 0.1),
    lambda: socket.getaddrinfo('example.com', 443),
):
    try:
        operation()
    except OSError as exc:
        errors.append(str(exc))
print('|'.join(errors))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("direct network access is off") == 2


def test_python_child_rejects_direct_udp():
    script = """
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.sendto(b'x', ('198.51.100.8', 53))
except OSError as exc:
    print(exc)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "direct network access is off" in completed.stdout


def test_python_child_can_reach_loopback_for_the_ollama_api():
    with RunningServer(EchoHandler) as server:
        script = f"""
import socket
with socket.create_connection(('127.0.0.1', {server.port}), 2) as stream:
    stream.sendall(b'ollama')
    print(stream.recv(32).decode())
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=environment(),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ollama"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_node_child_rejects_direct_tcp_dns_and_udp():
    script = r"""
const net = require('node:net');
const dns = require('node:dns');
const dgram = require('node:dgram');
let blocked = 0;
for (const operation of [
  () => net.connect(443, '198.51.100.8'),
  () => dns.lookup('example.com', (error) => {
    if (error && error.message.includes('direct network access is off')) blocked += 1;
  }),
  () => dgram.createSocket('udp4').send('x', 53, '198.51.100.8'),
]) {
  try { operation(); } catch (error) {
    if (error.message.includes('direct network access is off')) blocked += 1;
  }
}
setTimeout(() => { console.log(blocked); process.exit(blocked === 3 ? 0 : 1); }, 25);
"""
    completed = subprocess.run(
        ["node", "-e", script],
        env=environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "3"
