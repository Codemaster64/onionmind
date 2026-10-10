"""The v2 workbench features: each one off (nothing new shows) and on (it works).

Every feature sits behind one flag in window.features. These tests build the
real window over the real desktop core, with the inference core stubbed the
way tests/test_desktop_ui.py stubs it, and drive the controls a person would.

Run: python tests/test_desktop_v2_ui.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QProcess, QStandardPaths, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QWidget

    import onionmind_desktop as ui
    import onionmind_desktop_core as desktop_core

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class _CoreStub:
    MODEL = "inferno"
    BACKEND = "onionmind"


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class V2Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _wait_until(self, predicate, timeout_ms: int = 4000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def _window(self, core=None, **features) -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(core or _CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        for key, on in features.items():
            window.set_feature(key, on)
        window.show()
        self.app.processEvents()
        return window

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _settings(self, window) -> "ui.SettingsDialog":
        dialog = ui.SettingsDialog(window.data_root, "limitation", None, window)
        self.addCleanup(lambda: (dialog.done(0), dialog.deleteLater()))
        return dialog

    @staticmethod
    def _tab_labels(dialog) -> list[str]:
        return [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]


class FeatureStoreTests(V2Base):
    def test_defaults_leave_the_original_workbench_and_only_web_ask_on(self) -> None:
        window = self._window()
        try:
            self.assertEqual({k for k, on in window.features.items() if on}, {"webAsk"})
            self.assertEqual(len(window.features), 24)
            self.assertFalse(window.features_button.isHidden())
            dialog = self._settings(window)
            self.assertEqual(self._tab_labels(dialog),
                             ["General", "Privacy", "Updates", "Storage", "Features"])
        finally:
            self._close(window)

    def test_popover_lists_every_feature_and_turns_them_all_on_and_off(self) -> None:
        window = self._window()
        try:
            window.features_button.click()
            popover = window._features_popover
            self.assertTrue(self._wait_until(popover.isVisible))
            self.assertEqual(len(popover.toggles.boxes), 24)
            popover.toggles.all_on.click()
            self.assertTrue(all(window.features.values()))
            self.assertEqual(popover.toggles.count_label.text(), "24 of 24 on")
            self.assertTrue(popover.toggles.run_setup.isVisibleTo(popover))
            popover.toggles.all_off.click()
            self.assertFalse(any(window.features.values()))
            popover.toggles.boxes["palette"].click()
            self.assertTrue(window.features["palette"])
            popover.close()
        finally:
            self._close(window)

    def test_settings_tab_and_popover_drive_the_same_flags(self) -> None:
        window = self._window()
        try:
            dialog = self._settings(window)
            dialog.feature_toggles.boxes["zones"].click()
            self.assertTrue(window.features["zones"])
            window.set_feature("zones", False)
            self.assertFalse(dialog.feature_toggles.boxes["zones"].isChecked())
        finally:
            self._close(window)

    def test_flags_are_saved_between_launches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = ui.SettingsBridge(desktop_core, Path(tmp))
            window = self._window()
            try:
                window.demo = False
                window.settings_bridge = bridge
                window.set_feature("palette", True)
                window.set_feature("webAsk", False)
            finally:
                window.demo = True
                self._close(window)
            stored = bridge.load()["features"]
            self.assertTrue(stored["palette"])
            self.assertFalse(stored["webAsk"])
            self.assertEqual(desktop_core.load_features(bridge.load())["palette"], True)

    def test_without_the_desktop_core_there_are_no_v2_features(self) -> None:
        window = ui.OnionmindWindow(_CoreStub(), None, demo=True)
        try:
            self.assertEqual(window.features, {})
            self.assertTrue(window.features_button.isHidden())
        finally:
            self._close(window)


if __name__ == "__main__":
    unittest.main()
