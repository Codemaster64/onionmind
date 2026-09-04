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
            self.assertIn("reaches the web only through Tor", core.HARNESS_LIMITATION)

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
        self.assertEqual(powershell[-2:], ("-Command", powershell_text))

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


if __name__ == "__main__":
    unittest.main()
