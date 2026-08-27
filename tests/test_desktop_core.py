"""Tests for the pure Onionmind desktop support module.

Run with::

    python -m unittest tests/test_desktop_core.py
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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
        self.assertIn("inferno:latest", tagged.display_name)

        size_alias = core.describe_model("registry.local/onion/qwen3.5:9b")
        self.assertEqual(size_alias.raw_id, "registry.local/onion/qwen3.5:9b")
        self.assertEqual(size_alias.tier, "BLAZE")
        self.assertEqual(size_alias.tag, "9b")

        vision = core.describe_model("onionmind-inferno-vision:Q4_K_M")
        self.assertEqual(vision.tier, "INFERNO")
        self.assertEqual(vision.tag, "Q4_K_M")

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

    def test_git_processes_use_argument_lists_without_hidden_windows_flags(self) -> None:
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
            self.assertNotIn("creationflags", keywords)


class AgentAndTerminalTests(unittest.TestCase):
    def test_agent_command_is_local_workspace_scoped_and_edit_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="onion mind agent ") as temporary:
            root = Path(temporary)
            cwd = root / "project with spaces"
            cwd.mkdir()
            state_root = root / "agent state"
            prompt = "Fix parser.py and explain the diff."
            spec = core.AgentSpec(
                state_root=state_root,
                launcher=("node", "qwen-cli-entry.js"),
            )

            command = spec.build(
                model="inferno:latest",
                task=prompt,
                cwd=cwd,
            )

            self.assertEqual(command.argv[:2], ("node", "qwen-cli-entry.js"))
            self.assertEqual(command.cwd, cwd.resolve())
            self.assertEqual(command.argv[command.argv.index("--prompt") + 1], prompt)
            self.assertEqual(command.argv[command.argv.index("--model") + 1], "inferno:latest")
            system_prompt = command.argv[command.argv.index("--system-prompt") + 1]
            self.assertIn("Onionmind Agent", system_prompt)
            self.assertIn("current project", system_prompt)
            self.assertIn("file tools", system_prompt)
            self.assertLess(len(system_prompt), 800)
            self.assertIn("--output-format", command.argv)
            self.assertIn("stream-json", command.argv)
            self.assertIn("--include-partial-messages", command.argv)
            self.assertIn("--approval-mode", command.argv)
            self.assertIn("auto-edit", command.argv)
            self.assertIn("--safe-mode", command.argv)
            self.assertNotIn("yolo", command.argv)
            excluded = command.argv[command.argv.index("--exclude-tools") + 1]
            self.assertIn("run_shell_command", excluded)
            self.assertIn("web_fetch", excluded)
            self.assertIn("web_search", excluded)
            self.assertIn("computer_use__click", excluded)
            self.assertIn("computer_use__type_text", excluded)
            self.assertIn("record_artifact", excluded)
            self.assertIn("todo_write", excluded)
            for allowed_file_tool in (
                "edit",
                "glob",
                "grep_search",
                "read_file",
                "write_file",
            ):
                self.assertNotIn(allowed_file_tool, excluded.split(","))
            self.assertEqual(
                command.argv[command.argv.index("--openai-base-url") + 1],
                "http://127.0.0.1:11434/v1",
            )
            environment = dict(command.environment)
            self.assertEqual(environment["QWEN_USAGE_STATISTICS_ENABLED"], "false")
            self.assertEqual(environment["QWEN_CODE_SKIP_UPDATE_CHECK_ONCE"], "1")
            self.assertEqual(Path(environment["QWEN_HOME"]), state_root.resolve())
            self.assertEqual(
                Path(environment["QWEN_RUNTIME_DIR"]),
                (state_root / "runtime").resolve(),
            )
            agent_settings = json.loads(
                (state_root / "settings.json").read_text(encoding="utf-8")
            )
            generation = agent_settings["model"]["generationConfig"]
            self.assertEqual(generation["contextWindowSize"], 32768)
            self.assertEqual(generation["samplingParams"]["max_tokens"], 2048)
            self.assertFalse(generation["reasoning"])
            self.assertEqual(
                generation["extra_body"]["reasoning_effort"],
                "none",
            )
            self.assertIn("HTTPS_PROXY", command.unset_environment)
            self.assertIn("HTTP_PROXY", command.unset_environment)
            self.assertIn("selected project", core.AGENT_BOUNDARY)
            self.assertIn("file edits", core.AGENT_BOUNDARY)

            with self.assertRaisesRegex(ValueError, "INFERNO"):
                spec.build(model="spark:latest", task=prompt, cwd=cwd)

    def test_agent_rejects_non_loopback_model_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "loopback"):
                core.AgentSpec(
                    state_root=temporary,
                    base_url="https://api.example.com/v1",
                    launcher=("qwen",),
                )

    def test_agent_availability_checks_current_cli_and_local_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            spec = core.AgentSpec(state_root=temporary)
            with mock.patch.object(core.shutil, "which", return_value=None):
                missing = spec.check()
            self.assertFalse(missing.available)
            self.assertIn("Agent runtime is not installed", missing.reason)
            self.assertEqual(missing.boundary, core.AGENT_BOUNDARY)

            completed = subprocess.CompletedProcess(
                ["qwen", "--version"],
                0,
                stdout=b"0.22.0\n",
                stderr=b"",
            )
            endpoint = mock.MagicMock()
            endpoint.status = 200
            endpoint.read.return_value = b'{"data":[{"id":"inferno:latest"}]}'
            endpoint.__enter__.return_value = endpoint
            with mock.patch.object(
                core.AgentSpec,
                "_resolve_launcher",
                return_value=("node", "qwen-cli-entry.js"),
            ), mock.patch.object(core.subprocess, "run", return_value=completed) as run, mock.patch.object(
                core.urllib.request, "urlopen", return_value=endpoint
            ) as urlopen:
                available = spec.check()
            self.assertTrue(available.available)
            self.assertEqual(available.version, "0.22.0")
            positional, keywords = run.call_args
            self.assertEqual(positional[0][-1], "--version")
            self.assertIs(keywords["shell"], False)
            self.assertNotIn("creationflags", keywords)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/v1/models")

            missing_inferno_endpoint = mock.MagicMock()
            missing_inferno_endpoint.status = 200
            missing_inferno_endpoint.read.return_value = (
                b'{"data":[{"id":"spark:latest"}]}'
            )
            missing_inferno_endpoint.__enter__.return_value = missing_inferno_endpoint
            with mock.patch.object(
                core.AgentSpec,
                "_resolve_launcher",
                return_value=("node", "qwen-cli-entry.js"),
            ), mock.patch.object(
                core.subprocess, "run", return_value=completed
            ), mock.patch.object(
                core.urllib.request,
                "urlopen",
                return_value=missing_inferno_endpoint,
            ):
                unavailable_model = spec.check()
            self.assertFalse(unavailable_model.available)
            self.assertIn("INFERNO", unavailable_model.reason)

            old = subprocess.CompletedProcess(
                ["qwen", "--version"], 0, stdout=b"0.21.0\n", stderr=b""
            )
            with mock.patch.object(
                core.AgentSpec,
                "_resolve_launcher",
                return_value=("qwen",),
            ), mock.patch.object(core.subprocess, "run", return_value=old):
                unsupported = spec.check()
            self.assertFalse(unsupported.available)
            self.assertIn("update Onionmind Agent", unsupported.reason)

    def test_agent_stream_parser_hides_protocol_and_streams_text(self) -> None:
        parser = core.AgentStreamParser()
        first = parser.feed(
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Updated "}}}\n'
            '{"type":"stream_event","event":{"type":"content_block_delta",'
        )
        second = parser.feed(
            '"delta":{"type":"text_delta","text":"parser.py"}}}\n'
            '{"type":"result","subtype":"success"}\n'
        )
        self.assertEqual(first, ("Updated ",))
        self.assertEqual(second, ("parser.py",))
        self.assertEqual(parser.finish(), ())

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
        self.assertNotIn("CREATE_NO_WINDOW", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("PySide", source)


if __name__ == "__main__":
    unittest.main()
