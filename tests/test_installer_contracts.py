"""Regression contracts for the generated Onionmind installers and updaters."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
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
            "install-onionmind.ps1",
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
            "install-onionmind.ps1",
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
        self.assertIn(".Replace(\"'\", \"''\")", self.text("install-onionmind.ps1"))
        self.assertIn("replace(\"'\", \"''\")", self.text("update-onionmind.sh"))
        self.assertIn(".Replace(\"'\", \"''\")", self.text("update-onionmind.ps1"))

    def test_one_click_windows_bootstrap_reads_itself_as_utf8(self) -> None:
        setup = self.text("onionmind-setup.cmd")
        self.assertIn('set "ONIONMIND_SETUP=%~f0"', setup)
        self.assertIn("ReadAllText($env:ONIONMIND_SETUP", setup)
        self.assertNotIn("Get-Content -Raw $env:ONIONMIND_SETUP", setup)

    def test_windows_setup_provisions_python_before_model_download(self) -> None:
        installer = self.text("install-onionmind.ps1")
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
            "install-onionmind.ps1",
            "install-onionmind.sh",
            "update-onionmind.ps1",
            "update-onionmind.sh",
        ):
            with self.subTest(name=name):
                text = self.text(name)
                self.assertIn("Node.js ^22.19 or 24+", text)


if __name__ == "__main__":
    unittest.main()
