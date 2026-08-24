"""Qt state contracts for the in-app background-Tor indicator."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock


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
        core = SimpleNamespace(
            _managed_tor_process=managed,
            _port=verified,
            tor_proxy_port=lambda: port,
        )
        return SimpleNamespace(
            core=core,
            tor_status=desktop.StatusPill("Tor", "Off", "idle"),
            tor_probe_generation=2,
            tor_phase="off",
            inspector=ActivityRecorder(),
        )

    def test_late_startup_probe_cannot_overwrite_starting(self) -> None:
        host = self.state_host()
        host.tor_phase = "starting"
        host.tor_status.set_status("Starting", "busy")
        desktop.OnionmindWindow._tor_probe_complete(host, None, generation=1)
        self.assertEqual(host.tor_status.label.text(), "Starting")
        self.assertEqual(host.tor_phase, "starting")

    def test_unverified_listener_is_not_claimed_as_tor(self) -> None:
        host = self.state_host(port=9150)
        desktop.OnionmindWindow._show_local_tor_state(host, 9150)
        self.assertEqual(host.tor_status.label.text(), "Proxy · 9150")
        self.assertEqual(host.tor_phase, "proxy")

    def test_managed_hidden_process_transitions_starting_to_running(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        host = self.state_host(port=9150, managed=process)
        host.tor_phase = "starting"
        desktop.OnionmindWindow._show_local_tor_state(host, 9150)
        self.assertEqual(host.tor_status.label.text(), "Running · 9150")
        self.assertEqual(host.tor_phase, "running")

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
        self.assertEqual(host.tor_status.label.text(), "Off")
        self.assertEqual(host.tor_phase, "off")

    def test_replacement_listener_downgrades_running_to_unverified_proxy(self) -> None:
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
        self.assertEqual(host.tor_status.label.text(), "Proxy · 9150")
        self.assertEqual(host.tor_phase, "proxy")

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
        self.assertEqual(host.tor_status.label.text(), "Off")

    def test_search_permission_cannot_be_changed_during_a_run(self) -> None:
        consent = QCheckBox()
        consent.setChecked(True)
        host = SimpleNamespace(
            active_kind=None,
            send_button=mock.Mock(),
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
