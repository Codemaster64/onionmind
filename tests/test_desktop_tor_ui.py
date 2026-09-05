"""Qt state contracts for the in-app background-Tor indicator."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QCheckBox
    import onionmind_desktop as desktop
except ModuleNotFoundError:
    QApplication = None
    QCheckBox = None
    desktop = None


class ActivityRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def append_activity(self, message: str) -> None:
        self.messages.append(message)


@unittest.skipIf(QApplication is None, "PySide6 is not installed in this test environment")
class TorIndicatorStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def state_host(self, *, port=None, managed=None, verified=None):
        enabled = {"value": True}
        self.status_messages = []
        core = SimpleNamespace(
            _managed_tor_process=managed,
            _port=verified,
            tor_proxy_port=lambda: port,
            tor_enabled=lambda: enabled["value"],
            set_tor_enabled=lambda value: enabled.update(value=bool(value)),
        )
        self.autochecks = []
        return SimpleNamespace(
            core=core,
            tor_status=desktop.StatusActionButton("Tor", "Off", "idle", "Turn on"),
            tor_probe_generation=2,
            tor_phase="off",
            inspector=ActivityRecorder(),
            _maybe_autocheck_updates=lambda: self.autochecks.append("autocheck"),
            set_status=self.status_messages.append,
        )

    def test_late_startup_probe_cannot_overwrite_starting(self) -> None:
        host = self.state_host()
        host.tor_phase = "starting"
        host.tor_status.set_status("Checking…", "warn")
        desktop.OnionmindWindow._tor_probe_complete(host, None, generation=1)
        self.assertEqual(host.tor_status.status_text(), "Checking…")
        self.assertEqual(host.tor_status.text(), "Tor is off")
        self.assertEqual(host.tor_phase, "starting")

    def test_visible_copy_is_only_on_or_off(self) -> None:
        control = desktop.StatusActionButton("Tor", "Off", "idle", "Turn on")
        for status, state, expected in (
            ("Off", "idle", "Tor is off"),
            ("Checking…", "warn", "Tor is off"),
            ("Unavailable", "bad", "Tor is off"),
            ("Ready", "good", "Tor is on"),
        ):
            with self.subTest(status=status):
                control.set_status(status, state)
                self.assertEqual(control.text(), expected)
                self.assertTrue(control.accessibleName().startswith(expected + "."))
                self.assertEqual(control.iconSize().width(), 36)
                self.assertEqual(control.iconSize().height(), 36)

    def test_control_keeps_a_visible_button_surface_and_border_at_rest(self) -> None:
        rule = desktop._STYLE_TEMPLATE.split("QPushButton#torStatusAction {", 1)[1].split("}", 1)[0]
        self.assertIn("background: #211f1c", rule)
        self.assertIn("border: 1px solid #514b44", rule)
        self.assertNotIn("transparent", rule)

    def test_connecting_icon_is_spinner_only_and_power_icon_stays_inside_target(self) -> None:
        ready = desktop._tor_control_icon("ready").pixmap(36, 36).toImage()
        checking = desktop._tor_control_icon("checking").pixmap(36, 36).toImage()

        # The ready icon's stem is present in the center; connecting leaves the
        # center as the flat halo color instead of stacking a power glyph there.
        stem = ready.pixelColor(18, 13)
        self.assertGreaterEqual(min(stem.red(), stem.green(), stem.blue()), 245)
        gap = ready.pixelColor(14, 10)
        self.assertGreater(gap.green(), gap.red())
        self.assertEqual(checking.pixelColor(18, 18), checking.pixelColor(18, 17))

        # Neither authored shape reaches the canvas edge, which catches clipped
        # circles when the compact toolbar target is resized again.
        for image in (ready, checking):
            for point in ((0, 18), (35, 18), (18, 0), (18, 35)):
                self.assertEqual(image.pixelColor(*point).alpha(), 0)

    def test_connecting_state_animates_the_circular_target_then_stops(self) -> None:
        with mock.patch.object(desktop, "_ui_animations_enabled", return_value=True):
            control = desktop.StatusActionButton("Tor", "Off", "idle", "Turn on")
            control.show()
            self.app.processEvents()
            control.set_status("Checking…", "warn")
            self.assertTrue(control._spinner_timer.isActive())
            first_frame = control.icon().cacheKey()
            control._advance_spinner()
            self.assertNotEqual(control.icon().cacheKey(), first_frame)
            control.set_status("Ready", "good")
            self.assertFalse(control._spinner_timer.isActive())
            control.close()

        with mock.patch.object(desktop, "_ui_animations_enabled", return_value=False):
            control = desktop.StatusActionButton("Tor", "Off", "idle", "Turn on")
            control.show()
            self.app.processEvents()
            control.set_status("Checking…", "warn")
            self.assertFalse(control._spinner_timer.isActive())
            self.assertFalse(control.icon().isNull())
            control.close()

    def test_unverified_listener_is_not_claimed_as_tor(self) -> None:
        host = self.state_host(port=9150)
        desktop.OnionmindWindow._show_local_tor_state(host, 9150)
        self.assertEqual(host.tor_status.status_text(), "Unavailable")
        self.assertEqual(host.tor_status.property("torState"), "error")
        self.assertEqual(host.tor_status.text(), "Tor is off")
        self.assertEqual(host.tor_phase, "error")
        self.assertEqual(host.tor_status.action_text(), "Retry")
        self.assertIn("Click to try verification again", host.tor_status.toolTip())
        self.assertNotIn("9150", host.tor_status.toolTip())

    def test_verified_managed_process_transitions_checking_to_ready(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        host = self.state_host(port=9150, managed=process, verified=9150)
        host.tor_phase = "starting"
        desktop.OnionmindWindow._show_local_tor_state(host, 9150)
        self.assertEqual(host.tor_status.status_text(), "Ready")
        self.assertEqual(host.tor_status.text(), "Tor is on")
        self.assertEqual(host.tor_status.property("torState"), "ready")
        self.assertEqual(host.tor_phase, "running")
        self.assertEqual(host.tor_status.action_text(), "Turn off")
        # A verified circuit is the updater's only window, so the Running
        # transition is exactly where the permissioned autocheck piggybacks.
        self.assertEqual(self.autochecks, ["autocheck"])

    def test_unverified_managed_process_is_not_ready_or_green(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        host = self.state_host(port=9150, managed=process)
        desktop.OnionmindWindow._show_local_tor_state(host, 9150)
        self.assertEqual(host.tor_status.status_text(), "Unavailable")
        self.assertEqual(host.tor_status.property("torState"), "error")
        self.assertEqual(host.tor_status.text(), "Tor is off")
        self.assertEqual(self.autochecks, [])

    def test_toolbar_start_replaces_checking_with_verified_ready(self) -> None:
        host = self.state_host(port=9150, verified=9150)
        host.tor_phase = "starting"
        host.tor_stop_event = object()
        host._show_local_tor_state = lambda port: desktop.OnionmindWindow._show_local_tor_state(host, port)
        desktop.OnionmindWindow._toolbar_tor_started(host, 9150, generation=2)
        self.assertEqual(host.tor_status.status_text(), "Ready")
        self.assertEqual(host.tor_status.action_text(), "Turn off")
        self.assertEqual(self.status_messages, ["Tor is ready."])
        self.assertNotIn("9150", host.tor_status.toolTip())

    def test_exited_managed_process_transitions_running_to_off(self) -> None:
        process = mock.Mock()
        process.poll.return_value = 1
        host = self.state_host(port=None, managed=process, verified=9150)
        host.tor_phase = "running"

        def stop() -> None:
            host.core._managed_tor_process = None
            host.core._port = None

        host.core.stop_managed_tor = stop
        host._show_local_tor_state = lambda port: desktop.OnionmindWindow._show_local_tor_state(host, port)
        desktop.OnionmindWindow._poll_tor_liveness(host)
        self.assertEqual(host.tor_status.status_text(), "Off")
        self.assertEqual(host.tor_phase, "off")
        self.assertEqual(host.tor_status.action_text(), "Turn on")

    def test_replacement_listener_downgrades_ready_to_unavailable(self) -> None:
        process = mock.Mock()
        process.poll.return_value = 1
        host = self.state_host(port=9150, managed=process, verified=9150)
        host.tor_phase = "running"

        def stop() -> None:
            host.core._managed_tor_process = None
            host.core._port = None

        host.core.stop_managed_tor = stop
        host._show_local_tor_state = lambda port: desktop.OnionmindWindow._show_local_tor_state(host, port)
        desktop.OnionmindWindow._poll_tor_liveness(host)
        self.assertEqual(host.tor_status.status_text(), "Unavailable")
        self.assertEqual(host.tor_phase, "error")

    def test_live_owned_process_without_listener_is_stopped_before_off(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        host = self.state_host(port=None, managed=process)
        calls = []

        def stop() -> None:
            calls.append("stopped")
            host.core._managed_tor_process = None
            host.core._port = None

        host.core.stop_managed_tor = stop
        desktop.OnionmindWindow._show_local_tor_state(host, None)
        self.assertEqual(calls, ["stopped"])
        self.assertEqual(host.tor_status.status_text(), "Off")

    def test_search_permission_cannot_be_changed_during_a_run(self) -> None:
        consent = QCheckBox()
        consent.setChecked(True)
        host = SimpleNamespace(
            active_kind=None,
            send_button=mock.Mock(),
            retry_button=mock.Mock(),
            chat_button=mock.Mock(),
            agent_button=mock.Mock(),
            model_combo=mock.Mock(),
            search_consent=consent,
            left_rail=SimpleNamespace(projects=mock.Mock(), sessions=mock.Mock()),
            stop_event=None,
            harness_process=None,
            _sync_action_states=mock.Mock(),
        )
        desktop.OnionmindWindow._set_active(host, "chat")
        self.assertFalse(consent.isEnabled())
        self.assertFalse(consent.isChecked())
        host._sync_action_states.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
