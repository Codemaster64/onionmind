"""Regression contracts for the generated Onionmind installers and updaters."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
    def test_download_links_point_at_release_assets_not_raw_files(self) -> None:
        """A raw github link renders the script; a release asset downloads it.

        github.com/<repo>/raw/... serves text/plain with no Content-Disposition,
        so clicking a download badge opened the installer as a page of code
        instead of saving it. Release assets are served as
        `Content-Disposition: attachment`, so every downloadable script has to
        be attached to the rolling release AND linked from there.
        """
        downloadable = (
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
