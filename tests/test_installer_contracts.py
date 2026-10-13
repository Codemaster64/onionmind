"""Regression contracts for the generated Onionmind installers and updaters."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
    def test_linux_installer_roles_plan_first_and_keep_ollama_on_loopback(self) -> None:
        """--check changes nothing; --server stops before the app; Ollama stays private.

        The plan is printed from read-only probes, so `--check` must exit
        before the first sudo. A compute server gets Ollama, a model and SSH -
        never the app payloads. And no role may bind Ollama beyond loopback:
        clients reach it through SSH, and Ollama itself has no password.
        """
        sh = self.text("install-onionmind.sh")
        for flag in ("--client)", "--server)", "--check|--dry-run)", "--host=*)"):
            self.assertIn(flag, sh)
        check_exit = sh.index("Check only - nothing was changed")
        first_sudo = re.search(r"^\s*sudo ", sh, re.MULTILINE).start()
        self.assertLess(check_exit, first_sudo)
        self.assertLess(sh.index('say "Server ready"'), sh.index("<<'PYEOF'"))
        self.assertNotIn("OLLAMA_HOST=0.0.0.0", sh)
        self.assertNotIn("OLLAMA_HOST=", sh.split("<<'PYEOF'")[0])
        # Every step that installs or starts something checks first.
        self.assertGreaterEqual(sh.count("skip \""), 12)

    def test_linux_installer_uses_distro_correct_package_names(self) -> None:
        """tkinter is packaged under a different name in every distro.

        Arch shipped `python-tk`, which has never existed there - `python`
        lists `tk: for tkinter` as its optional dependency - and pacman aborts
        the WHOLE transaction on an unknown target, so a single wrong name
        meant tor, ollama and the python deps were never installed either.
        """
        sh = self.text("install-onionmind.sh")
        arch = sh[sh.index('[ "$DISTRO" = arch ]'):]
        arch = arch[: arch.index("\nelse")]
        # The package list itself, not the block - the comment above it names
        # python-tk on purpose, to say why it is not used.
        pkgs = arch.split("PKGS=(")[1].split(")")[0].split()
        self.assertIn("tk", pkgs)
        self.assertNotIn("python-tk", pkgs)

        # Debian is the one that really does prefix it.
        debian = sh[sh.index("DEB_APP_PKGS=("):]
        self.assertIn("python3-tk", debian[: debian.index(")")].split())

        # Homebrew has the same trap as Arch: there is no plain python-tk
        # formula, only versioned ones, so a bare name would fail the same way.
        packages = sh[sh.index('say "Step: system packages"'):]
        mac = packages[packages.index('"$DISTRO" = mac'):]
        mac = mac[: mac.index('elif [ "$DISTRO" = arch')]
        self.assertIn("python-tk@", mac)
        for bare in ('python-tk ', 'python-tk"', 'python-tk\n'):
            self.assertNotIn(bare, mac)

    def test_download_links_point_at_release_assets_not_raw_files(self) -> None:
        """A raw github link renders the script; a release asset downloads it.

        github.com/<repo>/raw/... serves text/plain with no Content-Disposition,
        so clicking a download badge opened the installer as a page of code
        instead of saving it. Release assets are served as
        `Content-Disposition: attachment`, so every downloadable script has to
        be attached to the rolling release AND linked from there.
        """
        downloadable = (
            "onionmind-install.cmd",
            "onionmind-setup.cmd",
            "install-onionmind.sh",
            "install-onionmind-android.sh",
            "matchstick.cmd",
            "matchstick.sh",
        )
        readme = self.text("README.md")
        raw = "/raw/refs/heads/main/"
        for asset in downloadable:
            self.assertNotIn(
                raw + asset, readme,
                f"{asset} is linked as a raw file, so it renders instead of downloading",
            )

        # The link is only good if the workflow actually publishes the asset.
        workflow = self.text(".github/workflows/desktop-build.yml")
        release = workflow[workflow.index("gh release create desktop-latest"):]
        for asset in downloadable:
            self.assertIn(asset, release, f"{asset} is linked but never attached to the release")
        # Attaching needs the tree, which the release job did not check out.
        self.assertIn("actions/checkout", workflow[:workflow.index("gh release create")])

    def test_windows_ships_one_installer_that_gates_on_architecture(self) -> None:
        """One Windows file, and it turns 32-bit away with a reason.

        The .ps1 used to be a second Windows installer that build.py wrapped
        into the .cmd; now the .cmd IS the installer. And x86 is not a build
        gap a download could close - Ollama has no 32-bit build and PySide6
        publishes no 32-bit Windows wheel - so the installer says so instead of
        failing deep inside winget or at the first Qt import.
        """
        self.assertFalse(
            (ROOT / "install-onionmind.ps1").exists(),
            "install-onionmind.ps1 is retired; onionmind-setup.cmd is the Windows installer",
        )
        setup = self.text("onionmind-setup.cmd")

        # Still a working polyglot: batch header, then the PowerShell payload.
        header = setup.split("#__ONION" + "MIND_PS__")[0]
        self.assertIn("@echo off", header)
        self.assertIn("exit /b %ERRORLEVEL%", header)

        # WOW64 trap: 32-bit PowerShell on 64-bit Windows reports x86 in
        # PROCESSOR_ARCHITECTURE, so the real architecture must be read from
        # PROCESSOR_ARCHITEW6432 first or x64 machines get turned away.
        arch = setup.index("PROCESSOR_ARCHITEW6432")
        self.assertLess(arch, setup.index("PROCESSOR_ARCHITECTURE", arch))
        for accepted in ("'AMD64'", "'ARM64'"):
            self.assertIn(accepted, setup)
        self.assertIn("there is no 32-bit version to install", setup)

    def test_universal_installer_is_one_file_for_every_platform(self) -> None:
        """onionmind-install.cmd is a batch file, a POSIX sh script and a
        PowerShell script at once, and only a dispatcher.

        cmd.exe sees the first line as a label and stops at `exit /b`; sh feeds
        those lines to `:` as a heredoc; the PowerShell half sits after the sh
        half's final exit. A single CR breaks sh, so the file must stay LF-only
        (and .gitattributes must not turn it into CRLF like every other .cmd).
        """
        raw = (ROOT / "onionmind-install.cmd").read_bytes()
        self.assertNotIn(b"\r", raw)
        raw.decode("ascii")                     # no codepage surprises in cmd.exe
        text = raw.decode("ascii")
        self.assertTrue(text.startswith(":<<'BATCH'\n"))
        batch = text[: text.index("\nBATCH\n")]
        self.assertIn("exit /b %ERRORLEVEL%", batch)
        code = [l for l in batch.lower().splitlines() if not l.startswith("rem")]
        self.assertFalse([l for l in code if "goto" in l or "call :" in l],
                         "label lookup misbehaves in LF-only batch files")
        marker = "#__ONIONMIND_INSTALL_PS__"
        self.assertEqual(text.count(marker), 1, "the PowerShell entry point must be unique")
        self.assertIn("'#__ONIONMIND_'+'INSTALL_PS__'", batch)
        self.assertIn("\nexit 0\n", text[: text.index(marker)])
        self.assertIn("onionmind-install.cmd text eol=lf", self.text(".gitattributes"))

        result = subprocess.run(["sh", "-n", str(ROOT / "onionmind-install.cmd")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

        # It hands over rather than re-implementing installs, so every script it
        # fetches must be a published asset, and the ONIONMIND_MODEL values it
        # passes must be ones those installers accept.
        workflow = self.text(".github/workflows/desktop-build.yml")
        release = workflow[workflow.index("gh release create desktop-latest"):]
        self.assertIn("onionmind-install.cmd", release)
        for asset in ("install-onionmind.sh", "install-onionmind-android.sh", "matchstick.sh",
                      "onionmind-setup.cmd", "matchstick.cmd"):
            self.assertIn(asset, text)
            self.assertIn(asset, release)
        self.assertIn("must be auto, fast or 27b", self.text("install-onionmind.sh"))
        self.assertIn("must be auto, fast, mobile or 27b", self.text("onionmind-setup.cmd"))
        self.assertIn("must be auto, lfm, 4b or 9b", self.text("install-onionmind-android.sh"))
        self.assertIn('MODELS="fast 27b"', text)
        self.assertIn('MODELS="lfm 4b 9b"', text)
        # The remote-models option hands over to the flags install-onionmind.sh parses.
        self.assertIn('bash "$f" --client "--host=$HOST"', text)
        sh = self.text("install-onionmind.sh")
        self.assertIn("--client) ROLE=client", sh)
        self.assertIn("--host=*) SERVER_HOST=", sh)
        # Both halves check what they download against the release's SHA256SUMS,
        # so the release must publish it.
        self.assertIn("SHA256SUMS", release)
        self.assertIn('"$BASE/SHA256SUMS"', text)
        self.assertIn('"$Base/SHA256SUMS"', text)

    def text(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_generated_payloads_and_line_endings_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "build.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_desktop_module_is_shipped_and_updated(self) -> None:
        """The desktop UI is split across sibling modules; a part left out of any
        list installs or updates into an app that dies on import."""
        modules = sorted(path.name for path in ROOT.glob("onionmind_desktop*.py"))
        self.assertGreaterEqual(len(modules), 6)
        build = self.text("build.py")
        bundle = self.text("tools/build-desktop.ps1")
        pyproject = self.text("pyproject.toml")
        updaters = {name: self.text(name) for name in ("update-onionmind.sh", "update-onionmind.ps1")}
        for module in modules:
            with self.subTest(module=module):
                for installer in ("onionmind-setup.cmd", "install-onionmind.sh"):
                    self.assertRegex(build, rf'\("{re.escape(module)}",\s+"{re.escape(installer)}"')
                self.assertIn(f'"{module}"', bundle)
                self.assertIn(f'"{module[:-3]}"', pyproject)
                for name, updater in updaters.items():
                    # Listed for download and syntax-checked before replacement.
                    self.assertGreaterEqual(updater.count(module), 2, name)
        self.assertEqual(self.text("install-onionmind.sh").count('"$DIR/onionmind_desktop'), len(modules) * 2)

    def test_every_desktop_runtime_gets_the_encryption_library(self) -> None:
        """Encryption at rest needs cryptography wherever the desktop runs; a
        runtime without it cannot open an already encrypted data folder."""
        for name in ("install-onionmind.sh", "onionmind-setup.cmd", "update-onionmind.sh",
                     "update-onionmind.ps1", "requirements-desktop.txt", "pyproject.toml",
                     ".github/workflows/desktop-build.yml"):
            with self.subTest(file=name):
                self.assertIn("cryptography>=42,<51", self.text(name))
        for name in ("install-onionmind.sh", "onionmind-setup.cmd", "update-onionmind.sh",
                     "update-onionmind.ps1", "tools/build-desktop.ps1"):
            with self.subTest(import_check=name):
                self.assertIn("cryptography.hazmat.primitives.ciphers.aead", self.text(name))

    def test_desktop_runtime_is_used_only_after_an_import_check(self) -> None:
        for name in (
            "onionmind-setup.cmd",
            "install-onionmind.sh",
            "update-onionmind.ps1",
            "update-onionmind.sh",
        ):
            with self.subTest(name=name):
                text = self.text(name)
                self.assertIn(".onionmind-desktop-ready", text)
                self.assertIn("PySide6.QtWidgets", text)

    def test_network_asset_sets_are_pinned_to_one_resolved_revision(self) -> None:
        for name in (
            "onionmind-setup.cmd",
            "install-onionmind.sh",
            "update-onionmind.ps1",
            "update-onionmind.sh",
        ):
            with self.subTest(name=name):
                text = self.text(name)
                self.assertIn("api.github.com/repos/Codemaster64/onionmind/commits/main", text)
                self.assertIn("40", text)
                self.assertNotIn(
                    "onionmind/main/dsh-onionmind-tor-search.js", text
                )
                self.assertNotIn("onionmind/main/dsh-onionmind-tor.patch.yml", text)

    def test_dsh_yaml_paths_escape_single_quotes(self) -> None:
        self.assertIn("replace(\"'\", \"''\")", self.text("install-onionmind.sh"))
        self.assertIn(".Replace(\"'\", \"''\")", self.text("onionmind-setup.cmd"))
        self.assertIn("replace(\"'\", \"''\")", self.text("update-onionmind.sh"))
        self.assertIn(".Replace(\"'\", \"''\")", self.text("update-onionmind.ps1"))

    def test_one_click_windows_bootstrap_reads_itself_as_utf8(self) -> None:
        setup = self.text("onionmind-setup.cmd")
        self.assertIn('set "ONIONMIND_SETUP=%~f0"', setup)
        self.assertIn("ReadAllText($env:ONIONMIND_SETUP", setup)
        self.assertNotIn("Get-Content -Raw $env:ONIONMIND_SETUP", setup)

    def test_windows_setup_provisions_python_before_model_download(self) -> None:
        installer = self.text("onionmind-setup.cmd")
        python_install = installer.index("Python.Python.3.12")
        weight_download = installer.index("Downloading $file")
        self.assertLess(python_install, weight_download)
        self.assertIn("Python 3.10 or newer is required", installer)

    def test_updaters_stage_and_validate_before_replacement(self) -> None:
        powershell = self.text("update-onionmind.ps1")
        shell = self.text("update-onionmind.sh")
        self.assertIn("py_compile", powershell)
        self.assertIn("[IO.File]::Replace", powershell)
        self.assertIn("previous files were restored", shell)
        self.assertIn("py_compile", shell)

    def test_agent_launchers_preflight_the_supported_node_runtime(self) -> None:
        for name in (
            "onionmind-setup.cmd",
            "install-onionmind.sh",
            "update-onionmind.ps1",
            "update-onionmind.sh",
        ):
            with self.subTest(name=name):
                text = self.text(name)
                self.assertIn("Node.js ^22.19 or 24+", text)


if __name__ == "__main__":
    unittest.main()
