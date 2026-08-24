"""Static contracts keeping platform release metadata aligned."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def capture(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise AssertionError(f"Could not find {label}")
    return match.group(1)


class ReleaseVersionContractTests(unittest.TestCase):
    def test_platform_release_metadata_is_on_onionmind_1_4(self) -> None:
        project_version = capture(
            r'^version\s*=\s*"([^"]+)"$',
            read("pyproject.toml"),
            "Python project version",
        )
        android_version = capture(
            r'^\s*versionName\s*=\s*"([^"]+)"$',
            read("android/app/build.gradle.kts"),
            "Android versionName",
        )
        windows_metadata = re.findall(
            r'"--(file|product)-version=([^"]+)"',
            read("tools/build-desktop.ps1"),
        )
        android_apk_names = set(
            re.findall(
                r"Onionmind-[0-9]+(?:\.[0-9]+)*\.apk",
                read("android/Dockerfile"),
            )
        )

        self.assertEqual(project_version, "1.4.0")
        self.assertEqual(android_version, "1.4")
        self.assertEqual(project_version.removesuffix(".0"), android_version)
        self.assertEqual(len(windows_metadata), 2)
        self.assertEqual(dict(windows_metadata), {
            "file": project_version,
            "product": project_version,
        })
        self.assertEqual(android_apk_names, {f"Onionmind-{android_version}.apk"})


if __name__ == "__main__":
    unittest.main()
