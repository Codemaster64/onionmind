"""Tests for the pure Onionmind desktop support module.

Run with::

    python -m unittest tests/test_desktop_core.py
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import onionmind_desktop_core as core
import onionmind_desktop_hardware as hardware


class ModelDisplayTests(unittest.TestCase):
    def test_tiers_aliases_and_tags_keep_the_raw_identifier(self) -> None:
        self.assertEqual(
            core.ONIONMIND_TIERS,
            (
                "SPARK",
                "EMBER",
                "BLAZE",
                "INFERNO",
                "CINDER",
                "WILDFIRE",
                "FLASHPOINT",
                "PHOENIX",
                "NOVA",
                "PYRE",
            ),
        )

        tagged = core.describe_model("inferno:latest")
        self.assertEqual(tagged.raw_id, "inferno:latest")
        self.assertEqual(tagged.tier, "INFERNO")
        self.assertEqual(tagged.tag, "latest")
        self.assertEqual(tagged.display_name, "Qwen3.8 27B · heavy - ~12-16 GB VRAM")

        size_alias = core.describe_model("registry.local/onion/qwen3.5:9b")
        self.assertEqual(size_alias.raw_id, "registry.local/onion/qwen3.5:9b")
        self.assertEqual(size_alias.tier, "BLAZE")
        self.assertEqual(size_alias.tag, "9b")

        vision = core.describe_model("onionmind-inferno-vision:Q4_K_M")
        self.assertEqual(vision.tier, "INFERNO")
        self.assertEqual(vision.tag, "Q4_K_M")
        self.assertEqual(
            vision.display_name, "Qwen3.8 27B vision · heavy - ~12-16 GB VRAM"
        )

        # A model already named after what it is keeps that name, and still says
        # what it costs to run.
        self.assertEqual(
            size_alias.display_name,
            "registry.local/onion/qwen3.5:9b · moderate - ~7 GB VRAM",
        )

        unknown = core.describe_model("deepseek-r1:8b")
        self.assertIsNone(unknown.tier)
        self.assertEqual(unknown.display_name, "deepseek-r1:8b")

    def test_model_choices_dedupe_only_exact_raw_ids(self) -> None:
        choices = core.model_displays(
            ["inferno", "inferno:latest", "inferno", "custom/model:Q5"]
        )
        self.assertEqual(
            [choice.raw_id for choice in choices],
            ["inferno", "inferno:latest", "custom/model:Q5"],
        )


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_is_atomic_and_merges_defaults(self) -> None:
        with tempfile.TemporaryDirectory(prefix="onion mind settings ") as temporary:
            path = Path(temporary) / "state" / "settings.json"
            store = core.SettingsStore(path, {"theme": "dark", "font_size": 14})

            self.assertEqual(store.load(), {"theme": "dark", "font_size": 14})
            saved = store.save({"font_size": 16, "model": "inferno:latest"})
            self.assertEqual(
                saved,
                {"theme": "dark", "font_size": 16, "model": "inferno:latest"},
            )
            self.assertEqual(store.load(), saved)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_failed_atomic_replace_leaves_previous_settings_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            store = core.SettingsStore(path)
            store.save({"theme": "dark"})

            with mock.patch.object(core.os, "replace", side_effect=OSError("busy")):
                with self.assertRaises(OSError):
                    store.save({"theme": "light"})

            self.assertEqual(store.load(), {"theme": "dark"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_settings_are_quarantined_and_defaults_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text("{ definitely not json", encoding="utf-8")
            store = core.SettingsStore(path, {"theme": "dark"})

            self.assertEqual(store.load(), {"theme": "dark"})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(path.parent.glob("settings.json.corrupt-*"))), 1)


class SessionStoreTests(unittest.TestCase):
    def test_unmatched_reasoning_opening_keeps_the_visible_prefix_once(self) -> None:
        self.assertEqual(core.strip_thinking("Before<think>SECRET"), "Before")
        self.assertEqual(
            core.strip_thinking(
                "Before<think>first</think> middle<think>SECOND SECRET"
            ),
            "Before middle",
        )

    def test_variant_and_truncated_reasoning_never_reaches_persistence(self) -> None:
        content = (
            "Before< THINK data-origin=legacy>SECRET</ THINK > after< THI"
        )
        self.assertEqual(core.strip_thinking(content), "Before after")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            store = core.SessionStore(root)
            session = store.create(
                messages=[{"role": "assistant", "content": content}]
            )
            saved = (root / f"{session.id}.json").read_text(encoding="utf-8")

        self.assertEqual(session.messages[0]["content"], "Before after")
        self.assertNotIn("SECRET", saved)
        self.assertNotIn("< THI", saved)

    def test_reasoning_is_removed_at_every_session_store_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            store = core.SessionStore(root)
            tool_call = {
                "function": {
                    "name": "web_search",
                    "arguments": {"query": "local privacy"},
                }
            }
            session = store.create(
                messages=[
                    {"role": "user", "content": "Research this"},
                    {
                        "role": "assistant",
                        "content": "<think>FIRST SECRET</think>I will search.",
                        "thinking": "STRUCTURED SECRET",
                        "tool_calls": [tool_call],
                    },
                    {"role": "tool", "content": "local result"},
                    {
                        "role": "assistant",
                        "content": "Before<think>SECOND SECRET</think> after",
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "<think>NESTED SECRET</think>Nested answer",
                            }
                        ],
                    },
                ]
            )

            self.assertEqual(session.messages[1]["content"], "I will search.")
            self.assertEqual(session.messages[1]["tool_calls"], [tool_call])
            self.assertNotIn("thinking", session.messages[1])
            self.assertEqual(session.messages[3]["content"], "Before after")
            self.assertEqual(
                session.messages[4]["content"][0]["text"], "Nested answer"
            )
            saved_text = (root / f"{session.id}.json").read_text(encoding="utf-8")
            self.assertNotIn("SECRET", saved_text)
            self.assertNotIn("<think>", saved_text)

            # Simulate a legacy on-disk session that predates the persistence
            # boundary. Loading must clean it before it reaches a transcript;
            # the next save must also repair the file.
            legacy = json.loads(saved_text)
            legacy["messages"][1]["content"] = (
                "<think>LEGACY SECRET</think>Visible tool-round text"
            )
            (root / f"{session.id}.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )

            loaded = store.load(session.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(
                loaded.messages[1]["content"], "Visible tool-round text"
            )
            self.assertEqual(loaded.messages[1]["tool_calls"], [tool_call])
            store.save(loaded)
            repaired = (root / f"{session.id}.json").read_text(encoding="utf-8")
            self.assertNotIn("LEGACY SECRET", repaired)
            self.assertNotIn("<think>", repaired)

    def test_create_save_list_load_and_archive_message_dicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="onion mind sessions ") as temporary:
            root = Path(temporary) / "local sessions"
            workspace = Path(temporary) / "project with spaces"
            workspace.mkdir()
            store = core.SessionStore(root)
            messages = [
                {"role": "user", "content": "Find the bug"},
                {
                    "role": "assistant",
                    "content": "I found it.",
                    "tool": {"name": "git_diff", "ok": True},
                },
            ]

            session = store.create(
                title="  Fix parser  ",
                model="deepseek-r1:8b",
                workspace=workspace,
                messages=messages,
            )
            messages[0]["content"] = "mutated outside"
            self.assertEqual(session.title, "Fix parser")
            self.assertEqual(session.workspace, str(workspace.resolve()))
            self.assertEqual(session.messages[0]["content"], "Find the bug")
            self.assertEqual([item.id for item in store.list()], [session.id])

            session.messages.append({"role": "user", "content": "Show the diff"})
            session.title = "Parser fixed"
            returned = store.save(session)
            self.assertIs(returned, session)
            loaded = store.load(session.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.title, "Parser fixed")
            self.assertEqual(loaded.model, "deepseek-r1:8b")
            self.assertEqual(len(loaded.messages), 3)
            self.assertIsInstance(loaded.messages[1], dict)

            archived = store.archive(session.id)
            self.assertIsNotNone(archived)
            assert archived is not None
            self.assertIsNotNone(archived.archived_at)
            self.assertIsNone(store.load(session.id))
            self.assertEqual(store.list(), [])
            self.assertEqual([item.id for item in store.list(archived=True)], [session.id])
            self.assertEqual(store.archive(session.id).id, session.id)  # idempotent

    def test_session_save_is_atomic_and_corruption_does_not_break_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            store = core.SessionStore(root)
            session = store.create(messages=[{"role": "user", "content": "before"}])
            session.messages[0]["content"] = "after"

            with mock.patch.object(core.os, "replace", side_effect=OSError("busy")):
                with self.assertRaises(OSError):
                    store.save(session)
            reloaded = store.load(session.id)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(reloaded.messages[0]["content"], "before")

            corrupt = root / "broken.json"
            corrupt.write_text('{"id":"broken","messages":"wrong"}', encoding="utf-8")
            listed = store.list()
            self.assertEqual([item.id for item in listed], [session.id])
            self.assertFalse(corrupt.exists())
            self.assertEqual(len(list(root.glob("broken.json.corrupt-*"))), 1)

    def test_only_new_messages_move_a_session_up_the_list(self) -> None:
        """The list sorts on updated_at and every save stamped it, so a rename
        - or the desktop re-saving whatever session you had open - jumped that
        session ahead of ones with newer conversation."""
        with tempfile.TemporaryDirectory() as temporary:
            store = core.SessionStore(temporary)
            older = store.create(
                title="Older", messages=[{"role": "user", "content": "hi"}]
            )
            newer = store.create(title="Newer")
            stamp = older.updated_at

            older.title = "Renamed"
            store.save(older)
            self.assertEqual(older.updated_at, stamp)
            self.assertEqual([s.id for s in store.list()], [newer.id, older.id])

            older.messages = [*older.messages, {"role": "user", "content": "more"}]
            store.save(older)
            self.assertGreater(older.updated_at, stamp)
            self.assertEqual([s.id for s in store.list()], [older.id, newer.id])

    def test_session_ids_cannot_escape_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = core.SessionStore(temporary)
            with self.assertRaises(ValueError):
                store.load("../settings")
            with self.assertRaises(ValueError):
                store.delete("../settings")

    def test_session_delete_permanently_removes_active_and_archived_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = core.SessionStore(temporary)
            active = store.create(title="Active")
            archived = store.create(title="Archived")
            store.archive(archived.id)

            self.assertTrue(store.delete(active.id))
            self.assertTrue(store.delete(archived.id))
            self.assertIsNone(store.load(active.id))
            self.assertIsNone(store.load(archived.id, archived=True))
            self.assertFalse(store.delete(active.id))


class WorkspaceInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="onion mind git fixture ")
        self.root = Path(self.temporary.name) / "repository with spaces"
        self.root.mkdir()
        self._git("init")
        self._git("config", "user.name", "Onionmind Tests")
        self._git("config", "user.email", "onionmind@example.invalid")
        (self.root / "AGENTS.md").write_text("# Fixture instructions\n", encoding="utf-8")
        (self.root / "source code.py").write_text("print('before')\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._git("add", "--", "AGENTS.md", "source code.py", "src/helper.py")
        self._git("commit", "-m", "fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def test_inspect_path_with_spaces_reports_git_agents_tree_and_changes(self) -> None:
        inspector = core.WorkspaceInspector(max_entries=50, max_depth=4)
        clean = inspector.inspect(self.root)
        self.assertTrue(clean.is_git)
        self.assertTrue(clean.branch)
        self.assertFalse(clean.dirty)
        self.assertEqual(clean.change_summary, "Clean")
        self.assertEqual(clean.agents_files, ("AGENTS.md",))
        self.assertIn("source code.py", clean.file_tree)
        self.assertIn("src/helper.py", clean.file_tree)

        (self.root / "source code.py").write_text("print('after')\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("untracked\n", encoding="utf-8")
        dirty = inspector.inspect(self.root)
        self.assertTrue(dirty.dirty)
        self.assertEqual(
            {change.path for change in dirty.changes},
            {"source code.py", "notes.txt"},
        )
        self.assertIn("2 changes", dirty.change_summary)
        self.assertIn("1 unstaged", dirty.change_summary)
        self.assertIn("1 untracked", dirty.change_summary)

        diff = inspector.diff(self.root, "source code.py")
        self.assertIn("print('before')", diff)
        self.assertIn("print('after')", diff)
        all_changes = inspector.diff(self.root)
        self.assertIn("Untracked file preview: notes.txt", all_changes)
        self.assertIn("untracked", all_changes)
        selected_untracked = inspector.diff(self.root, "notes.txt")
        self.assertIn("Untracked file preview: notes.txt", selected_untracked)
        self.assertIn("untracked", selected_untracked)
        with self.assertRaises(ValueError):
            inspector.diff(self.root, self.root.parent / "outside.py")

    def test_tree_is_bounded_and_selected_path_must_be_a_directory(self) -> None:
        inspector = core.WorkspaceInspector(max_entries=2, max_depth=1)
        snapshot = inspector.inspect(self.root)
        self.assertLessEqual(len(snapshot.file_tree), 2)
        self.assertTrue(snapshot.tree_truncated)

        file_path = self.root / "source code.py"
        with self.assertRaises(ValueError):
            inspector.inspect(file_path)
        with self.assertRaises(ValueError):
            inspector.inspect(self.root / "missing")

    def test_tree_does_not_descend_through_directory_links(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "private-name.txt").write_text("outside\n", encoding="utf-8")
        linked = self.root / "linked-outside"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks are unavailable for this test user")

        snapshot = core.WorkspaceInspector(max_entries=100, max_depth=4).inspect(self.root)
        self.assertNotIn("linked-outside/private-name.txt", snapshot.file_tree)

    def test_git_processes_use_argument_lists_and_hide_helper_windows(self) -> None:
        real_run = subprocess.run
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def recording_run(*args, **kwargs):
            calls.append((args, kwargs))
            return real_run(*args, **kwargs)

        with mock.patch.object(core.subprocess, "run", side_effect=recording_run):
            core.WorkspaceInspector().inspect(self.root)
            core.WorkspaceInspector().diff(self.root, "source code.py")

        self.assertTrue(calls)
        for positional, keywords in calls:
            self.assertIsInstance(positional[0], list)
            argv = positional[0]
            self.assertEqual(
                argv[1:5],
                ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"],
            )
            if "status" in argv:
                self.assertIn("--ignore-submodules=all", argv)
            if "diff" in argv:
                self.assertIn("--no-textconv", argv)
                self.assertIn("--ignore-submodules=all", argv)
            if "ls-files" in argv:
                self.assertIn("--exclude-standard", argv)
            self.assertIs(keywords.get("shell"), False)
            self.assertEqual(keywords.get("creationflags"), core._NO_WINDOW)


class HarnessAndTerminalTests(unittest.TestCase):
    def test_harness_command_preserves_model_prompt_and_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="onion mind harness ") as temporary:
            cwd = Path(temporary)
            prompt = "Fix parser.py, run its tests, and explain the diff."
            command = core.HarnessSpec().build(
                model="registry.local/deepseek-r1:8b",
                task=prompt,
                cwd=cwd,
            )
            self.assertEqual(
                command.argv,
                (
                    "ollama",
                    "launch",
                    "dsh",
                    "--model",
                    "registry.local/deepseek-r1:8b",
                    "--",
                    "--profile",
                    "headless",
                    prompt,
                ),
            )
            self.assertEqual(command.cwd, cwd.resolve())
            self.assertNotIn("--patch", command.argv)
            self.assertIn("starts in the selected working directory", core.HARNESS_LIMITATION)
            self.assertIn("web traffic goes through Tor", core.HARNESS_LIMITATION)

    def test_harness_availability_is_actionable_and_safe(self) -> None:
        spec = core.HarnessSpec()
        with mock.patch.object(core.shutil, "which", return_value=None):
            missing = spec.check()
        self.assertFalse(missing.available)
        self.assertIn("local engine is not ready", missing.reason)
        self.assertEqual(missing.limitation, core.HARNESS_LIMITATION)

        completed = subprocess.CompletedProcess(
            [r"C:\Program Files\Ollama\ollama.exe", "--version"],
            0,
            stdout=b"ollama version test",
            stderr=b"",
        )
        node_completed = subprocess.CompletedProcess(
            [r"C:\Program Files\nodejs\node.exe", "--version"],
            0,
            stdout=b"v24.6.0",
            stderr=b"",
        )
        with mock.patch.object(
            core.shutil,
            "which",
            side_effect=lambda name: (
                r"C:\Program Files\Ollama\ollama.exe"
                if name == "ollama"
                else r"C:\Program Files\nodejs\node.exe"
            ),
        ), mock.patch.object(
            core.subprocess, "run", side_effect=[completed, node_completed]
        ) as run:
            available = spec.check()
        self.assertTrue(available.available)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            positional, keywords = call
            self.assertIsInstance(positional[0], list)
            self.assertIs(keywords["shell"], False)
            self.assertEqual(keywords.get("creationflags"), core._NO_WINDOW)

        old_node = subprocess.CompletedProcess(
            ["node", "--version"], 0, stdout=b"v20.18.0", stderr=b""
        )
        with mock.patch.object(
            core.shutil, "which", side_effect=lambda name: name
        ), mock.patch.object(
            core.subprocess, "run", side_effect=[completed, old_node]
        ):
            unsupported = spec.check()
        self.assertFalse(unsupported.available)
        self.assertIn("newer local runtime", unsupported.reason)

    def test_terminal_parser_builds_argv_without_shell_true(self) -> None:
        if os.name == "nt":
            direct = core.parse_terminal_command(
                r'git -C "C:\repository with spaces" status'
            )
            self.assertEqual(
                direct,
                ("git", "-C", r"C:\repository with spaces", "status"),
            )
        else:
            direct = core.parse_terminal_command('git -C "/repository with spaces" status')
            self.assertEqual(direct, ("git", "-C", "/repository with spaces", "status"))

        powershell_text = "$env:ONIONMIND_TEST='yes'; Get-ChildItem"
        powershell = core.parse_terminal_command(
            powershell_text, interpreter="powershell"
        )
        self.assertIn(powershell[0], {"powershell.exe", "pwsh"})
        self.assertEqual(powershell[-2], "-Command")
        self.assertTrue(powershell[-1].endswith("; " + powershell_text))
        if os.name == "nt":
            # Non-ASCII output survives the trip into the terminal pane.
            done = subprocess.run(core.parse_terminal_command('"æøå üñí"', interpreter="powershell"),
                                  capture_output=True, timeout=60)
            self.assertEqual(done.stdout.decode("utf-8").strip(), "æøå üñí")

        cmd_text = "dir && echo ready"
        self.assertEqual(
            core.parse_terminal_command(cmd_text, interpreter="cmd"),
            ("cmd.exe", "/d", "/s", "/c", cmd_text),
        )

        shell_text = "printf ready | tr a-z A-Z"
        self.assertEqual(
            core.parse_terminal_command(shell_text, interpreter="sh"),
            ("/bin/sh", "-c", shell_text),
        )

        source = inspect.getsource(core)
        self.assertNotIn("CREATE_NEW_CONSOLE", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("PySide", source)


class UpdateFeedTests(unittest.TestCase):
    """The Tor-routed self-update path: manifest validation, staging, swap."""

    def _manifest_dict(self, **overrides):
        payload = {
            "revision": "2301ddffb6978cb07e495a0bb2b98a0e85583f8b",
            "version": "1.0.0",
            "asset": "Onionmind-Windows-x64.zip",
            "asset_url": "https://github.com/Codemaster64/onionmind/releases/download/desktop-latest/Onionmind-Windows-x64.zip",
            "size": 1024,
            "sha256": "a" * 64,
        }
        payload.update(overrides)
        return payload

    def test_manifest_round_trip_and_short_revision(self):
        manifest = core.parse_update_manifest(json.dumps(self._manifest_dict()))
        self.assertEqual(manifest.revision, self._manifest_dict()["revision"])
        self.assertEqual(core.short_revision(manifest.revision), "2301ddf")
        self.assertEqual(core.short_revision(None), "unknown")
        self.assertEqual(core.short_revision("2301ddf-dirty"), "2301ddf-dirt")

    def test_manifest_rejects_tampering(self):
        cases = [
            self._manifest_dict(revision="not-a-sha"),
            self._manifest_dict(revision=123),
            self._manifest_dict(asset="../evil.zip"),
            self._manifest_dict(asset="bundle/inner.zip"),
            self._manifest_dict(asset_url="http://github.com/Onionmind-Windows-x64.zip"),
            self._manifest_dict(asset_url="https://evil.example.com/Onionmind-Windows-x64.zip"),
            self._manifest_dict(size=0),
            self._manifest_dict(size="1024"),
            self._manifest_dict(sha256="A" * 64),
            self._manifest_dict(sha256="short"),
        ]
        for payload in cases:
            with self.assertRaises(core.BundleUpdateError):
                core.parse_update_manifest(json.dumps(payload))
        with self.assertRaises(core.BundleUpdateError):
            core.parse_update_manifest("not json at all")

    def test_update_state_is_the_honest_tri_state(self):
        manifest = core.parse_update_manifest(json.dumps(self._manifest_dict()))
        self.assertEqual(core.update_state(manifest.revision, manifest), "current")
        self.assertEqual(
            core.update_state("1111111111111111111111111111111111111111", manifest), "available"
        )
        self.assertEqual(core.update_state(None, manifest), "development")
        self.assertEqual(core.update_state("2301ddf", None), "unavailable")

    def test_installed_revision_reads_the_marker_honestly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(core.installed_revision(root))
            (root / core.UPDATE_REVISION_FILENAME).write_text("\n")
            self.assertIsNone(core.installed_revision(root))
            (root / core.UPDATE_REVISION_FILENAME).write_text("abc1234\n")
            self.assertEqual(core.installed_revision(root), "abc1234")


class BundleUpdaterTests(unittest.TestCase):
    def _updater(self, root: Path) -> core.BundleUpdater:
        return core.BundleUpdater(
            root / "bundle",
            root / "work",
            lambda port: {"http": f"socks5h://x:{port}", "https": f"socks5h://x:{port}"},
            "Onionmind-test-agent",
        )

    def _manifest(self, revision="2301ddffb6978cb07e495a0bb2b98a0e85583f8b") -> core.UpdateManifest:
        return core.parse_update_manifest(
            json.dumps(
                {
                    "revision": revision,
                    "version": "1.0.0",
                    "asset": "Onionmind-Windows-x64.zip",
                    "asset_url": "https://github.com/Codemaster64/onionmind/releases/download/desktop-latest/Onionmind-Windows-x64.zip",
                    "size": 1024,
                    "sha256": "a" * 64,
                }
            )
        )

    def _bundle_zip(self, destination: Path, revision: str, slip_member: str | None = None) -> Path:
        with tempfile.TemporaryDirectory() as source:
            inner = Path(source)
            (inner / "Onionmind.exe").write_bytes(b"MZ fake")
            (inner / core.UPDATE_REVISION_FILENAME).write_text(revision + "\n")
            with zipfile.ZipFile(destination, "w") as archive:
                for path in sorted(inner.rglob("*")):
                    archive.write(path, path.relative_to(inner).as_posix())
                if slip_member:
                    archive.writestr(slip_member, "escaped")
        return destination

    def test_stage_verifies_revision_and_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self._updater(root)
            manifest = self._manifest()
            archive = self._bundle_zip(root / "bundle.zip", manifest.revision)
            staging = updater.stage(manifest, archive)
            self.assertTrue((staging / "Onionmind.exe").is_file())
            self.assertEqual(core.installed_revision(staging), manifest.revision)

            wrong = self._bundle_zip(root / "wrong.zip", "1111111111111111111111111111111111111111")
            with self.assertRaises(core.BundleUpdateError):
                updater.stage(manifest, wrong)

    def test_stage_refuses_zip_slip_and_bare_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self._updater(root)
            manifest = self._manifest()
            slipped = self._bundle_zip(
                root / "slip.zip", manifest.revision, slip_member="../escape.txt"
            )
            with self.assertRaises(core.BundleUpdateError):
                updater.stage(manifest, slipped)
            self.assertFalse((root / "escape.txt").exists())

            with zipfile.ZipFile(root / "empty.zip", "w") as archive:
                archive.writestr("readme.txt", "no executable here")
            with self.assertRaises(core.BundleUpdateError):
                updater.stage(manifest, root / "empty.zip")

    def test_download_verifies_size_and_digest(self):
        payload = bytes(range(256)) * 4

        class FakeResponse:
            status_code = 200
            text = ""

            def iter_content(self, chunk_size):
                for start in range(0, len(payload), chunk_size):
                    yield payload[start : start + chunk_size]

        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict]] = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeResponse()

        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self._updater(root)
            base = self._manifest()
            manifest = core.UpdateManifest(
                revision=base.revision,
                version=base.version,
                asset_name=base.asset_name,
                asset_url=base.asset_url,
                size=len(payload),
                sha256=digest,
            )
            session = FakeSession()
            updater._session = session
            notes: list[tuple[object, str]] = []
            downloaded = updater.download(9150, manifest, progress=lambda f, n: notes.append((f, n)))
            self.assertEqual(downloaded.read_bytes(), payload)
            self.assertTrue(notes)
            method, url, kwargs = session.calls[0]
            self.assertEqual(method, "GET")
            self.assertEqual(url, manifest.asset_url)
            self.assertTrue(kwargs["proxies"]["https"].startswith("socks5h://"))

            tampered = core.UpdateManifest(
                revision=base.revision,
                version=base.version,
                asset_name=base.asset_name,
                asset_url=base.asset_url,
                size=len(payload),
                sha256="0" * 64,
            )
            with self.assertRaises(core.BundleUpdateError):
                updater.download(9150, tampered)

            truncated = core.UpdateManifest(
                revision=base.revision,
                version=base.version,
                asset_name=base.asset_name,
                asset_url=base.asset_url,
                size=len(payload) + 1,
                sha256=digest,
            )
            with self.assertRaises(core.BundleUpdateError):
                updater.download(9150, truncated)

    def test_fetch_manifest_without_a_proxy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            updater = core.BundleUpdater(
                Path(temporary),
                Path(temporary) / "work",
                lambda port: {},
                "Onionmind-test-agent",
            )
            with self.assertRaises(core.BundleUpdateError):
                updater.fetch_manifest(9150)

    def test_apply_script_carries_paths_and_safety(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self._updater(root)
            staging = root / "work" / "staging-2301ddffb697"
            staging.mkdir(parents=True)
            command = updater.apply_command(staging)
            self.assertEqual(command[0], "powershell.exe")
            self.assertIn("-ExecutionPolicy", command)
            # Paths ride on the command line, so the script stays generic.
            self.assertIn(str(root / "bundle"), command)
            self.assertIn(str(staging), command)
            self.assertIn(str(root / "work" / "apply.log"), command)
            script = Path(command[command.index("-File") + 1])
            self.assertTrue(script.is_file())
            body = script.read_text(encoding="utf-8-sig")
            self.assertIn("$ParentPid", body)
            self.assertIn("$InstallDir", body)
            self.assertIn(core.UPDATE_REVISION_FILENAME, body)
            self.assertNotIn("@MARKER@", body)
            self.assertNotIn("@EXE_NAME@", body)
            self.assertIn("backup-before-", body)
            self.assertIn("throw", body)

    def test_pending_and_prune_keep_only_live_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            updater = self._updater(root)
            manifest = self._manifest()
            staging = updater.stage(manifest, self._bundle_zip(root / "b.zip", manifest.revision))
            self.assertEqual(core.pending_staging_dir(work), staging)

            empty = work / "staging-00000000000"
            empty.mkdir()
            self.assertEqual(core.pending_staging_dir(work), staging)

            # A build staged before the running bundle was installed is older:
            # offering it as an update would downgrade (seen: a broken build
            # left staged by an install step that never ran).
            install = root / "installed"
            install.mkdir()
            marker = install / core.UPDATE_REVISION_FILENAME
            marker.write_text("f" * 40, encoding="utf-8")
            later = staging.stat().st_mtime + 60
            os.utime(marker, (later, later))
            self.assertIsNone(core.pending_staging_dir(work, install))
            os.utime(marker, (later - 120, later - 120))
            self.assertEqual(core.pending_staging_dir(work, install), staging)
            marker.write_text(manifest.revision, encoding="utf-8")            # already running it
            os.utime(marker, (later - 120, later - 120))
            self.assertIsNone(core.pending_staging_dir(work, install))

            core.prune_update_workdir(work, running_revision=manifest.revision)
            self.assertFalse(staging.exists())
            self.assertTrue(empty.exists())
            self.assertEqual(core.pending_staging_dir(work), None)


class WorkbenchPreferencesTests(unittest.TestCase):
    def test_empty_settings_yield_the_shipped_defaults(self) -> None:
        self.assertEqual(core.load_preferences({}), dict(core.PREFERENCE_DEFAULTS))

    def test_valid_values_survive_and_junk_falls_back(self) -> None:
        preferences = core.load_preferences(
            {
                "text_scale": "comfortable",
                "enter_sends": False,
                "show_terminal_on_launch": True,
                "startup_mode": "chat",
                "reduce_motion": "reduced",
            }
        )
        self.assertEqual(preferences["text_scale"], "comfortable")
        self.assertFalse(preferences["enter_sends"])
        self.assertTrue(preferences["show_terminal_on_launch"])
        self.assertEqual(preferences["startup_mode"], "chat")
        self.assertEqual(preferences["reduce_motion"], "reduced")

        junk = core.load_preferences(
            {
                "text_scale": "enormous",
                "startup_mode": 7,
                "reduce_motion": None,
                "unknown_knob": "ignored",
            }
        )
        self.assertEqual(junk["text_scale"], "system")
        self.assertEqual(junk["startup_mode"], "remember")
        self.assertEqual(junk["reduce_motion"], "system")
        self.assertNotIn("unknown_knob", junk)

    def test_startup_mode_resolution(self) -> None:
        self.assertEqual(core.resolve_startup_mode("agent", "chat"), "agent")
        self.assertEqual(core.resolve_startup_mode("remember", "chat"), "chat")
        self.assertEqual(core.resolve_startup_mode("remember", ""), "agent")
        self.assertEqual(core.resolve_startup_mode("remember", "bogus"), "agent")
        self.assertEqual(core.resolve_startup_mode("bogus", "chat"), "chat")

    def test_scale_factors_and_motion_resolution(self) -> None:
        self.assertEqual(core.text_scale_factor("system"), 1.0)
        self.assertEqual(core.text_scale_factor("compact"), 0.9)
        self.assertEqual(core.text_scale_factor("comfortable"), 1.15)
        self.assertEqual(core.text_scale_factor("unheard-of"), 1.0)
        self.assertFalse(core.animations_enabled("reduced", True))
        self.assertTrue(core.animations_enabled("full", False))
        self.assertTrue(core.animations_enabled("system", True))
        self.assertFalse(core.animations_enabled("system", False))


class ModelServerStartPlanTests(unittest.TestCase):
    def which(self, *found):
        return lambda name: f"/usr/bin/{name}" if name in found else None

    def test_linux_system_service_is_the_only_way_offered(self) -> None:
        plan = core.model_server_start_plan("linux", self.which("systemctl", "ollama"),
                                            unit_exists=lambda scope: scope == ())
        self.assertEqual([w.argv for w in plan], [("systemctl", "start", "ollama.service")])
        self.assertEqual(plan[0].manual, "sudo systemctl start ollama")

    def test_linux_user_service_wins_over_the_system_one(self) -> None:
        plan = core.model_server_start_plan("linux", self.which("systemctl", "ollama"),
                                            unit_exists=lambda scope: True)
        self.assertEqual(plan[0].argv, ("systemctl", "--user", "start", "ollama.service"))

    def test_linux_without_a_service_falls_back_to_serve(self) -> None:
        plan = core.model_server_start_plan("linux", self.which("systemctl", "ollama"),
                                            unit_exists=lambda scope: False)
        self.assertEqual([w.argv for w in plan], [("/usr/bin/ollama", "serve")])
        self.assertEqual(core.model_server_start_plan("linux", self.which(), unit_exists=lambda s: False), [])

    def test_this_platform_with_nothing_installed_offers_nothing(self) -> None:
        self.assertEqual(core.model_server_start_plan(which=lambda name: None, exists=lambda p: False,
                                                      unit_exists=lambda scope: False), [])

    def test_mac_opens_the_app_before_serve(self) -> None:
        plan = core.model_server_start_plan("darwin", self.which("ollama"),
                                            exists=lambda p: p == "/Applications/Ollama.app")
        self.assertEqual(plan[0].argv, ("open", "-g", "-a", "/Applications/Ollama.app"))
        self.assertEqual(plan[1].argv, ("/usr/bin/ollama", "serve"))

    def test_windows_starts_the_tray_app_before_serve(self) -> None:
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/u/AppData/Local"}):
            plan = core.model_server_start_plan("win32", self.which("ollama"), exists=lambda p: True)
        self.assertTrue(plan[0].argv[0].endswith("ollama app.exe"))
        self.assertFalse(plan[0].waits)


class ModelDiscoveryTests(unittest.TestCase):
    def test_markers_are_found_by_name_and_tags(self) -> None:
        self.assertEqual(
            core.uncensored_marker("Qwen3.5-9B-abliterated-GGUF"), "abliterated"
        )
        self.assertEqual(core.uncensored_marker("plain-name", ["gguf", "uncensored"]), "uncensored")
        self.assertIsNone(core.uncensored_marker("Qwen3.5-4B", ["gguf", "chat"]))
        # Absent markers say nothing; the UI must present it that way.
        self.assertEqual(core.UNCENSORED_MARKERS[0], "abliterated")

    def test_quant_tokens_come_out_of_filenames(self) -> None:
        self.assertEqual(core.quant_from_filename("model-Q4_K_M.gguf"), "Q4_K_M")
        self.assertEqual(core.quant_from_filename("IQ2_M-prefix"), "IQ2_M")
        self.assertEqual(core.quant_from_filename("plainname.gguf"), None)

    def test_references_normalize_to_pull_names(self) -> None:
        cases = {
            "llama3.2:3b": "llama3.2:3b",
            "BLAZE": "BLAZE",
            "hf.co/user/model-gguf": "hf.co/user/model-gguf",
            "https://huggingface.co/user/model-gguf": "hf.co/user/model-gguf",
            "https://huggingface.co/user/model-gguf/blob/main/file.Q5_K_M.gguf": "hf.co/user/model-gguf:Q5_K_M",
            "https://huggingface.co/user/model-gguf/resolve/main/file.Q8_0.gguf": "hf.co/user/model-gguf:Q8_0",
            "user/model-gguf": "hf.co/user/model-gguf",
            "https://example.com/other.gguf": "https://example.com/other.gguf",
            "  ": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(core.normalize_model_reference(raw), expected, raw)

    def test_catalog_rows_parse_flags_and_counts(self) -> None:
        payload = [
            {"id": "user/hot-gguf", "downloads": 1_234_000, "likes": 42, "tags": ["gguf"]},
            {"id": "user/abliterated-llama", "downloads": 90, "likes": 1, "tags": ["gguf", "uncensored"]},
            {"downloads": 5},
            "junk",
        ]
        rows = core.parse_hf_catalog(payload)
        self.assertEqual([row.id for row in rows], ["user/hot-gguf", "user/abliterated-llama"])
        self.assertTrue(rows[0].gguf)
        # The id's marker wins over a tag's when both are present.
        self.assertEqual(rows[1].uncensored, "abliterated")
        self.assertEqual(core.format_downloads(1_234_000), "1.2M")
        self.assertEqual(core.format_downloads(90), "90")
        self.assertEqual(core.parse_hf_catalog({"not": "a list"}), [])

    def test_catalog_fetch_goes_through_tor_and_never_directly(self) -> None:
        seen: dict[str, object] = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json() -> list[dict]:
                return [{"id": "user/model-gguf", "downloads": 3, "tags": ["gguf"]}]

        class _Session:
            def get(self, url: str, **kwargs: object) -> "_Response":
                seen["url"] = url
                seen["kwargs"] = kwargs
                return _Response()

        rows = core.fetch_hf_catalog(9150, session=_Session())
        self.assertEqual([row.id for row in rows], ["user/model-gguf"])
        self.assertEqual(seen["url"], core.HF_CATALOG_URL)
        kwargs = seen["kwargs"]
        self.assertEqual(kwargs["proxies"], {  # type: ignore[index]
            "http": "socks5h://127.0.0.1:9150",
            "https": "socks5h://127.0.0.1:9150",
        })
        # socks5h keeps hostname resolution inside Tor; a socks5:// URL would
        # leak the DNS question to the local resolver.
        self.assertIn("socks5h://", str(kwargs["proxies"]))  # type: ignore[index]

    def test_parameter_counts_come_out_of_model_names(self) -> None:
        self.assertEqual(core.parameter_billions("Qwen2.5-7B-Instruct-GGUF"), 7.0)
        self.assertEqual(core.parameter_billions("Llama-3.2-1B"), 1.0)  # 3.2 is a version
        self.assertEqual(core.parameter_billions("model-0.5B-q4"), 0.5)
        self.assertIsNone(core.parameter_billions("user/tinystories-gguf/blob/main"))
        self.assertIsNone(core.parameter_billions(""))

    def test_context_window_accepts_any_count_and_warns_at_the_edges(self) -> None:
        """The chat context is typed, not chosen from a fence: anything that
        parses is kept, and only the ranges that go wrong are called out."""
        self.assertEqual(core.parse_context_window("24k"), 24576)
        self.assertEqual(core.parse_context_window("21,000 tokens"), 21000)
        self.assertEqual(core.parse_context_window(21000), 21000)
        for junk in ("", "plenty", "-5", 0, True, None):
            self.assertIsNone(core.parse_context_window(junk), junk)

        # A stored preference survives whatever shape it was written in.
        self.assertEqual(core.load_preferences({"context_window": "8k"})["context_window"], 8192)
        self.assertEqual(core.load_preferences({"context_window": 21000})["context_window"], 21000)
        self.assertEqual(
            core.load_preferences({"context_window": "plenty"})["context_window"],
            core.PREFERENCE_DEFAULTS["context_window"],
        )

        self.assertEqual(core.context_window_warning(16384), "")
        self.assertIn("Below 1k", core.context_window_warning(512))
        self.assertIn("KV cache", core.context_window_warning(65536))
        self.assertIn("128k", core.context_window_warning(200000))

    def test_catalog_rows_carry_a_short_description(self) -> None:
        """The list API has no prose description, so the row composes one from
        the task, the parameter count, the fit, and the download count."""
        rows = core.parse_hf_catalog(
            [
                {
                    "id": "user/Qwen2.5-7B-Instruct-GGUF",
                    "downloads": 1_200_000,
                    "likes": 30,
                    "pipeline_tag": "text-generation",
                    "tags": ["gguf"],
                }
            ]
        )
        self.assertEqual(rows[0].task, "text-generation")
        described = core.catalog_description(
            rows[0], core.catalog_fit(rows[0].id, 12_288, None)[1]
        )
        self.assertIn("Text generation", described)
        self.assertIn("7B parameters", described)
        self.assertIn("fits this machine", described)
        self.assertIn("1.2M downloads", described)
        # A row without a task or a size in its name still describes itself.
        bare = core.catalog_description(core.CatalogModel(id="user/mystery"))
        self.assertEqual(bare, "Local model · 0 downloads")

    def test_catalog_fit_ranks_against_this_machine(self) -> None:
        fits = core.catalog_fit("user/model-7B-gguf", 12_288, None)
        self.assertEqual(fits[0], 0)  # ~4.2GB at Q4 fits 12GB VRAM
        self.assertIn("fits this machine's VRAM", fits[1])
        beyond = core.catalog_fit("user/model-70B-gguf", 12_288, None)
        self.assertEqual(beyond[0], 2)
        self.assertIn("beyond this machine's VRAM", beyond[1])
        on_ram = core.catalog_fit("user/model-8B-gguf", None, 8_192)
        self.assertEqual(on_ram[0], 0)  # ~4.8GB at Q4 against a ~6.8GB RAM budget
        self.assertIn("fits this machine's RAM", on_ram[1])
        too_big_for_ram = core.catalog_fit("user/model-27B-gguf", None, 8_192)
        self.assertEqual(too_big_for_ram[0], 2)
        unknown = core.catalog_fit("user/tinystories-gguf", 8_192, 32_768)
        self.assertEqual(unknown[0], 1)
        self.assertEqual(unknown[1], "size unknown")

    def test_machine_specs_report_or_stay_silent(self) -> None:
        ram = core.machine_memory_mb()
        self.assertTrue(ram is None or ram > 0)
        vram = core.gpu_vram_mb()
        self.assertTrue(vram is None or vram > 0)


class GpuMemoryProbeTests(unittest.TestCase):
    """Each vendor probe against canned tool output or a fake sysfs tree."""

    def probe(self, outputs: dict[str, str]):
        return mock.patch.object(hardware, "_run_probe",
                                 side_effect=lambda argv: outputs.get(" ".join(argv[:2])))

    def test_nvidia_is_parsed_with_its_name(self) -> None:
        with self.probe({"nvidia-smi --query-gpu=memory.used,memory.total,name":
                         "1200, 4096, NVIDIA GeForce GTX 1650\n"}):
            self.assertEqual(core._nvidia_memory(), (1200, 4096, "NVIDIA GeForce GTX 1650"))

    def test_amd_reads_amdgpu_sysfs_and_skips_small_carveouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for card, vendor, total, used in (("card0", "0x1002", 512, 100),
                                              ("card1", "0x1002", 16_368, 2_048),
                                              ("card2", "0x10de", 24_000, 0)):
                device = root / card / "device"
                device.mkdir(parents=True)
                (device / "vendor").write_text(vendor + "\n")
                (device / "mem_info_vram_total").write_text(str(total * 1024 * 1024))
                (device / "mem_info_vram_used").write_text(str(used * 1024 * 1024))
            (root / "card1" / "device" / "product_name").write_text("Radeon RX 7800 XT\n")
            with mock.patch.object(hardware, "_DRM_ROOT", root):
                self.assertEqual(core._amd_memory(), (2_048, 16_368, "Radeon RX 7800 XT"))

    def test_intel_reads_xpu_smi_json(self) -> None:
        discovery = json.dumps({"device_name": "Intel(R) Arc(TM) A770 Graphics",
                                "memory_physical_size_byte": str(16 * 1024 ** 3)})
        stats = json.dumps({"device_level": [{"metrics_type": "XPUM_STATS_MEMORY_USED", "value": 900}]})
        with self.probe({"xpu-smi discovery": discovery, "xpu-smi stats": stats}):
            self.assertEqual(core._intel_memory(), (900, 16_384, "Intel(R) Arc(TM) A770 Graphics"))

    def test_apple_reports_the_metal_share_of_unified_memory(self) -> None:
        uname = mock.Mock(sysname="Darwin", machine="arm64")
        with mock.patch.object(core.os, "uname", return_value=uname, create=True):
            with self.probe({"sysctl -n": str(32 * 1024 ** 3)}):
                # 32 GB machine: 2/3 of RAM, no used figure of its own.
                self.assertEqual(core._apple_memory(), (None, 21_845, "Apple GPU (unified memory)"))

    def test_windows_dxgi_picks_the_real_gpu(self) -> None:
        basic = {"name": "Microsoft Basic Render Driver", "vendor": 0x1414, "dedicated_mb": 0,
                 "shared_mb": 8_000, "software": True, "used_mb": None, "budget_mb": None}
        radeon = {"name": "AMD Radeon RX 6600", "vendor": 0x1002, "dedicated_mb": 8_176,
                  "shared_mb": 8_000, "software": False, "used_mb": 1_234, "budget_mb": 7_600}
        iris = {"name": "Intel(R) Iris(R) Xe Graphics", "vendor": 0x8086, "dedicated_mb": 128,
                "shared_mb": 7_900, "software": False, "used_mb": 300, "budget_mb": 7_000}
        # A discrete card reports its own memory and its real use.
        self.assertEqual(core._pick_dxgi_adapter([basic, iris, radeon]), (1_234, 8_176, "AMD Radeon RX 6600"))
        # An integrated GPU borrows RAM: the shared size, no "used" of its own.
        self.assertEqual(core._pick_dxgi_adapter([basic, iris]),
                         (None, 8_028, "Intel(R) Iris(R) Xe Graphics (shared memory)"))
        # The software rasterizer alone (a VM, a CI runner) is no GPU - also
        # without the software flag, as Windows CI really reports it.
        self.assertIsNone(core._pick_dxgi_adapter([basic]))
        self.assertIsNone(core._pick_dxgi_adapter([dict(basic, software=False)]))
        self.assertIsNone(core._pick_dxgi_adapter([]))

    @unittest.skipUnless(os.name == "nt", "DXGI exists only on Windows")
    def test_dxgi_runs_for_real_on_windows(self) -> None:
        # Calls into dxgi.dll through ctypes: every adapter comes back with its
        # fields, and nothing crashes on a runner with only the software one.
        adapters = core._dxgi_adapters()
        self.assertIsInstance(adapters, list)
        self.assertTrue(adapters, "Windows always lists at least one adapter")
        for adapter in adapters:
            self.assertIn("dedicated_mb", adapter)
            self.assertIsInstance(adapter["software"], bool)
        print("DXGI adapters:", adapters)
        if all(a["vendor"] == 0x1414 for a in adapters):   # a runner: no GPU at all
            self.assertIsNone(core._pick_dxgi_adapter(adapters))

    def test_off_windows_dxgi_reports_nothing(self) -> None:
        if os.name != "nt":
            self.assertEqual(core._dxgi_adapters(), [])

    def test_first_vendor_found_wins_and_absence_is_none(self) -> None:
        # Intel is probed on every platform (AMD and Apple only off Windows).
        with mock.patch.object(hardware, "_nvidia_memory", return_value=None), \
             mock.patch.object(hardware, "_amd_memory", return_value=None), \
             mock.patch.object(hardware, "_intel_memory", return_value=(1, 8_192, "Intel GPU")), \
             mock.patch.object(hardware, "_apple_memory", return_value=None):
            self.assertEqual(core.gpu_memory_mb(), (1, 8_192))
            self.assertEqual(core.gpu_vram_mb(), 8_192)
            self.assertEqual(core.gpu_name(), "Intel GPU")
        with mock.patch.object(hardware, "_gpu_memory", return_value=None):
            self.assertEqual(core.gpu_memory_mb(), (None, None))
            self.assertIsNone(core.gpu_name())


class AgentContextUsageTests(unittest.TestCase):
    def test_request_and_answer(self) -> None:
        request = json.loads(core.agent_context_usage_message())
        self.assertEqual(request["request"]["subtype"], "get_context_usage")
        line = json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"], "response": {
                "subtype": "get_context_usage", "totalTokens": 9000, "contextWindowSize": 32768,
                "isEstimated": True, "breakdown": {"systemPrompt": 4000, "builtinTools": 2500,
                                                   "mcpTools": 300, "skills": 200, "memoryFiles": 900}}}})
        event = core.parse_agent_line(line)
        self.assertEqual(event["request_id"], core.AGENT_CONTEXT_REQUEST)
        self.assertEqual(core.agent_context_usage(event["payload"]),
                         {"used": 9000, "window": 32768, "overhead": 7000, "estimated": True})
        self.assertIsNone(core.agent_context_usage({"subtype": "other"}))
        self.assertIsNone(core.agent_context_usage({"subtype": "get_context_usage"}))


FIXTURES = Path(__file__).resolve().parent


class WorkbenchV2CoreTests(unittest.TestCase):
    def test_every_flag_defaults_off_except_web_ask_and_junk_falls_back(self) -> None:
        self.assertEqual(len(core.FEATURE_FLAGS), 24)
        defaults = core.load_features({})
        self.assertEqual({k for k, on in defaults.items() if on}, {"webAsk"})
        loaded = core.load_features({"features": {"palette": True, "webAsk": False,
                                                  "lock": "yes", "bogus": True}})
        self.assertTrue(loaded["palette"])
        self.assertFalse(loaded["webAsk"])
        self.assertFalse(loaded["lock"])           # a non-bool is junk, not truthy
        self.assertNotIn("bogus", loaded)
        self.assertEqual(core.load_features({"features": "nope"}), defaults)

    def test_web_ask_matches_web_shaped_prompts_and_trims_the_query(self) -> None:
        self.assertTrue(core.needs_web("Is there a newer minitest release?"))
        self.assertTrue(core.needs_web("look it up please"))
        self.assertFalse(core.needs_web("Explain how this project is structured"))
        self.assertEqual(core.web_query("Is there a newer minitest release?"),
                         "a newer minitest release")
        self.assertLessEqual(len(core.web_query("what is " + "x" * 200)), 70)

    def test_presets_keep_ids_and_apply_edits_and_system_prompt_is_single(self) -> None:
        presets = core.load_presets({"preset_texts": {"concise": "Be brief.", "review": "  "}})
        self.assertEqual([p["id"] for p in presets], ["default", "review", "concise", "teach"])
        self.assertEqual(presets[2]["text"], "Be brief.")
        self.assertTrue(presets[1]["text"].startswith("Review code"))  # blank edit ignored
        self.assertEqual(core.active_preset({"preset": "teach"})["name"], "Explain step by step")
        self.assertEqual(core.active_preset({"preset": "missing"})["id"], "default")
        messages = core.with_system_prompt(
            [{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}], "new")
        self.assertEqual(messages, [{"role": "system", "content": "new"},
                                    {"role": "user", "content": "hi"}])

    def test_rules_are_per_project_and_decide_requests_deny_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings: dict = {}
            core.store_rules(settings, tmp, {"editTests": True, "readOnly": False,
                                             "blocked": ".env, secrets/"})
            rules = core.load_rules(settings, tmp + os.sep)   # same folder, other spelling
            self.assertTrue(rules["editTests"])
            self.assertEqual(core.load_rules(settings, tmp + "-other"), core.RULE_DEFAULTS)
            write = lambda rel: {"file_path": os.path.join(tmp, rel), "content": "x"}
            self.assertEqual(core.rule_decision(rules, "write_file", write("tests/a.py"), tmp)[0], "allow")
            self.assertEqual(core.rule_decision(rules, "write_file", write("src/a.py"), tmp)[0], "ask")
            self.assertEqual(core.rule_decision(rules, "edit", write(".env"), tmp)[0], "deny")
            self.assertEqual(core.rule_decision(rules, "edit", write("secrets/k.pem"), tmp)[0], "deny")
            self.assertEqual(core.rule_decision(rules, "read_file", write("config/.env"), tmp)[0], "deny")
            self.assertEqual(core.rule_decision(rules, "run_shell_command",
                                                {"command": "python -m pytest -q"}, tmp)[0], "allow")
            self.assertEqual(core.rule_decision(rules, "run_shell_command",
                                                {"command": "cat .env"}, tmp)[0], "deny")
            self.assertEqual(core.rule_decision(rules, "run_shell_command",
                                                {"command": "rm -rf build"}, tmp)[0], "ask")
            read_only = dict(rules, readOnly=True)
            self.assertEqual(core.rule_decision(read_only, "write_file", write("tests/a.py"), tmp)[0], "deny")

    def test_agent_paths_are_made_relative_on_any_platform(self) -> None:
        self.assertEqual(core.describe_tool_request("write_file", {"file_path": "/work/demo/a/b.py"}, "/work/demo"),
                         "edit a/b.py")
        self.assertEqual(core.describe_tool_request("edit", {"file_path": "C:\\proj\\src\\x.py"}, "C:\\proj"),
                         "edit src/x.py")
        self.assertEqual(core.describe_tool_request("edit", {"file_path": "/elsewhere/x.py"}, "/work/demo"),
                         "edit /elsewhere/x.py")
        self.assertEqual(core.describe_tool_request("edit", {"file_path": "/work/demo-other/x.py"}, "/work/demo"),
                         "edit /work/demo-other/x.py")

    def test_blocked_paths_become_agent_deny_rules_for_reads_and_edits(self) -> None:
        denials = core.agent_rule_denials({"blocked": ".env, config/credentials.yml.enc, ./x/"})
        self.assertIn("read_file(/.env)", denials)
        self.assertIn("edit(/**/.env)", denials)
        self.assertIn("edit(/config/credentials.yml.enc)", denials)
        self.assertNotIn("read_file(/**/config/credentials.yml.enc)", denials)
        self.assertIn("read_file(/x)", denials)
        self.assertEqual(core.agent_rule_denials({"blocked": "bad(name)"}), [])

    def test_real_qwen_stream_lines_parse_into_typed_events(self) -> None:
        lines = (FIXTURES / "qwen-stream-sample.jsonl").read_text(encoding="utf-8").splitlines()
        events = [core.parse_agent_line(line) for line in lines]
        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds, ["text", "system", "batch", "permission", "batch", "batch", "result"])
        tool = events[2]["events"][0]
        self.assertEqual((tool["kind"], tool["tool"]), ("tool_use", "write_file"))
        permission = events[3]
        self.assertEqual(permission["tool"], "write_file")
        self.assertEqual(permission["input"]["file_path"], "/work/demo/hello.txt")
        self.assertEqual(core.describe_tool_request("write_file", permission["input"], "/work/demo"),
                         "edit hello.txt")
        self.assertEqual(core.edit_size("write_file", permission["input"]), (1, 0))
        result = events[4]["events"][0]
        self.assertEqual((result["kind"], result["error"]), ("tool_result", False))
        self.assertEqual(events[5]["events"], [{"kind": "say", "text": "Done."}])
        self.assertTrue(events[6]["ok"])
        allow = json.loads(core.agent_permission_response(permission["request_id"], True, permission["input"]))
        self.assertEqual(allow["response"]["request_id"], permission["request_id"])
        self.assertEqual(allow["response"]["response"]["behavior"], "allow")
        deny = json.loads(core.agent_permission_response("r", False, message="no"))
        self.assertEqual(deny["response"]["response"], {"behavior": "deny", "message": "no"})
        self.assertEqual(json.loads(core.agent_initialize_message())["request"]["subtype"], "initialize")
        self.assertEqual(json.loads(core.agent_task_message("do it"))["message"]["content"], "do it")

    def test_agent_reasoning_never_surfaces_as_said_text(self) -> None:
        line = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "<think>private plan</think>Visible."}]}})
        self.assertEqual(core.parse_agent_line(line)["events"], [{"kind": "say", "text": "Visible."}])

    def test_token_estimate_and_labels(self) -> None:
        self.assertEqual(core.estimate_tokens("abcd" * 10), 10)
        self.assertEqual(core.estimate_tokens([{"content": "abcd"}, "abcd"]), 2)
        self.assertEqual(core.format_tokens(3140), "3.1k")
        self.assertEqual(core.format_tokens(32768), "32k")
        self.assertEqual(core.format_tokens(16384), "16k")
        self.assertEqual(core.format_tokens(512), "512")

    def test_errors_classify_from_the_engine_own_messages(self) -> None:
        self.assertEqual(core.classify_error("Ollama is not running on 127.0.0.1:11434. Start it, then retry."), "server")
        self.assertEqual(core.classify_error("No model server on 11434 (ollama) or 8080 (llama-server). Start one."), "server")
        self.assertEqual(core.classify_error('Ollama returned 500: {"error":"CUDA error: out of memory"}'), "oom")
        self.assertEqual(core.classify_error("Tor circuit could not be verified"), "tor")
        self.assertIsNone(core.classify_error("Model 'x' is not installed. See what is: ollama list"))

    def test_session_search_spans_projects_and_splits_the_match(self) -> None:
        sessions = [
            {"id": "a", "title": "One", "workspace": "/p1",
             "messages": [{"role": "user", "content": "Fix the login redirect loop now"}]},
            {"id": "b", "title": "Two", "workspace": "/p2",
             "messages": [{"role": "assistant", "content": "The REDIRECT happens in auth.py"},
                          {"role": "tool", "content": "redirect"}]},
            {"id": "c", "title": "Three", "workspace": None, "messages": [{"role": "user", "content": "nothing"}]},
        ]
        hits = core.search_sessions(sessions, "redirect")
        self.assertEqual([h["session_id"] for h in hits], ["a", "b"])
        self.assertEqual(hits[1]["match"], "REDIRECT")
        self.assertTrue(hits[0]["before"].endswith("login "))
        self.assertEqual(core.search_sessions(sessions, "r"), [])

    def test_age_based_deletion_uses_real_timestamps(self) -> None:
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        sessions = [{"id": "new", "updated_at": (now - timedelta(hours=2)).isoformat()},
                    {"id": "old", "updated_at": (now - timedelta(days=9)).isoformat()},
                    {"id": "bad", "updated_at": "yesterday"}]
        self.assertEqual([s["id"] for s in core.sessions_older_than(sessions, 7, now)], ["old"])
        self.assertEqual(core.sessions_older_than(sessions, 0, now), [])

    def test_passphrase_is_stored_only_as_a_salted_hash(self) -> None:
        record = core.hash_passphrase("correct horse")
        self.assertNotIn("correct horse", json.dumps(record))
        self.assertTrue(core.verify_passphrase("correct horse", record))
        self.assertFalse(core.verify_passphrase("wrong", record))
        self.assertFalse(core.verify_passphrase("x", {"kdf": "md5", "salt": "00", "hash": "00"}))
        self.assertNotEqual(core.hash_passphrase("same")["hash"], core.hash_passphrase("same")["hash"])

    def test_egress_log_keeps_times_and_destinations_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = core.EgressLog(Path(tmp) / "egress.jsonl")
            log.append("Tor", "Chat search · DuckDuckGo onion service")
            log.append("Direct", "Model download · registry.ollama.ai")
            log.append("Bogus", "Update check · github.com")
            day = datetime_today()
            rows = log.entries(day)
            self.assertEqual([r["route"] for r in rows], ["Tor", "Direct", "Tor"])
            self.assertEqual(log.counts(day), {"search": 1, "tor": 2, "direct": 1})
            self.assertEqual(log.entries("1999-01-01"), [])
            csv_text = core.network_log_csv(rows)
            self.assertTrue(csv_text.startswith("time,route,what\n"))
            self.assertEqual(len(json.loads(core.network_log_json(rows))), 3)
            agent_log = Path(tmp) / "agent-net.log"
            agent_log.write_text(
                f"{day} 10:00:01 tor example.org:443\n{day} 10:00:02 local 127.0.0.1:11434\n"
                f"{day} 10:00:03 REFUSED 1.2.3.4:80\n1999-01-01 00:00:00 tor old.example:443\n",
                encoding="utf-8")
            agent_rows = core.agent_net_entries(agent_log, day)
            self.assertEqual([r["route"] for r in agent_rows], ["Tor", "Refused"])
            self.assertTrue(agent_rows[0]["what"].startswith("Agent network"))

    def test_revert_restores_head_and_undo_brings_the_work_back(self) -> None:
        if shutil_which("git") is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as backups:
            root = Path(tmp)
            run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
            run("init", "-q")
            run("config", "user.email", "t@example.invalid")
            run("config", "user.name", "t")
            (root / "a.txt").write_text("original\n", encoding="utf-8")
            (root / "b.txt").write_text("keep\n", encoding="utf-8")
            run("add", ".")
            run("commit", "-q", "-m", "init")
            (root / "a.txt").write_text("agent edit\n", encoding="utf-8")
            (root / "new.txt").write_text("brand new\n", encoding="utf-8")
            (root / "staged.txt").write_text("staged new\n", encoding="utf-8")
            run("add", "staged.txt")
            stats = core.change_stats(root)
            self.assertEqual(stats.get("a.txt"), (1, 1))
            snapshot = core.WorkspaceInspector().inspect(root)
            record = core.revert_changes(root, snapshot.changes, backups)
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "original\n")
            self.assertFalse((root / "new.txt").exists())
            self.assertFalse((root / "staged.txt").exists())
            self.assertEqual(core.WorkspaceInspector().inspect(root).changes, ())
            restored = core.undo_revert(record)
            self.assertEqual(restored, 3)
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "agent edit\n")
            self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "brand new\n")
            with self.assertRaises(ValueError):
                core.revert_changes(root, [{"status": "M", "path": "../escape.txt"}], backups)

    def test_revert_hunks_puts_back_only_the_chosen_parts(self) -> None:
        if shutil_which("git") is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as backups:
            root = Path(tmp)
            run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
            run("init", "-q")
            run("config", "user.email", "t@example.invalid")
            run("config", "user.name", "t")
            original = "".join(f"line {n}\n" for n in range(1, 41))
            (root / "f.txt").write_text(original, encoding="utf-8")
            run("add", ".")
            run("commit", "-q", "-m", "init")
            edited = original.replace("line 3\n", "line three\n").replace("line 30\n", "line thirty\n")
            (root / "f.txt").write_text(edited, encoding="utf-8")
            hunks = core.diff_hunks(root, "f.txt")
            self.assertEqual([(h.added, h.removed) for h in hunks], [(1, 1), (1, 1)])
            record = core.revert_hunks(root, "f.txt", [1], backups)
            now = (root / "f.txt").read_text(encoding="utf-8")
            self.assertIn("line three\n", now)              # unticked: stays
            self.assertIn("line 30\n", now)                 # ticked: back to HEAD
            self.assertEqual(core.undo_revert(record), 1)
            self.assertEqual((root / "f.txt").read_text(encoding="utf-8"), edited)
            with self.assertRaises(ValueError):
                core.revert_hunks(root, "f.txt", [5], backups)
            with self.assertRaises(ValueError):
                core.diff_hunks(root, "../escape.txt")

    def test_model_fit_and_node_version_gate(self) -> None:
        gib = 1024 ** 3
        self.assertEqual(core.model_fit(5 * gib, 12_288, None), "Fits")
        self.assertEqual(core.model_fit(11 * gib, 12_288, None), "Tight")
        self.assertEqual(core.model_fit(17 * gib, 12_288, None), "Too big")
        self.assertEqual(core.model_fit(None, 12_288, None), "")
        self.assertEqual(core.model_fit(5 * gib, None, None), "")
        self.assertTrue(core.node_supports_agent("v22.19.0"))
        self.assertTrue(core.node_supports_agent("v24.1.0"))
        self.assertFalse(core.node_supports_agent("v22.18.9"))
        self.assertFalse(core.node_supports_agent(None))
        used, total = core.gpu_memory_mb()
        self.assertTrue(total is None or used is None or 0 <= used <= total)


class RemoteComputeTests(unittest.TestCase):
    def test_targets_are_narrow_and_never_read_as_options(self) -> None:
        self.assertEqual(core.parse_ssh_target(" radagast@bever "), core.SshTarget("radagast@bever"))
        self.assertEqual(core.parse_ssh_target("gpu-box:2222"), core.SshTarget("gpu-box", 2222))
        self.assertEqual(core.parse_ssh_target("10.0.0.5").label, "10.0.0.5")
        for bad in ("", "-oProxyCommand=evil", "a b", "host;rm", "host:0", "host:99999",
                    "$(x)", "user@", None, 42):
            self.assertIsNone(core.parse_ssh_target(bad), bad)

    def test_tunnel_command_forwards_loopback_to_loopback_only(self) -> None:
        argv = core.ssh_tunnel_argv(core.SshTarget("me@gpu", 2222), 40001)
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-N", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ExitOnForwardFailure=yes", argv)
        self.assertEqual(argv[argv.index("-L") + 1], "127.0.0.1:40001:127.0.0.1:11434")
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertEqual(argv[-2:], ["--", "me@gpu"])

    def test_failures_name_the_fix(self) -> None:
        self.assertIn("ssh-copy-id", core.describe_ssh_failure("me@gpu: Permission denied (publickey).", "me@gpu"))
        self.assertIn("accept", core.describe_ssh_failure("Host key verification failed.", "gpu"))
        self.assertIn("Could not find", core.describe_ssh_failure(
            "ssh: Could not resolve hostname nope: Name or service not known", "nope"))
        self.assertEqual(core.describe_ssh_failure("odd\nlast line\n"), "last line")

    def test_settings_drop_junk_and_never_autoconnect_without_a_host(self) -> None:
        self.assertEqual(core.load_remote_settings({}), {"host": "", "autoconnect": False})
        self.assertEqual(core.load_remote_settings({"remote_compute": {"host": "-x", "autoconnect": True}}),
                         {"host": "", "autoconnect": False})
        self.assertEqual(core.load_remote_settings({"remote_compute": {"host": "gpu:22", "autoconnect": 1}}),
                         {"host": "gpu:22", "autoconnect": True})

    def _fake_ssh(self, *, serve: bool, stderr: bytes = b""):
        """A Popen stand-in: 'forwards' by serving /api/version on the -L port."""
        import http.server, io, threading

        class Version(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                data = b'{"version": "0.40.1"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        launched: list[list[str]] = []

        class Proc:
            def __init__(self, argv, **kwargs):
                launched.append(argv)
                self.stderr = io.BytesIO(stderr)
                self.code = None
                self.server = None
                if serve:
                    port = int(argv[argv.index("-L") + 1].split(":")[1])
                    self.server = http.server.HTTPServer(("127.0.0.1", port), Version)
                    threading.Thread(target=self.server.serve_forever, daemon=True).start()
                elif stderr:
                    self.code = 255

            def poll(self):
                return self.code

            def terminate(self):
                if self.server is not None:
                    self.server.shutdown()
                    self.server.server_close()
                self.code = -15

            def wait(self, timeout=None):
                return self.code

            kill = terminate

        return Proc, launched

    def test_tunnel_is_ready_only_when_ollama_answers_through_it(self) -> None:
        proc, launched = self._fake_ssh(serve=True)
        tunnel = core.SshTunnel(core.SshTarget("me@gpu"), popen=proc, which=lambda name: "/usr/bin/ssh")
        port = tunnel.start()
        self.assertTrue(tunnel.alive)
        self.assertEqual((tunnel.local_port, tunnel.version), (port, "0.40.1"))
        self.assertEqual(launched[0][0], "/usr/bin/ssh")
        tunnel.stop()
        self.assertFalse(tunnel.alive)
        self.assertIsNone(tunnel.local_port)

    def test_tunnel_failure_raises_the_actionable_message(self) -> None:
        proc, _ = self._fake_ssh(serve=False, stderr=b"me@gpu: Permission denied (publickey).\n")
        tunnel = core.SshTunnel(core.SshTarget("me@gpu"), popen=proc, which=lambda name: "ssh")
        with self.assertRaises(RuntimeError) as caught:
            tunnel.start()
        self.assertIn("ssh-copy-id", str(caught.exception))
        self.assertFalse(tunnel.alive)
        with self.assertRaises(RuntimeError) as missing:
            core.SshTunnel(core.SshTarget("gpu"), which=lambda name: None).start()
        self.assertIn("OpenSSH", str(missing.exception))

    def test_login_without_ollama_fails_fast_and_cancel_leaves_nothing(self) -> None:
        proc, _ = self._fake_ssh(
            serve=False, stderr=b"channel 2: open failed: connect failed: Connection refused\n")
        # A live ssh whose forward has nowhere to land.
        class LiveProc(proc):
            def __init__(self, argv, **kwargs):
                super().__init__(argv, **kwargs)
                self.code = None
        tunnel = core.SshTunnel(core.SshTarget("gpu"), popen=LiveProc, which=lambda name: "ssh",
                                ready_timeout=20)
        import time
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as caught:
            tunnel.start()
        self.assertIn("no Ollama answered", str(caught.exception))
        self.assertLess(time.monotonic() - started, 10)
        self.assertFalse(tunnel.alive)

        hanging, _ = self._fake_ssh(serve=False)
        tunnel = core.SshTunnel(core.SshTarget("gpu"), popen=hanging, which=lambda name: "ssh")
        import threading
        threading.Timer(0.5, tunnel.cancel).start()
        with self.assertRaises(RuntimeError):
            tunnel.start()
        self.assertFalse(tunnel.alive)


def datetime_today() -> str:
    from datetime import datetime
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def shutil_which(name: str):
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    unittest.main()
