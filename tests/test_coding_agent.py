"""The Qwen Code harness wiring: what run_code sets up, and the MCP framing.

Nothing here touches Tor, Ollama or qwen. It checks the things that silently
break the agent instead of erroring: the MCP server landing in the wrong
settings scope (qwen then gates it behind a prompt nobody can answer), a child
process finding a way onto the clearnet, a context too small to work in, and
malformed JSON-RPC on stdout.

Run: python3 tests/test_coding_agent.py
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import onionmind

MUTE = mock.patch("builtins.print")              # keep banners out of the test log
BACKSLASH = chr(92)


def as_model(name):
    return mock.patch.object(onionmind, "MODEL", name)


def run_code_into(root, backend="ollama"):
    """Drive run_code() with every outside dependency stubbed, then read back the
    project settings, the user settings, and the environment qwen was given."""
    home = os.path.join(root, "home")
    work = os.path.join(root, "work")
    os.makedirs(home, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    seen = {}
    with mock.patch.object(onionmind, "tor_check"), \
         mock.patch.object(onionmind, "start_tor_hidden"), \
         mock.patch.object(onionmind, "detect_backend"), \
         mock.patch.object(onionmind, "start_tor_bridge", return_value=9999), \
         mock.patch.object(onionmind, "BACKEND", backend), \
         mock.patch.object(onionmind, "_code_model", lambda ctx: onionmind.MODEL), \
         mock.patch.object(onionmind, "code_ctx", return_value=onionmind.CODE_CTX), \
         mock.patch("shutil.which", return_value="qwen"), \
         mock.patch("subprocess.call",
                    side_effect=lambda *a, **kw: seen.update(kw.get("env") or {}) or 0), \
         mock.patch("os.path.expanduser", return_value=home), \
         mock.patch.object(onionmind, "NET_LOG",
                           os.path.join(home, ".onionmind", "agent-net.log")), \
         mock.patch("builtins.input", return_value="a"), MUTE:
        onionmind.run_code(work)

    def read(where):
        with open(os.path.join(where, ".qwen", "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)

    return read(work), read(home), seen


class SettingsTests(unittest.TestCase):
    def test_mcp_server_is_user_scoped_and_the_project_gets_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            project, user, _env = run_code_into(root)
        # Project scope would make qwen demand interactive approval for the
        # server, so the agent must NOT find it there.
        self.assertNotIn("mcpServers", project)
        self.assertIn("onionmind", user["mcpServers"])
        self.assertEqual(user["mcpServers"]["onionmind"]["args"][-1], "--mcp")
        # The child dials Tor's SOCKS port itself; the HTTP bridge would loop.
        self.assertEqual(user["mcpServers"]["onionmind"]["env"]["ALL_PROXY"], "")

        self.assertEqual(project["proxy"], "http://127.0.0.1:9999")
        self.assertEqual(project["model"]["name"], onionmind.MODEL)
        self.assertEqual(project["model"]["sessionTokenLimit"], onionmind.CODE_CTX)
        # qwen ships its own web tools; they do not go through Tor. The rest of
        # the deny list is the shell side, checked in ContainmentTests.
        deny = project["permissions"]["deny"]
        self.assertIn("web_search", deny)
        self.assertIn("web_fetch", deny)

    def test_existing_settings_survive(self):
        with tempfile.TemporaryDirectory() as root:
            work = os.path.join(root, "work", ".qwen")
            home = os.path.join(root, "home", ".qwen")
            os.makedirs(work)
            os.makedirs(home)
            with open(os.path.join(work, "settings.json"), "w") as fh:
                json.dump({"ui": {"theme": "dark"}}, fh)
            with open(os.path.join(home, "settings.json"), "w") as fh:
                json.dump({"mcpServers": {"mine": {"command": "x"}}}, fh)
            project, user, _env = run_code_into(root)
        self.assertEqual(project["ui"], {"theme": "dark"})
        self.assertIn("mine", user["mcpServers"])
        self.assertIn("onionmind", user["mcpServers"])


class BackendTests(unittest.TestCase):
    def test_agent_follows_the_detected_backend(self):
        # Android has no Ollama; sending the agent to 11434 there is a dead port.
        for backend, port in (("ollama", "11434"), ("llama-server", "8080")):
            with tempfile.TemporaryDirectory() as root:
                _project, _user, env = run_code_into(root, backend)
            self.assertEqual(env["OPENAI_BASE_URL"],
                             f"http://127.0.0.1:{port}/v1", backend)
            self.assertEqual(env["OPENAI_MODEL"], onionmind.MODEL)
            self.assertTrue(env["HTTPS_PROXY"].endswith(":9999"), env["HTTPS_PROXY"])


class TorOnlyTests(unittest.TestCase):
    """The agent gets the web through Tor or not at all."""

    def test_every_proxy_variable_a_child_reads_is_set(self):
        env = onionmind._proxy_env("http://127.0.0.1:9999")
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual(env[name], "http://127.0.0.1:9999")
            if os.name != "nt":                  # curl reads http_proxy lowercase-only
                self.assertEqual(env[name.lower()], "http://127.0.0.1:9999")
        # An inherited NO_PROXY is a hole straight to the clearnet.
        self.assertEqual(env["NO_PROXY"], "")
        # Node's fetch ignores everything above without this; it leaked on v24.
        self.assertEqual(env["NODE_USE_ENV_PROXY"], "1")

    def test_the_mcp_child_is_not_sent_back_through_the_bridge(self):
        env = onionmind._proxy_env("")           # it dials Tor's SOCKS port itself
        self.assertEqual(env["ALL_PROXY"], "")
        self.assertEqual(env["NODE_USE_ENV_PROXY"], "0")

    def test_qwen_is_launched_pointing_at_the_bridge(self):
        with tempfile.TemporaryDirectory() as root:
            _project, _user, env = run_code_into(root)
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:9999")
        self.assertEqual(env["NO_PROXY"], "")
        self.assertEqual(env["NODE_USE_ENV_PROXY"], "1")

    def test_the_bridge_refuses_rather_than_connecting_direct(self):
        # No pinned Tor port: PySocks would otherwise fall back to 1080 and
        # connect to whatever happens to be listening there.
        with mock.patch.object(onionmind, "_port", None):
            with self.assertRaises(OSError):
                onionmind._dial("example.com", 80)

    def test_loopback_still_goes_direct(self):
        # The local model is not "the web" - routing it over Tor would be absurd.
        with mock.patch.object(onionmind.socket, "create_connection") as conn:
            onionmind._dial("127.0.0.1", 11434)
        conn.assert_called_once()


class ContextTests(unittest.TestCase):
    """The context IS the budget. The installed chat model is num_ctx 8192, most
    of which an agent spends on its system prompt before the task even starts."""

    def test_the_agent_gets_a_context_worth_coding_in(self):
        self.assertGreaterEqual(onionmind.CODE_CTX, 32768)

    def test_the_derived_model_asks_ollama_for_the_bigger_context(self):
        seen = []

        def capture(argv, **kwargs):
            with open(argv[-1], encoding="utf-8") as fh:      # the temp Modelfile
                seen.append(fh.read())
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(onionmind, "BACKEND", "ollama"), as_model("inferno"), \
             mock.patch("subprocess.run", side_effect=capture):
            self.assertEqual(onionmind._code_model(32768), "inferno-code")
        self.assertIn("FROM inferno", seen[0])
        self.assertIn("num_ctx 32768", seen[0])

    def test_it_falls_back_instead_of_dying_when_ollama_refuses(self):
        refused = subprocess.CompletedProcess([], 1, "", "Error: no such model")
        with mock.patch.object(onionmind, "BACKEND", "ollama"), as_model("inferno"), \
             MUTE, mock.patch("subprocess.run", return_value=refused):
            self.assertEqual(onionmind._code_model(32768), "inferno")

    def test_llama_server_keeps_the_context_its_server_started_with(self):
        with mock.patch.object(onionmind, "BACKEND", "llama-server"), \
             as_model("inferno"), mock.patch("subprocess.run") as run:
            self.assertEqual(onionmind._code_model(32768), "inferno")
        run.assert_not_called()                  # there is no ollama to ask


class NetLogTests(unittest.TestCase):
    """One line per thing that leaves the machine. The agent owns the console it
    runs in, so this file is the only place that record can go."""

    def test_what_leaves_the_machine_is_written_down(self):
        with tempfile.TemporaryDirectory() as root:
            log = os.path.join(root, "deep", "agent-net.log")
            with mock.patch.object(onionmind, "NET_LOG", log):
                onionmind._net_log("tor      example.onion:443")
            with open(log, encoding="utf-8") as fh:
                self.assertIn("example.onion:443", fh.read())

    def test_a_broken_log_never_takes_the_network_down(self):
        with mock.patch.object(onionmind, "NET_LOG", ""):
            onionmind._net_log("anything")       # must not raise


class ContainmentTests(unittest.TestCase):
    """Nothing reaches the network except through the proxy, which is Tor."""

    def test_commands_that_cannot_be_proxied_are_denied(self):
        with tempfile.TemporaryDirectory() as root:
            project, _user, _env = run_code_into(root)
        deny = project["permissions"]["deny"]
        for name in ("ping", "nslookup", "ssh", "scp", "nc", "telnet", "certutil"):
            self.assertIn(f"run_shell_command({name})", deny, name)
        # curl, wget, git and npm read the proxy variables, so they ARE on Tor
        # and must stay usable - denying them would cost capability for nothing.
        for name in ("curl", "wget", "git", "npm", "pip", "node", "python"):
            self.assertNotIn(f"run_shell_command({name})", deny, name)

    def test_children_are_pointed_at_the_socket_shims(self):
        with tempfile.TemporaryDirectory() as root:
            _project, _user, env = run_code_into(root)
        self.assertTrue(env["PYTHONPATH"].split(os.pathsep)[0].endswith("shims"))
        self.assertIn("--require", env["NODE_OPTIONS"])
        # NODE_OPTIONS is parsed with shell escaping, which eats backslashes.
        self.assertNotIn(BACKSLASH, env["NODE_OPTIONS"])

    def test_the_python_shim_refuses_a_direct_socket(self):
        with tempfile.TemporaryDirectory() as root:
            shim_dir = self.shims(root)
            out = self.child(shim_dir, [sys.executable, "-c",
                "import socket; socket.socket().connect(('192.0.2.1', 80))"])
        self.assertIn("direct network access is off", out)

    def test_the_python_shim_leaves_loopback_alone(self):
        with tempfile.TemporaryDirectory() as root:
            shim_dir = self.shims(root)
            out = self.child(shim_dir, [sys.executable, "-c",
                "import socket;"
                "s=socket.socket();"
                "s.settimeout(0.2);"
                "s.connect_ex(('127.0.0.1', 9));"     # refused or not, never guarded
                "print('reached loopback')"])
        self.assertIn("reached loopback", out)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_node_shim_refuses_a_direct_socket(self):
        with tempfile.TemporaryDirectory() as root:
            shim_dir = self.shims(root)
            out = self.child(shim_dir, ["node", "-e",
                "require('net').connect(80, '192.0.2.1')"])
        self.assertIn("direct network access is off", out)

    # -- helpers ------------------------------------------------------------
    def shims(self, root):
        log = os.path.join(root, ".onionmind", "agent-net.log")
        with mock.patch.object(onionmind, "NET_LOG", log):
            onionmind._write_shims()
        return os.path.join(root, ".onionmind", "shims")

    def child(self, shim_dir, argv):
        env = dict(os.environ)
        env["PYTHONPATH"] = shim_dir
        env["NODE_OPTIONS"] = '--require "%s"' % os.path.join(
            shim_dir, "no-direct-net.js").replace(os.sep, "/")
        done = subprocess.run(argv, env=env, capture_output=True, text=True,
                              timeout=60)
        return done.stdout + done.stderr


class ModelTests(unittest.TestCase):
    """MODEL defaults to the tier the installer WOULD have built. A box that got
    a different one asks ollama for a name it has never heard of."""

    def resolve(self, installed, backend="ollama"):
        with mock.patch.object(onionmind, "BACKEND", backend), as_model("inferno"),              mock.patch.object(onionmind, "installed_models", return_value=installed),              MUTE:
            return onionmind.resolve_model()

    def test_a_model_that_is_not_installed_falls_back_to_one_that_is(self):
        self.assertEqual(self.resolve(["qwen3:4b"]), "qwen3:4b")

    def test_the_installed_model_is_left_alone(self):
        self.assertEqual(self.resolve(["inferno:latest", "qwen3:4b"]), "inferno")

    def test_a_derived_model_is_not_preferred_over_what_it_came_from(self):
        # -code and -vision are built FROM another model; as a default they are
        # a worse answer than their parent.
        self.assertEqual(self.resolve(["qwen3:4b-code", "qwen3:4b"]), "qwen3:4b")

    def test_llama_server_keeps_whatever_it_loaded(self):
        # There is no /api/tags to ask, and the server serves one model anyway.
        self.assertEqual(self.resolve([], "llama-server"), "inferno")


class BudgetTests(unittest.TestCase):
    """The slider's backing store. One file, so the CLI, the Tk window and the
    workbench cannot disagree about what the budget currently is."""

    def temp_store(self, root):
        return mock.patch.object(onionmind, "CODE_CTX_FILE",
                                 os.path.join(root, ".onionmind", "code-ctx"))

    def test_a_saved_budget_comes_back(self):
        with tempfile.TemporaryDirectory() as root, self.temp_store(root),              mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ONIONMIND_CODE_CTX", None)
            onionmind.set_code_ctx(65536)
            self.assertEqual(onionmind.code_ctx(), 65536)

    def test_nothing_saved_means_the_default(self):
        with tempfile.TemporaryDirectory() as root, self.temp_store(root),              mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ONIONMIND_CODE_CTX", None)
            self.assertEqual(onionmind.code_ctx(), onionmind.CODE_CTX)

    def test_the_env_override_still_wins(self):
        # Someone who exported it is debugging; the slider must not fight them.
        with tempfile.TemporaryDirectory() as root, self.temp_store(root),              mock.patch.dict(os.environ, {"ONIONMIND_CODE_CTX": "16384"}):
            onionmind.set_code_ctx(131072)
            self.assertEqual(onionmind.code_ctx(), 16384)

    def test_a_nonsense_value_cannot_build_a_model_that_will_not_load(self):
        with tempfile.TemporaryDirectory() as root, self.temp_store(root),              mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ONIONMIND_CODE_CTX", None)
            self.assertEqual(onionmind.set_code_ctx(999999999), onionmind.CODE_STEPS[-1])
            self.assertEqual(onionmind.set_code_ctx(1), onionmind.CODE_STEPS[0])
            with open(onionmind.CODE_CTX_FILE, "w", encoding="utf-8") as fh:
                fh.write("banana")              # hand-edited to junk
            self.assertEqual(onionmind.code_ctx(), onionmind.CODE_CTX)

    def test_the_slider_stops_are_the_ones_the_core_clamps_to(self):
        self.assertEqual(onionmind.CODE_STEPS[0], 8192)
        self.assertIn(onionmind.CODE_CTX, onionmind.CODE_STEPS)


class McpTests(unittest.TestCase):
    def drive(self, requests_):
        out = io.StringIO()
        lines = "".join(json.dumps(r) + "\n" for r in requests_)
        with mock.patch.object(onionmind, "tor_check"), \
         mock.patch.object(onionmind, "start_tor_hidden"), \
             mock.patch.object(onionmind.sys, "stdin", io.StringIO(lines)), \
             mock.patch.object(onionmind.sys, "stdout", out):
            onionmind.run_mcp()
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_handshake_and_tool_list(self):
        replies = self.drive([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no id: no reply
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]],
                         ["web_search", "web_fetch"])

    def test_a_failing_search_answers_instead_of_dying(self):
        with mock.patch.object(onionmind, "web_search",
                               side_effect=OSError("no circuit")):
            replies = self.drive([{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                   "params": {"name": "web_search",
                                              "arguments": {"query": "x"}}}])
        self.assertEqual(replies[0]["id"], 7)
        self.assertIn("failed over Tor", replies[0]["result"]["content"][0]["text"])


class HarnessAgentTests(unittest.TestCase):
    """The shipped agent (DeepSeek Harness) leaves over Tor or does not leave.

    It used to be launched straight from the user's environment by three
    different callers: no proxy, no containment, only its search plugin routed.
    """

    def env(self, root):
        log = os.path.join(root, ".onionmind", "agent-net.log")
        with mock.patch.object(onionmind, "tor_check"),              mock.patch.object(onionmind, "start_tor_hidden"),              mock.patch.object(onionmind, "start_tor_bridge", return_value=9999),              mock.patch.object(onionmind, "NET_LOG", log):
            return onionmind.agent_env({"PATH": os.environ.get("PATH", "")})

    def test_no_tor_means_no_agent(self):
        with mock.patch.object(onionmind, "start_tor_hidden"),              mock.patch.object(onionmind, "tor_check",
                               side_effect=SystemExit("No Tor proxy")),              mock.patch("subprocess.call") as call:
            with self.assertRaises(SystemExit):
                onionmind.run_agent("do a thing")
        call.assert_not_called()                 # fails closed: never started

    def test_hidden_tor_is_attempted_before_failing_closed(self):
        # The agent brings up the tor.exe this file owns - hidden, never
        # firefox.exe - and only then verifies the circuit.
        order = []
        with mock.patch.object(onionmind, "start_tor_hidden",
                               side_effect=lambda: order.append("hidden")),              mock.patch.object(onionmind, "tor_check",
                               side_effect=lambda: order.append("check")),              mock.patch.object(onionmind, "start_tor_bridge", return_value=9999),              mock.patch.object(onionmind, "NET_LOG", os.devnull),              mock.patch("subprocess.call", return_value=0):
            onionmind.run_agent("do a thing")
        self.assertEqual(order, ["hidden", "check"])

    def test_every_child_is_pointed_at_the_tor_bridge(self):
        with tempfile.TemporaryDirectory() as root:
            env = self.env(root)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual(env[name], "http://127.0.0.1:9999", name)
        # An inherited NO_PROXY is a hole straight to the clearnet.
        self.assertEqual(env["NO_PROXY"], "")
        self.assertEqual(env["NODE_USE_ENV_PROXY"], "1")

    def test_the_agent_cannot_open_its_own_socket(self):
        with tempfile.TemporaryDirectory() as root:
            env = self.env(root)
        self.assertTrue(env["PYTHONPATH"].split(os.pathsep)[0].endswith("shims"))
        self.assertIn("--require", env["NODE_OPTIONS"])

    def test_the_search_plugin_is_wired_up(self):
        with tempfile.TemporaryDirectory() as root:
            env = self.env(root)
        # Without these the plugin reports itself unavailable and the harness
        # quietly falls back to its own provider over a direct connection.
        self.assertTrue(env["ONIONMIND_PY"].endswith("onionmind.py"))
        self.assertTrue(env["ONIONMIND_PYTHON"])

    def test_the_command_runs_the_asked_for_model_and_task(self):
        # The shipped DSH takes neither a task nor --profile any more (it exits
        # 1 with "unknown option --profile"), so Agent mode runs Qwen Code
        # non-interactively instead - the same agent run_code drives.
        argv = onionmind.agent_argv("some-model", "fix the parser",
                                    executable=["qwen"])
        self.assertEqual(argv, ["qwen", "--model", "some-model",
                                "-p", "fix the parser"])
        # No task means an interactive session, so no -p at all.
        self.assertEqual(onionmind.agent_argv("some-model", None,
                                              executable=["qwen"]),
                         ["qwen", "--model", "some-model"])

    def test_the_search_patch_escape_hatch_is_gone(self):
        # ollama's launcher rejects the patch profile today, and an environment
        # variable that smuggles it past agent_argv would be a silent boundary
        # change - so the launcher offers no way in for it at all, and the
        # harness's own provider rides the proxy shims like everything else.
        for value in (None, "1"):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ONIONMIND_DSH_PATCH", None)
                if value is not None:
                    os.environ["ONIONMIND_DSH_PATCH"] = value
                self.assertNotIn("--patch", onionmind.agent_argv("m", "t"))

    def test_no_task_means_an_interactive_session(self):
        argv = onionmind.agent_argv("some-model")
        self.assertNotIn("headless", argv)


if __name__ == "__main__":
    unittest.main()
