"""Privacy and background-Tor regression contracts."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import onionmind


class SearchConsentTests(unittest.TestCase):
    def test_ollama_request_omits_tools_until_search_is_allowed(self) -> None:
        response = mock.Mock(status_code=200, ok=True)
        response.json.return_value = {
            "message": {"role": "assistant", "content": "local response"}
        }
        messages = [{"role": "user", "content": "hello"}]
        with mock.patch.object(onionmind.requests, "post", return_value=response) as post:
            onionmind._ask_ollama(messages)
            self.assertNotIn("tools", post.call_args.kwargs["json"])

            onionmind._ask_ollama(messages, allow_search=True)
            self.assertEqual(post.call_args.kwargs["json"]["tools"], onionmind.TOOLS)

    def test_unpermitted_model_tool_call_cannot_reach_tor_or_search(self) -> None:
        replies = iter(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "web_search",
                                "arguments": {"query": "private query"},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "Stayed local."},
            ]
        )
        history = [{"role": "user", "content": "stay local"}]
        with mock.patch.object(onionmind, "BACKEND", "ollama"), \
             mock.patch.object(onionmind, "_ask_ollama", side_effect=lambda *_a, **_kw: next(replies)), \
             mock.patch.object(onionmind, "tor_check") as check, \
             mock.patch.object(onionmind, "web_search") as search:
            answer = onionmind.turn(history)

        self.assertEqual(answer, "Stayed local.")
        check.assert_not_called()
        search.assert_not_called()
        self.assertEqual(history[-2]["content"], "(web search was not allowed for this turn)")


class OnionSearchTests(unittest.TestCase):
    def test_retries_only_the_onion_service_with_fresh_circuits(self) -> None:
        attempts = []

        def fail(url, **kwargs):
            attempts.append((url, kwargs["proxies"]))
            raise RuntimeError("offline")

        with mock.patch.object(onionmind.secrets, "token_hex", side_effect=("first", "second")), \
             mock.patch.object(onionmind.requests, "post", side_effect=fail):
            result = onionmind.web_search("private query")

        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(url == onionmind.ENDPOINT for url, _ in attempts))
        self.assertNotEqual(attempts[0][1], attempts[1][1])
        self.assertIn("onion service", result)


class BackgroundTorTests(unittest.TestCase):
    def tearDown(self) -> None:
        onionmind._managed_tor_process = None
        onionmind._port = None

    def test_hidden_start_runs_tor_exe_without_a_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Tor Browser"
            browser = root / "Browser"
            data = browser / "TorBrowser" / "Data" / "Tor"
            executable = browser / "TorBrowser" / "Tor" / "tor.exe"
            data.mkdir(parents=True)
            executable.parent.mkdir(parents=True, exist_ok=True)
            for path in (executable, data / "torrc-defaults", data / "torrc"):
                path.touch()

            process = mock.Mock(pid=1234)
            process.poll.return_value = None
            with mock.patch.object(onionmind.os, "name", "nt"), \
                 mock.patch.object(onionmind, "_tor_browser_roots", return_value=[str(root)]), \
                 mock.patch.object(onionmind, "tor_proxy_port", side_effect=[None, 9150]), \
                 mock.patch.object(onionmind.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(onionmind.start_tor_hidden(timeout=1), 9150)

            argv = popen.call_args.args[0]
            self.assertEqual(Path(argv[0]), executable)
            self.assertNotIn("firefox.exe", " ".join(argv).lower())
            self.assertIn("9150 IsolateSOCKSAuth", argv)
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_existing_proxy_is_reused_not_adopted(self) -> None:
        with mock.patch.object(onionmind, "tor_proxy_port", return_value=9050), \
             mock.patch.object(onionmind.subprocess, "Popen") as popen:
            self.assertEqual(onionmind.start_tor_hidden(), 9050)
        popen.assert_not_called()
        self.assertIsNone(onionmind._managed_tor_process)

    def test_failed_reverification_clears_a_previously_verified_port(self) -> None:
        response = mock.Mock()
        response.json.return_value = {"IsTor": False}
        onionmind._port = 9150
        with mock.patch.object(onionmind.requests, "get", return_value=response), \
             self.assertRaises(SystemExit):
            onionmind.tor_check()
        self.assertIsNone(onionmind._port)


class DistributionPrivacyTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_no_cloud_workflow_files_remain(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        self.assertFalse(list(workflow_dir.glob("*.yml")))
        self.assertFalse(list(workflow_dir.glob("*.yaml")))

    def test_search_runtime_is_onion_only(self) -> None:
        onion_endpoint = (
            "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad"
            ".onion/html/"
        )
        runtime_holders = (
            "onionmind.py",
            "android/core/src/main/kotlin/org/onionmind/core/Agent.kt",
            "install-onionmind.ps1",
            "install-onionmind.sh",
            "install-onionmind-android.sh",
            "onionmind-setup.cmd",
        )
        forbidden = (
            "https://html." + "duckduckgo.com/html/",
            "clearnet",
            "direct fallback",
            "both endpoints",
        )

        for name in runtime_holders:
            with self.subTest(name=name):
                runtime = self.text(name)
                self.assertIn(onion_endpoint, runtime)
                lowered = runtime.lower()
                for phrase in forbidden:
                    self.assertNotIn(phrase, lowered)

        android = self.text(
            "android/core/src/main/kotlin/org/onionmind/core/Agent.kt"
        )
        search = android[android.index("    fun webSearch(") :]
        search = search[: search.index("    fun parseResults(")]
        self.assertIn("repeat(2)", search)
        self.assertIn("Socks5Socket.randomCreds()", search)
        self.assertIn("Request.Builder().url(ENDPOINT)", search)

    def test_windows_launchers_never_start_tor_browser(self) -> None:
        for name in (
            "install-onionmind.ps1",
            "update-onionmind.ps1",
            "onionmind-setup.cmd",
            "onionmind-bootstrap.ps1",
        ):
            with self.subTest(name=name):
                text = self.text(name)
                self.assertNotIn("Starting Tor Browser", text)
                self.assertNotIn("Start-Process $c", text)

    def test_desktop_startup_uses_only_the_local_tor_probe(self) -> None:
        desktop = self.text("onionmind_desktop.py")
        start = desktop.index("    def _probe_services(self) -> None:")
        end = desktop.index("    def _start_worker", start)
        probe = desktop[start:end]
        self.assertIn('getattr(self.core, "tor_proxy_port", None)', probe)
        self.assertNotIn("tor_check", probe)
        self.assertIn('QCheckBox("Allow Tor search this turn")', desktop)
        self.assertIn("starter(stop_event=stop_event)", desktop)
        self.assertNotIn("turn_stream(history, on_text, stop_event)", desktop)

    def test_legacy_agent_cannot_bypass_chat_search_consent(self) -> None:
        core = self.text("onionmind.py")
        start = core.index("    def launch_coding_agent():")
        end = core.index("    send.configure", start)
        launcher = core[start:end]
        self.assertNotIn("--patch", launcher)
        self.assertIn("--profile", launcher)

    def test_bootstrap_requires_separate_network_consent(self) -> None:
        bootstrap = self.text("onionmind-bootstrap.ps1")
        self.assertIn("[switch]$Apply", bootstrap)
        self.assertIn("[switch]$AllowDirectNetwork", bootstrap)
        self.assertIn("Refusing direct network access", bootstrap)
        self.assertNotIn("ollama pull", bootstrap.lower())

    def test_desktop_builder_audits_before_consent_gated_dependency_repair(self) -> None:
        builder = self.text("tools/build-desktop.ps1")
        self.assertIn("$DependencyAudit", builder)
        self.assertIn("[switch]$AllowDirectNetwork", builder)
        self.assertNotIn('"--upgrade", "pip"', builder)
        self.assertNotIn('"--assume-yes-for-downloads"', builder)
        self.assertIn('"--windows-console-mode=hide"', builder)

    def test_windows_updater_audits_exact_runtime_ranges_before_repair(self) -> None:
        updater = self.text("update-onionmind.ps1")
        audit_at = updater.index("$DesktopDependencyAudit = @'")
        plan_at = updater.index("Direct-network update plan")
        repair_at = updater.index(
            "if (-not $desktopDependenciesReady -and $nativePythonSupported) {"
        )
        ready_at = updater.index(
            "} elseif ($desktopDependenciesReady) {", repair_at
        )
        repair = updater[repair_at:ready_at]
        outside_repair = updater[:repair_at] + updater[ready_at:]

        self.assertLess(audit_at, plan_at)
        self.assertIn("(6, 11) <= value < (6, 12)", updater)
        self.assertIn("(2, 32) <= value < (3,)", updater)
        self.assertIn("(1, 7) <= value < (2,)", updater)
        self.assertIn("$desktopDependenciesReady", updater)
        self.assertIn("& $py -m venv $desktopEnv", repair)
        self.assertIn("-m pip install", repair)
        self.assertNotIn("& $py -m venv $desktopEnv", outside_repair)
        self.assertNotIn("-m pip install", outside_repair)
        self.assertIn("Desktop dependencies already satisfy", updater)


class AndroidPrivacyTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_android_startup_does_not_start_tor(self) -> None:
        app = self.text("android/app/src/main/java/org/onionmind/app/App.kt")
        activity = self.text(
            "android/app/src/main/java/org/onionmind/app/MainActivity.kt"
        )
        manager = self.text(
            "android/app/src/main/java/org/onionmind/app/ProcessManager.kt"
        )
        self.assertNotIn("ensureTor", app)
        self.assertNotIn("downloadModel", app)
        self.assertNotIn("DownloadService", app)
        self.assertNotIn("requestPermissions", activity)
        self.assertNotIn("torEnabled", manager)

    def test_android_background_download_is_user_driven_and_crash_safe(self) -> None:
        manifest = self.text("android/app/src/main/AndroidManifest.xml")
        service = self.text(
            "android/app/src/main/java/org/onionmind/app/DownloadService.kt"
        )
        manager = self.text(
            "android/app/src/main/java/org/onionmind/app/ProcessManager.kt"
        )

        self.assertIn("android.permission.FOREGROUND_SERVICE_DATA_SYNC", manifest)
        self.assertIn('android:name=".DownloadService"', manifest)
        self.assertIn('android:exported="false"', manifest)
        self.assertIn('android:foregroundServiceType="dataSync"', manifest)
        self.assertIn("ProcessManager.runDownload(this, id)", service)
        self.assertIn("PowerManager.PARTIAL_WAKE_LOCK", service)
        self.assertIn("ctx.startForegroundService", manager)
        self.assertIn("downloadClaimed.compareAndSet(false, true)", manager)

        # Each HTTP range lands in its own exact-length file. The shared .part
        # is assembled only after every chunk is complete, so a process kill
        # can never promote a merely preallocated sparse file as a model.
        self.assertIn("parallelPartFiles(part)", manager)
        self.assertIn("range.file.length() != range.length", manager)
        self.assertIn("output.fd.sync()", manager)
        self.assertIn("part.length() != expected", manager)
        self.assertIn("cleanupParallelParts(part)", manager)
        self.assertNotIn('RandomAccessFile(part, "rw")', manager)
        self.assertNotIn("setLength(expected)", manager)

        # Installed models must be non-empty regular files, and known catalog
        # sizes plus the GGUF magic must match before llama-server selects them.
        self.assertIn("it.isFile && it.length() > 0", manager)
        self.assertIn("it.length() == model.bytes", manager)
        self.assertIn("hasGgufHeader(it)", manager)
        self.assertIn("header.contentEquals(byteArrayOf(0x47, 0x47, 0x55, 0x46))", manager)

    def test_android_discloses_and_confirms_direct_model_downloads(self) -> None:
        page = self.text("android/app/src/main/assets/index.html")

        self.assertIn("Model downloads use direct HTTPS, not Tor", page)
        self.assertIn("exposes this phone’s network address to the model host", page)
        self.assertIn("Inference and local files stay on this phone", page)
        self.assertNotIn("leave the app open", page)
        self.assertIn("resumable even when Onionmind is minimized", page)

        install = page[page.index("async function install(id)") :]
        install = install[: install.index("function confirmModelDownload")]
        self.assertLess(
            install.index("if (!confirmModelDownload()) return"),
            install.index("api('/api/install'"),
        )

        custom = page[page.index("async function addCustom()") :]
        self.assertLess(
            custom.index("if (!confirmModelDownload()) return"),
            custom.index("api('/api/install'"),
        )

    def test_android_search_permission_is_consumed_per_chat_request(self) -> None:
        page = self.text("android/app/src/main/assets/index.html")
        server = self.text("android/app/src/main/java/org/onionmind/app/Server.kt")
        agent = self.text("android/core/src/main/kotlin/org/onionmind/core/Agent.kt")

        self.assertIn('id="allow-search" type="checkbox"', page)
        reset = page.index("$('allow-search').checked = false")
        submit = page.index("api('/api/chat'", reset)
        self.assertLess(reset, submit)
        self.assertIn("allowSearch:String(allowSearch)", page)
        self.assertNotIn("/api/tor", page)

        self.assertIn('formValue(body, "allowSearch")', server)
        self.assertIn("Agent.turn(LLAMA, messages, allowSearch)", server)
        self.assertIn("ProcessManager.ensureTor(ctx)", server)
        self.assertNotIn('"/api/tor"', server)

        self.assertIn("if (allowSearch) put(\"tools\"", agent)
        self.assertIn("name == \"web_search\" && allowSearch", agent)
        self.assertIn("web search was not allowed for this turn", agent)

    def test_android_context_covers_the_reasoning_budget(self) -> None:
        manager = self.text(
            "android/app/src/main/java/org/onionmind/app/ProcessManager.kt"
        )
        agent = self.text("android/core/src/main/kotlin/org/onionmind/core/Agent.kt")
        self.assertIn('"-c", "16384"', manager)
        self.assertIn("const val NUM_PREDICT = 16384", agent)

    def test_android_loopback_page_requires_an_unshared_capability_url(self) -> None:
        server = self.text("android/app/src/main/java/org/onionmind/app/Server.kt")
        activity = self.text(
            "android/app/src/main/java/org/onionmind/app/MainActivity.kt"
        )

        # Loopback is shared by every app on Android.  Neither the public root
        # nor a guessed route may return the token-bearing HTML page.
        self.assertIn('private val pageCapability', server)
        self.assertIn('private val pagePath = "/app/$pageCapability"', server)
        self.assertIn('session.uri == pagePath -> page()', server)
        self.assertIn('session.uri == "/" -> notFound()', server)
        self.assertIn('else -> notFound()', server)
        self.assertNotIn('"/" -> page()', server)

        # Only the activity gets the in-process capability URL. API calls keep
        # their separate header token, and every response is non-cacheable.
        self.assertIn("fun pageUrl(): String", server)
        self.assertIn('web.loadUrl(Server.pageUrl())', activity)
        self.assertNotIn('web.loadUrl("http://127.0.0.1:8081/")', activity)
        self.assertIn('session.headers["x-onionmind-token"] != apiToken', server)
        self.assertIn('replace("__ONIONMIND_TOKEN__", apiToken)', server)
        self.assertIn('addHeader("Cache-Control", "no-store")', server)

        # Public failures are deliberately bodyless and cannot reflect either
        # capability through an exception string.
        self.assertIn('private fun notFound(): Response', server)
        self.assertIn('Response.Status.NOT_FOUND, "text/plain", ""', server)
        self.assertIn('replace(apiToken, "[redacted]")', server)
        self.assertIn('replace(pageCapability, "[redacted]")', server)


if __name__ == "__main__":
    unittest.main()
