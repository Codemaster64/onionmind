"""Encryption at rest for the desktop data folder (onionmind_desktop_core vault).

Run with::

    python -m unittest tests/test_vault.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import onionmind_desktop_core as core


class MemoryKeystore:
    name, label, strong = "memory", "a test key store", True

    def __init__(self) -> None:
        self.key = None

    def get(self):
        return self.key

    def put(self, key):
        self.key = key

    def delete(self):
        self.key = None


SECRET = "the session talks about a private thing"


@unittest.skipUnless(core.encryption_available(), "the cryptography package is not installed")
class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "desktop"
        self.root.mkdir()
        self.store = MemoryKeystore()
        # Every vault in these tests uses the in-memory store, also when it is
        # looked up again by name from vault.json.
        patcher = mock.patch.object(core, "keystore_for", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def no_plaintext_anywhere(self) -> None:
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), path)

    def test_first_open_encrypts_what_an_older_version_left_in_plaintext(self) -> None:
        plain = core.SessionStore(self.root / "sessions")
        session = plain.create(title="Plans", messages=[{"role": "user", "content": SECRET}])
        core.SettingsStore(self.root / "settings.json").save({"remote": {"host": "secret-host"}})
        (self.root / "network-log.jsonl").write_text(
            json.dumps({"time": "2026-10-13T10:00:00", "route": "Tor", "what": SECRET}) + "\n")
        (self.root / "reverts" / "abc").mkdir(parents=True)
        (self.root / "reverts" / "abc" / "notes.txt").write_text(SECRET)

        vault = core.open_vault(self.root, keystore=self.store)

        self.no_plaintext_anywhere()
        self.assertNotIn(b"secret-host", (self.root / "settings.json").read_bytes())
        header = json.loads((self.root / core.VAULT_FILE).read_text())
        self.assertEqual(header["mode"], "device")
        self.assertNotIn("password", header)
        self.assertEqual(self.store.key and len(self.store.key), 32)
        # ...and everything still reads back through the vault.
        loaded = core.SessionStore(self.root / "sessions", vault).load(session.id)
        self.assertEqual(loaded.messages[0]["content"], SECRET)
        self.assertEqual(core.SettingsStore(self.root / "settings.json", vault=vault).load()["remote"]["host"],
                         "secret-host")
        self.assertEqual(core.EgressLog(self.root / "network-log.jsonl", vault).entries()[0]["what"], SECRET)
        self.assertEqual(core.read_data_file(self.root / "reverts" / "abc" / "notes.txt", vault), SECRET.encode())

    def test_new_writes_are_encrypted_and_read_back(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        sessions = core.SessionStore(self.root / "sessions", vault)
        session = sessions.create(title="T", messages=[{"role": "user", "content": SECRET}])
        sessions.archive(session.id)
        log = core.EgressLog(self.root / "network-log.jsonl", vault)
        log.append("Tor", SECRET)
        core.SettingsStore(self.root / "settings.json", vault=vault).save({"note": SECRET})
        self.no_plaintext_anywhere()
        self.assertEqual(sessions.list(archived=True)[0].messages[0]["content"], SECRET)
        self.assertEqual(log.entries()[0]["what"], SECRET)
        self.assertTrue(all(line.startswith("enc1:") for line in
                            (self.root / "network-log.jsonl").read_text().splitlines()))

    def test_reopening_uses_the_stored_key(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        core.SettingsStore(self.root / "settings.json", vault=vault).save({"note": SECRET})
        again = core.open_vault(self.root, keystore=self.store)
        self.assertEqual(core.SettingsStore(self.root / "settings.json", vault=again).load()["note"], SECRET)

    def test_without_the_vault_encrypted_files_are_left_alone_never_overwritten(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        core.SettingsStore(self.root / "settings.json", vault=vault).save({"note": SECRET})
        before = (self.root / "settings.json").read_bytes()
        plain = core.SettingsStore(self.root / "settings.json", defaults={"a": 1})
        self.assertEqual(plain.load(), {"a": 1})                 # unreadable, not "corrupt"
        with self.assertRaises(core.VaultError):
            plain.save({"note": "downgrade"})
        self.assertEqual((self.root / "settings.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("settings.json.corrupt-*")), [])

    def test_a_password_replaces_the_key_store_and_gates_opening(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        core.SettingsStore(self.root / "settings.json", vault=vault).save({"note": SECRET})
        vault.set_password("correct horse")
        self.assertIsNone(self.store.key)                        # nothing usable left on the machine
        self.assertTrue(core.vault_needs_password(self.root))
        header_text = (self.root / core.VAULT_FILE).read_text()
        self.assertNotIn("correct horse", header_text)
        with self.assertRaises(core.WrongPassword):
            core.open_vault(self.root)
        with self.assertRaises(core.WrongPassword):
            core.open_vault(self.root, password="wrong")
        opened = core.open_vault(self.root, password="correct horse")
        self.assertEqual(core.SettingsStore(self.root / "settings.json", vault=opened).load()["note"], SECRET)
        self.assertTrue(opened.check_password("correct horse"))
        self.assertFalse(opened.check_password("nope"))

    def test_locking_drops_the_key_until_the_password_brings_it_back(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        settings = core.SettingsStore(self.root / "settings.json", vault=vault)
        settings.save({"note": SECRET})
        self.assertFalse(vault.lock())                           # no password: nothing to lock
        self.assertFalse(vault.locked)
        vault.set_password("correct horse")
        self.assertTrue(vault.lock())
        self.assertTrue(vault.locked)
        with self.assertRaises(core.VaultLocked):
            vault.encrypt(b"x")
        with self.assertRaises(core.VaultLocked):
            vault.decrypt((self.root / "settings.json").read_bytes())
        with self.assertRaises(core.VaultLocked):
            vault.set_password("another one", current="correct horse")
        self.assertFalse(vault.check_password("wrong"))
        self.assertTrue(vault.check_password("correct horse"))  # checked without the key in memory
        self.assertTrue(vault.locked)
        with self.assertRaises(core.WrongPassword):
            vault.unlock("wrong")
        vault.unlock("correct horse")
        self.assertFalse(vault.locked)
        self.assertEqual(settings.load()["note"], SECRET)

    def test_changing_the_password_needs_the_current_one_and_keeps_the_data(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        core.SettingsStore(self.root / "settings.json", vault=vault).save({"note": SECRET})
        vault.set_password("one")
        with self.assertRaises(core.WrongPassword):
            vault.set_password("two", current="wrong")
        vault.set_password("two", current="one")
        with self.assertRaises(core.WrongPassword):
            core.open_vault(self.root, password="one")
        opened = core.open_vault(self.root, password="two")
        self.assertEqual(core.SettingsStore(self.root / "settings.json", vault=opened).load()["note"], SECRET)

    def test_removing_the_password_hands_the_key_back_to_the_key_store(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        vault.set_password("pw")
        with self.assertRaises(core.WrongPassword):
            vault.remove_password("wrong")
        vault.remove_password("pw")
        self.assertFalse(core.vault_needs_password(self.root))
        self.assertIsNotNone(self.store.key)
        reopened = core.open_vault(self.root)
        self.assertEqual(reopened.mode, "device")

    def test_a_lost_key_is_reported_not_replaced(self) -> None:
        core.open_vault(self.root, keystore=self.store)
        self.store.key = None
        with self.assertRaises(core.KeystoreUnavailable):
            core.open_vault(self.root)
        self.assertTrue((self.root / core.VAULT_FILE).is_file())   # nothing was reset behind your back

    def test_erase_removes_the_data_and_the_key(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        core.SessionStore(self.root / "sessions", vault).create(title="x")
        core.erase_data_folder(self.root)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIsNone(self.store.key)
        with self.assertRaises(core.VaultError):
            core.erase_data_folder(Path(self.temp.name))        # never a folder that is not a vault

    def test_a_tampered_file_is_unreadable_but_kept(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        sessions = core.SessionStore(self.root / "sessions", vault)
        session = sessions.create(title="x")
        path = self.root / "sessions" / f"{session.id}.json"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(bytes(data))
        self.assertIsNone(sessions.load(session.id))
        self.assertTrue(path.is_file())

    def test_revert_backups_are_encrypted_and_undo_restores_bytes_and_mode(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        vault = core.open_vault(self.root, keystore=self.store)
        project = Path(self.temp.name) / "project"
        project.mkdir()
        run = lambda *args: subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        script = project / "tool.sh"
        script.write_text("echo old\n")
        run("add", ".")
        run("commit", "-qm", "init")
        script.write_text(f"echo {SECRET}\n")
        os.chmod(script, 0o755)
        change = {"status": " M", "path": "tool.sh"}
        record = core.revert_changes(project, [change], self.root / "reverts", vault)
        self.assertEqual(script.read_text(), "echo old\n")
        self.no_plaintext_anywhere()
        self.assertEqual(core.undo_revert(record, vault), 1)
        self.assertEqual(script.read_text(), f"echo {SECRET}\n")
        if os.name != "nt":
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)

    def test_the_agent_log_moves_into_the_encrypted_log_and_the_plaintext_is_shredded(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        agent_log = Path(self.temp.name) / "agent-net.log"
        agent_log.write_text("2026-10-13 10:00:00 tor      example.org:443\n"
                             "2026-10-13 10:00:01 local    127.0.0.1:11434\n")
        log = core.EgressLog(self.root / "network-log.jsonl", vault)
        self.assertEqual(core.absorb_agent_net_log(agent_log, log), 1)
        self.assertFalse(agent_log.exists())
        self.assertIn("example.org", log.entries()[0]["what"])
        self.assertNotIn(b"example.org", (self.root / "network-log.jsonl").read_bytes())
        # Logging off: the lines are dropped, the file still goes.
        agent_log.write_text("2026-10-13 10:00:00 tor      example.org:443\n")
        self.assertEqual(core.absorb_agent_net_log(agent_log, None), 0)
        self.assertFalse(agent_log.exists())

    def test_the_agents_qwen_session_is_sealed_into_the_vault_and_restored_to_resume(self) -> None:
        vault = core.open_vault(self.root, keystore=self.store)
        base = Path(self.temp.name) / "runtime"
        base.mkdir()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base)}):
            scratch = core.open_agent_scratch(self.root, vault)
            self.assertEqual(scratch.parent, base)              # RAM-backed where there is one
            if os.name != "nt":
                self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
            chat = scratch / "projects" / "-home-me-proj" / "chats" / "abc-123.jsonl"
            chat.parent.mkdir(parents=True)
            chat.write_text(json.dumps({"text": SECRET}) + "\n")
            (scratch / core.AGENT_NET_LOG_NAME).write_text("2026-10-13 10:00:00 tor      example.org:443\n")
            (scratch / "shims").mkdir()
            (scratch / "shims" / "sitecustomize.py").write_text("# shim\n")
            self.assertTrue(core.seal_agent_scratch(self.root, vault, scratch, "abc-123"))
            self.assertFalse(scratch.exists())                   # the plaintext is gone
            self.no_plaintext_anywhere()
            archive = self.root / core.AGENT_ARCHIVE_DIR / "abc-123.tar"
            self.assertTrue(core.is_encrypted(archive.read_bytes()))

            resumed = core.open_agent_scratch(self.root, vault, resume="abc-123")
            restored = resumed / "projects" / "-home-me-proj" / "chats" / "abc-123.jsonl"
            self.assertIn(SECRET, restored.read_text())
            self.assertFalse((resumed / core.AGENT_NET_LOG_NAME).exists())
            self.assertFalse((resumed / "shims").exists())
            # A run with no session id keeps nothing; a bad id never names a path.
            self.assertFalse(core.seal_agent_scratch(self.root, vault, resumed, "../escape"))
            self.assertFalse(resumed.exists())

            # A run the app never saw end: its log is taken in, the folder shredded.
            left = core.open_agent_scratch(self.root, vault)
            (left / core.AGENT_NET_LOG_NAME).write_text("2026-10-13 10:00:00 tor      example.net:443\n")
            log = core.EgressLog(self.root / "network-log.jsonl", vault)
            self.assertEqual(core.sweep_agent_scratch(log), 1)
            self.assertFalse(left.exists())
            self.assertIn("example.net", log.entries()[0]["what"])


@unittest.skipUnless(core.encryption_available(), "the cryptography package is not installed")
class KeystoreTests(unittest.TestCase):
    """Each real key store round-trips a key on the platform that has it."""

    def round_trip(self, store, may_be_missing: bool = True) -> None:
        key = os.urandom(32)
        try:
            store.put(key)
        except core.KeystoreUnavailable as exc:
            if not may_be_missing:
                raise
            self.skipTest(f"{store.name} unavailable here: {exc}")
        try:
            self.assertEqual(store.get(), key)
        finally:
            store.delete()
        self.assertIsNone(store.get())

    def test_file_keystore_is_private_to_this_user(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = core.FileKeystore("test", Path(temporary))
            key = os.urandom(32)
            store.put(key)
            self.assertEqual(store.get(), key)
            if os.name != "nt":
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            store.delete()
            self.assertIsNone(store.get())

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows only")
    def test_dpapi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = core.DpapiKeystore(Path(temporary) / "device-key.dpapi")
            self.round_trip(store, may_be_missing=False)      # DPAPI is always there on Windows

    @unittest.skipUnless(sys.platform == "darwin", "the Keychain is macOS only")
    def test_mac_keychain(self) -> None:
        self.round_trip(core.MacKeychain(f"test-{os.getpid()}"))

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("secret-tool"), "needs secret-tool")
    def test_secret_service(self) -> None:
        self.round_trip(core.SecretServiceKeystore(f"test-{os.getpid()}"))

    def test_platform_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = core.keystore_for(temporary)
            expected = ("dpapi" if os.name == "nt" else "keychain" if sys.platform == "darwin"
                        else "secret-service" if shutil.which("secret-tool") else "file")
            self.assertEqual(store.name, expected)
            self.assertEqual(core.keystore_for(temporary, "file").name, "file")


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox

    import onionmind_desktop as ui

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
try:
    import onionmind_tui as tui
except ImportError:
    tui = None


class _Core:
    MODEL = "inferno"
    BACKEND = "onionmind"


@unittest.skipUnless(QT_AVAILABLE and core.encryption_available(), "needs PySide6 and cryptography")
class VaultDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "desktop"
        self.store = MemoryKeystore()
        for target, name, value in ((core, "keystore_for", mock.Mock(return_value=self.store)),
                                    (ui, "desktop_data_root", mock.Mock(return_value=self.root))):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def dialogs(self, *steps):
        """Answer the UnlockDialogs in order: (outcome, text) per dialog shown."""
        shown = []
        answers = list(steps)

        def run(dialog):
            shown.append(dialog.kind)
            outcome, text = answers.pop(0)
            if outcome == "unlocked":
                dialog.password.setText(text)
                dialog._submit()
                if dialog.outcome != "unlocked":
                    dialog.outcome = "quit"
                    shown.append(dialog.error.text())
            else:
                dialog.outcome = outcome
            return 0

        patcher = mock.patch.object(ui.UnlockDialog, "exec", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return shown

    def window(self, vault):
        window = ui.OnionmindWindow(_Core(), core, vault=vault)
        self.addCleanup(window.close)
        return window

    def test_first_start_encrypts_and_the_window_writes_only_ciphertext(self) -> None:
        vault, notice = ui._open_vault_for_startup(core, self.root)
        self.assertEqual(notice, "")
        window = self.window(vault)
        session = window.session_bridge.create("Plans", "m", None, [{"role": "user", "content": SECRET}])
        window.settings_data["note"] = SECRET
        window.settings_bridge.save(window.settings_data)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), path)
        self.assertEqual(window.session_bridge.list()[0].messages[0]["content"], SECRET)
        self.assertEqual(session.title, "Plans")

    def test_password_from_settings_then_unlock_at_next_start(self) -> None:
        vault, _notice = ui._open_vault_for_startup(core, self.root)
        window = self.window(vault)
        dialog = ui.SettingsDialog(self.root, "", window.update_bridge, window)
        self.addCleanup(dialog.close)
        self.assertIn("test key store", dialog.encryption_status.text())
        dialog.password_new.setText("short")
        dialog.password_repeat.setText("short")
        dialog._set_password()
        self.assertIn("at least 8", dialog.password_feedback.text())
        dialog.password_new.setText("a long password")
        dialog.password_repeat.setText("a long password")
        dialog._set_password()
        self.assertTrue(vault.has_password)
        self.assertTrue(window.has_passphrase())          # the idle lock now has its secret
        self.assertTrue(window._verify_passphrase("a long password"))
        self.assertFalse(window._verify_passphrase("nope"))
        self.assertFalse(dialog.passphrase_field.isVisibleTo(dialog))

        shown = self.dialogs(("unlocked", "wrong"), ("unlocked", "a long password"))
        self.assertIs(ui._open_vault_for_startup(core, self.root)[0], ui._QUIT)
        self.assertEqual(shown, ["password", "That password is not right."])
        reopened, _ = ui._open_vault_for_startup(core, self.root)
        self.assertTrue(reopened.has_password)

    def test_the_idle_lock_drops_the_key_after_a_run_ends_and_unlock_brings_it_back(self) -> None:
        vault, _notice = ui._open_vault_for_startup(core, self.root)
        window = self.window(vault)
        window.session_bridge.create("Plans", "m", None, [{"role": "user", "content": SECRET}])
        window.features["lock"] = True
        window.open_settings_tab = lambda label: None
        self.assertTrue(window.set_passphrase("a long password"))
        window.active_kind = "chat"                      # a run is still going
        self.assertTrue(window.lock_now())
        self.assertFalse(vault.locked)                   # it keeps the key to save its result
        window._set_active(None)
        self.app.processEvents()
        self.assertTrue(vault.locked)
        self.assertEqual(window.session_bridge.list(), [])   # nothing readable while locked
        overlay = window.lock_overlay
        overlay.passphrase.setText("wrong")
        overlay.try_unlock()
        self.assertTrue(vault.locked)
        self.assertFalse(overlay.isHidden())
        overlay.passphrase.setText("a long password")
        overlay.try_unlock()
        self.assertFalse(vault.locked)
        self.assertTrue(overlay.isHidden())
        self.assertEqual(window.session_bridge.list()[0].messages[0]["content"], SECRET)

    def test_an_agent_run_gets_a_private_scratch_folder_sealed_when_it_ends(self) -> None:
        vault, _notice = ui._open_vault_for_startup(core, self.root)
        window = self.window(vault)
        base = Path(self.temp.name) / "runtime"
        base.mkdir()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base)}):
            env = window._open_agent_scratch()
            scratch = Path(env["QWEN_RUNTIME_DIR"])
            self.assertTrue(scratch.is_dir())
            self.assertEqual(Path(env["ONIONMIND_NET_LOG"]).parent, scratch)
            (scratch / "projects").mkdir()
            (scratch / "projects" / "chat.jsonl").write_text(SECRET)
            window._agent_session_id = "run-1"
            window._seal_agent_scratch()
            self.assertFalse(scratch.exists())
            self.assertIsNone(window._agent_scratch)
            self.assertTrue((self.root / core.AGENT_ARCHIVE_DIR / "run-1.tar").is_file())
            # Resuming that session restores it into a new scratch folder.
            again = Path(window._open_agent_scratch()["QWEN_RUNTIME_DIR"])
            self.assertEqual((again / "projects" / "chat.jsonl").read_text(), SECRET)
            window._seal_agent_scratch()
        unencrypted = self.window(None)
        self.assertEqual(unencrypted._open_agent_scratch(), {})     # no vault: as before

    def test_forgotten_password_erases_after_confirmation(self) -> None:
        vault, _ = ui._open_vault_for_startup(core, self.root)
        core.SessionStore(self.root / "sessions", vault).create(title="x")
        vault.set_password("a long password")
        self.dialogs(("erase", ""))
        fresh, _ = ui._open_vault_for_startup(core, self.root)
        self.assertFalse(fresh.has_password)
        self.assertEqual(list(self.root.rglob("*.json")), [self.root / core.VAULT_FILE])

    def test_the_erase_button_asks_first(self) -> None:
        dialog = ui.UnlockDialog("password", lambda text: None)
        with mock.patch.object(ui.QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel):
            dialog._secondary()
        self.assertNotEqual(dialog.outcome, "erase")
        with mock.patch.object(ui.QMessageBox, "warning", return_value=QMessageBox.StandardButton.Discard):
            dialog._secondary()
        self.assertEqual(dialog.outcome, "erase")

    def test_a_missing_key_offers_quit_or_erase_never_a_new_key(self) -> None:
        ui._open_vault_for_startup(core, self.root)
        self.store.key = None
        shown = self.dialogs(("quit", ""))
        self.assertIs(ui._open_vault_for_startup(core, self.root)[0], ui._QUIT)
        self.assertEqual(shown, ["missing-key"])

    def test_an_old_lock_passphrase_becomes_the_password(self) -> None:
        self.root.mkdir(parents=True)
        core.SettingsStore(self.root / "settings.json").save({"lock": {"passphrase": core.hash_passphrase("old secret"),
                                                                       "lock_after": 5}})
        shown = self.dialogs(("unlocked", "old secret"))
        vault, _ = ui._open_vault_for_startup(core, self.root)
        self.assertEqual(shown, ["upgrade"])
        self.assertTrue(vault.has_password)
        self.assertTrue(vault.check_password("old secret"))
        settings = core.SettingsStore(self.root / "settings.json", vault=vault).load()
        self.assertEqual(settings["lock"], {"lock_after": 5})

    def test_without_cryptography_existing_encrypted_data_is_never_opened_as_plaintext(self) -> None:
        ui._open_vault_for_startup(core, self.root)
        with mock.patch.object(core, "encryption_available", return_value=False), \
             mock.patch.object(ui.QMessageBox, "critical") as critical:
            self.assertIs(ui._open_vault_for_startup(core, self.root)[0], ui._QUIT)
        critical.assert_called_once()
        other = Path(self.temp.name) / "fresh"
        with mock.patch.object(core, "encryption_available", return_value=False):
            vault, notice = ui._open_vault_for_startup(core, other)
        self.assertIsNone(vault)
        self.assertIn("Not encrypted", notice)

    @unittest.skipIf(tui is None, "textual is not installed")
    def test_tui_opens_the_same_vault_with_a_typed_password(self) -> None:
        vault, _ = ui._open_vault_for_startup(core, self.root)
        core.SessionStore(self.root / "sessions", vault).create(title="Shared", messages=[{"role": "user", "content": SECRET}])
        vault.set_password("a long password")
        answers = iter(["wrong", "a long password"])
        opened, problem = tui.open_vault(self.root, ask=lambda prompt: next(answers))
        self.assertEqual(problem, "")
        self.assertEqual(core.SessionStore(self.root / "sessions", opened).list()[0].title, "Shared")
        opened, problem = tui.open_vault(self.root, ask=lambda prompt: "wrong")
        self.assertIsNone(opened)
        self.assertIn("Three wrong passwords", problem)


if __name__ == "__main__":
    unittest.main()
