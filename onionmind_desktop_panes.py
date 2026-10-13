"""Bridges to the core module, and the window's side panes: the left rail, the
terminal drawer and the inspector.

Part of onionmind_desktop, which re-exports every name defined here.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import QProcess, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from onionmind_desktop_theme import (
    _as_text,
    _brand_runtime_text,
    _field,
    _icon,
    _ICON_ROLE,
    _now_iso,
    _path_key,
    _sanitize_assistant_messages,
    _themed_icon,
    ANSI_ESCAPE,
    APP_ID,
    APP_NAME,
    SKIP_DIRS,
    theme_tokens,
)

from onionmind_desktop_widgets import RunPanel


class SessionBridge:
    """Use onionmind_desktop_core when present; keep a small QSettings fallback."""

    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.desktop_core = desktop_core
        self.root = root
        self.store = None
        if desktop_core is not None and hasattr(desktop_core, "SessionStore"):
            try:
                self.store = desktop_core.SessionStore(root)
            except Exception:
                self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)

    @staticmethod
    def _clean_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return _sanitize_assistant_messages(messages)

    @classmethod
    def _clean_session(cls, session: Any) -> Any:
        messages = cls._clean_messages(_field(session, "messages", ()) or ())
        if dataclasses.is_dataclass(session):
            return dataclasses.replace(session, messages=messages)
        if isinstance(session, dict):
            cleaned = copy.deepcopy(session)
            cleaned["messages"] = messages
            return cleaned
        setattr(session, "messages", messages)
        return session

    def create(self, title: str, model: str, workspace: Optional[str], messages: Iterable[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            return self.store.create(title=title, model=model, workspace=workspace, messages=clean_messages)
        now = _now_iso()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "model": model,
            "workspace": workspace,
            "messages": clean_messages,
            "created_at": now,
            "updated_at": now,
        }

    def list(self, *, archived: bool = False) -> list[Any]:
        if self.store is not None:
            try:
                return [
                    self._clean_session(item)
                    for item in self.store.list(archived=archived)
                ]
            except Exception:
                return []
        key = "archived_sessions" if archived else "sessions"
        try:
            return [
                self._clean_session(item)
                for item in json.loads(self.fallback.value(key, "[]"))
            ]
        except (TypeError, ValueError):
            return []

    def save(self, session: Any, *, title: str, model: str, workspace: Optional[str], messages: list[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            if dataclasses.is_dataclass(session):
                session = dataclasses.replace(
                    session,
                    title=title,
                    model=model,
                    workspace=workspace,
                    messages=clean_messages,
                )
            else:
                for key, value in {
                    "title": title,
                    "model": model,
                    "workspace": workspace,
                    "messages": clean_messages,
                }.items():
                    setattr(session, key, value)
            return self.store.save(session)
        payload = dict(session)
        payload.update(
            title=title,
            model=model,
            workspace=workspace,
            messages=clean_messages,
            updated_at=_now_iso(),
        )
        sessions = [s for s in self.list() if _field(s, "id") != payload["id"]]
        sessions.insert(0, payload)
        self.fallback.setValue("sessions", json.dumps(sessions[:80]))
        return payload

    def archive(self, session_id: str) -> Any:
        if self.store is not None:
            try:
                return self.store.archive(session_id)
            except Exception:
                return None
        sessions = self.list()
        archived = next((item for item in sessions if _as_text(_field(item, "id")) == session_id), None)
        remaining = [item for item in sessions if _as_text(_field(item, "id")) != session_id]
        self.fallback.setValue("sessions", json.dumps(remaining[:80]))
        if archived is not None:
            try:
                archive_items = list(json.loads(self.fallback.value("archived_sessions", "[]")))
            except (TypeError, ValueError):
                archive_items = []
            archive_items.insert(0, archived)
            self.fallback.setValue("archived_sessions", json.dumps(archive_items[:80]))
        return archived

    def delete(self, session_id: str) -> bool:
        """Permanently remove a session instead of moving it to local archive storage."""

        if self.store is not None:
            deleter = getattr(self.store, "delete", None)
            if not callable(deleter):
                return False
            try:
                return bool(deleter(session_id))
            except Exception:
                return False

        deleted = False
        for key in ("sessions", "archived_sessions"):
            try:
                items = list(json.loads(self.fallback.value(key, "[]")))
            except (TypeError, ValueError):
                items = []
            remaining = [
                item for item in items if _as_text(_field(item, "id")) != session_id
            ]
            if len(remaining) != len(items):
                deleted = True
                self.fallback.setValue(key, json.dumps(remaining[:80]))
        return deleted


class SettingsBridge:
    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)
        if desktop_core is not None and hasattr(desktop_core, "SettingsStore"):
            try:
                self.store = desktop_core.SettingsStore(root / "settings.json", defaults={})
            except Exception:
                self.store = None

    def load(self) -> dict[str, Any]:
        if self.store is not None:
            try:
                return dict(self.store.load())
            except Exception:
                return {}
        try:
            return dict(json.loads(self.fallback.value("settings_json", "{}")))
        except (TypeError, ValueError):
            return {}

    def save(self, settings: dict[str, Any]) -> None:
        if self.store is not None:
            try:
                self.store.save(settings)
                return
            except Exception:
                pass
        self.fallback.setValue("settings_json", json.dumps(settings))


class WorkspaceBridge:
    def __init__(self, desktop_core: Any) -> None:
        self.inspector = None
        if desktop_core is not None and hasattr(desktop_core, "WorkspaceInspector"):
            try:
                self.inspector = desktop_core.WorkspaceInspector(max_entries=260, max_depth=5)
            except Exception:
                self.inspector = None

    def inspect(self, selected: str) -> dict[str, Any]:
        if self.inspector is not None:
            snap = self.inspector.inspect(selected)
            try:
                diff = self.inspector.diff(selected)
            except Exception as exc:
                diff = f"Diff unavailable: {exc}"
            changes = [
                {
                    "status": _field(change, "status", "?"),
                    "path": _field(change, "path", ""),
                    "original_path": _field(change, "original_path"),
                }
                for change in (_field(snap, "changes", ()) or ())
            ]
            return {
                "root": _as_text(_field(snap, "root", selected)),
                "is_git": bool(_field(snap, "is_git", False)),
                "branch": _as_text(_field(snap, "branch", "No repository")),
                "dirty": bool(_field(snap, "dirty", False)),
                "changes": changes,
                "agents_files": [_as_text(p) for p in (_field(snap, "agents_files", ()) or ())],
                "file_tree": [_as_text(p) for p in (_field(snap, "file_tree", ()) or ())],
                "tree_truncated": bool(_field(snap, "tree_truncated", False)),
                "summary": _as_text(_field(snap, "change_summary", "")),
                "diff": diff,
            }
        return self._fallback_inspect(selected)

    def _fallback_inspect(self, selected: str) -> dict[str, Any]:
        root = Path(selected).resolve()
        files: list[str] = []
        agents: list[str] = []
        for current, dirs, names in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            safe_directories: list[str] = []
            for directory_name in dirs:
                if directory_name in SKIP_DIRS or directory_name.startswith("."):
                    continue
                candidate = current_path / directory_name
                is_junction = bool(
                    getattr(candidate, "is_junction", lambda: False)()
                )
                if candidate.is_symlink() or is_junction:
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(root)
                except (OSError, ValueError):
                    continue
                safe_directories.append(directory_name)
            dirs[:] = safe_directories
            if depth >= 5:
                dirs[:] = []
            for name in sorted(names):
                relative = (current_path / name).relative_to(root).as_posix()
                files.append(relative)
                if name.upper() == "AGENTS.MD":
                    agents.append(relative)
                if len(files) >= 260:
                    break
            if len(files) >= 260:
                break
        git = shutil.which("git")
        branch, changes, diff = "No repository", [], "This folder is not a Git repository."
        is_git = False
        if git:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            git_base = [
                git,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
            ]
            try:
                probe = subprocess.run(
                    [*git_base, "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True,
                    text=True, timeout=6, creationflags=flags,
                )
                is_git = probe.returncode == 0 and probe.stdout.strip() == "true"
                if is_git:
                    branch_run = subprocess.run(
                        [*git_base, "branch", "--show-current"], cwd=root, capture_output=True,
                        text=True, timeout=6, creationflags=flags,
                    )
                    branch = branch_run.stdout.strip() or "detached HEAD"
                    status_run = subprocess.run(
                        [*git_base, "status", "--short", "--untracked-files=normal", "--ignore-submodules=all"], cwd=root, capture_output=True,
                        text=True, timeout=8, creationflags=flags,
                    )
                    for line in status_run.stdout.splitlines():
                        if not line.strip():
                            continue
                        changes.append({"status": line[:2].strip() or "?", "path": line[3:], "original_path": None})
                    diff_run = subprocess.run(
                        [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3", "HEAD", "--"], cwd=root, capture_output=True,
                        text=True, timeout=10, creationflags=flags,
                    )
                    diff = diff_run.stdout
                    if diff_run.returncode != 0:
                        cached_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--cached", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        working_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        diff = cached_run.stdout + working_run.stdout
                    untracked = [
                        change["path"] for change in changes if change["status"] == "??"
                    ]
                    if untracked:
                        diff += "\nUntracked files (content preview unavailable in fallback mode):\n"
                        diff += "".join(f"  {path}\n" for path in untracked[:50])
                    diff = diff[:200000] or "No staged or unstaged diff."
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "root": str(root), "is_git": is_git, "branch": branch, "dirty": bool(changes),
            "changes": changes, "agents_files": agents, "file_tree": files,
            "tree_truncated": len(files) >= 260, "summary": f"{len(changes)} observed change(s)", "diff": diff,
        }


class HarnessBridge:
    FALLBACK_LIMITATION = (
        "Onionmind Agent is an early-access local coding workflow. Approvals are on "
        "by default, and YOLO runs edits and commands without asking - which never "
        "moves the network boundary. "
        "The agent reaches the web only through Tor and does not start without it.\n\n"
        "Tor is enforced by the environment the agent runs in, not by the operating "
        "system: a compiled binary, python -S, or a tool that ignores proxies (ping, "
        "nslookup) can still reach the network directly. Closing that needs an OS "
        "egress rule - a firewall rule, a container - or the Matchstick live USB."
    )

    def __init__(self, desktop_core: Any, core: Any = None) -> None:
        self.desktop_core = desktop_core
        self.core = core
        self.spec = None
        if desktop_core is not None and hasattr(desktop_core, "HarnessSpec"):
            try:
                self.spec = desktop_core.HarnessSpec()
            except Exception:
                self.spec = None

    def _launcher(self, model: str, task: str, cwd: str = "",
                  yolo: bool = False, stream: bool = False,
                  deny: Iterable[str] = (), no_network: bool = False,
                  resume: str = "") -> Optional[list[str]]:
        """``onionmind.py --agent``: the one place Tor is verified and enforced.

        Launching the agent directly would inherit this window's environment,
        which has no proxy and no socket containment - the agent would have
        direct web access whether Tor is up or not.
        """
        script = _as_text(getattr(self.core, "__file__", ""))
        if not script or not callable(getattr(self.core, "run_agent", None)):
            return None
        argv = [sys.executable, os.path.abspath(script), "--agent",
                "--model", model]
        if yolo:
            argv.append("--yolo")
        if stream:
            # v2: the structured mode. The task goes to stdin, not the argv.
            argv.append("--stream-json")
        if no_network:
            argv.append("--no-network")
        for rule in deny:
            argv += ["--deny", rule]
        if resume:
            argv += ["--resume", resume]
        if cwd:
            # The working directory is an argument, not just the process cwd:
            # run_agent writes the project settings there before it starts.
            argv += ["--cwd", cwd]
        return argv if stream else argv + [task]

    @property
    def limitation(self) -> str:
        if self.spec is not None:
            value = _as_text(getattr(self.spec, "limitation", "")) or _as_text(
                getattr(self.desktop_core, "HARNESS_LIMITATION", "")
            ) or self.FALLBACK_LIMITATION
            return _brand_runtime_text(value)
        return self.FALLBACK_LIMITATION

    def check(self) -> tuple[bool, str]:
        # The launcher IS the Tor boundary, so without one there is nothing to
        # start: launching the harness ourselves would hand it this window's
        # unproxied environment.
        if self._launcher("model", "task") is None:
            return False, (
                "Onionmind Agent needs the Onionmind runtime to route it through Tor, "
                "and that runtime is not loaded. Re-run Onionmind setup, then restart."
            )
        # A hint, not the gate: re-verifying Tor here would repeat the round trip
        # the launcher makes anyway, on every task. The launcher is what refuses.
        if not getattr(self.core, "_port", None):
            return False, (
                "Tor is not up. Onionmind Agent reaches the web only through Tor and "
                "does not start without it. Start Tor from the toolbar, then try again."
            )
        if self.spec is not None:
            availability = self.spec.check()
            return bool(_field(availability, "available", False)), _brand_runtime_text(
                _field(availability, "reason", "")
            )
        executable = shutil.which("ollama")
        return bool(executable), (
            ""
            if executable
            else "Onionmind Agent is not ready. Re-run Onionmind setup, then restart the app."
        )

    def build(self, *, model: str, task: str, cwd: str,
              yolo: bool = False, stream: bool = False,
              deny: Iterable[str] = (), no_network: bool = False,
              resume: str = "") -> tuple[list[str], str]:
        launcher = self._launcher(model, task, cwd, yolo, stream, deny, no_network, resume)
        if launcher is None:                     # check() refuses first; belt and braces
            raise RuntimeError(
                "Onionmind Agent has no Tor-verified launcher, so it will not start."
            )
        return launcher, cwd


class UpdateBridge:
    """Tor-only self-update for the installed standalone bundle.

    Source installs and development checkouts have no bundle to swap, so the
    bridge reports itself unavailable there and the UI says so instead of
    half-offering an update. Every network call goes through the verified Tor
    port with a fresh isolated circuit, exactly like Chat search: a failed
    check never falls back to a direct request.
    """

    def __init__(self, core: Any, desktop_core: Any) -> None:
        self.core = core
        self.desktop_core = desktop_core
        # Nuitka puts __compiled__ into each compiled module's globals (and
        # sets sys.frozen in standalone); a plain `python onionmind_desktop.py`
        # checkout has neither.
        frozen = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))
        candidate = Path(sys.executable).resolve().parent if frozen else None
        if candidate is not None and not (candidate / "Onionmind.exe").is_file():
            candidate = None
        self.install_dir: Optional[Path] = candidate
        self.work_dir: Optional[Path] = (
            self.install_dir.parent / "onionmind-update" if self.install_dir else None
        )

    @property
    def available(self) -> bool:
        return self.desktop_core is not None and self.install_dir is not None

    def revision(self) -> Optional[str]:
        reader = getattr(self.desktop_core, "installed_revision", None)
        if not self.available or not callable(reader):
            return None
        return reader(self.install_dir)

    def revision_label(self) -> str:
        revision = self.revision()
        if revision is None:
            return "development copy" if not self.available else "unknown revision"
        helper = getattr(self.desktop_core, "short_revision", None)
        return f"revision {helper(revision) if callable(helper) else revision[:7]}"

    def _updater(self) -> Any:
        factory = getattr(self.core, "_proxies", None)
        if not callable(factory):
            raise RuntimeError("This Onionmind build cannot build Tor proxy settings.")
        user_agent = _as_text(getattr(self.core, "UA", "")) or (
            "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        return self.desktop_core.BundleUpdater(
            self.install_dir,
            self.work_dir,
            lambda port: factory(port, True),   # fresh credentials => fresh circuit
            user_agent,
        )

    def tor_port(self) -> Optional[int]:
        port = getattr(self.core, "_port", None)
        return int(port) if port else None

    def check(self) -> Any:
        """Fetch and validate the feed manifest. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct update check.")
        return self._updater().fetch_manifest(port)

    def download(
        self,
        manifest: Any,
        progress: Callable[[Optional[float], str], None],
        stop_event: Any,
    ) -> Path:
        """Download, verify, and stage the new bundle. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct download.")
        updater = self._updater()
        archive = updater.download(port, manifest, progress=progress, stop_event=stop_event)
        return updater.stage(manifest, archive)

    def pending(self) -> Optional[Path]:
        finder = getattr(self.desktop_core, "pending_staging_dir", None)
        if not self.available or not callable(finder):
            return None
        return finder(self.work_dir)

    def housekeep(self) -> None:
        pruner = getattr(self.desktop_core, "prune_update_workdir", None)
        if self.available and callable(pruner):
            try:
                pruner(self.work_dir, running_revision=self.revision())
            except OSError:
                pass

    def apply_command(self, staging_dir: str) -> list[str]:
        return self._updater().apply_command(staging_dir)


class LeftRail(QWidget):
    newTaskRequested = Signal()
    addSessionRequested = Signal()
    openFolderRequested = Signal()
    sessionSelected = Signal(str)
    projectSelected = Signal(str)
    removeProjectRequested = Signal(str)
    deleteProjectRequested = Signal(str)
    modelsRequested = Signal()
    settingsRequested = Signal()
    exportRequested = Signal()
    archiveRequested = Signal(str)
    deleteSessionRequested = Signal(str)
    renameSessionRequested = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftRail")
        self.setMinimumWidth(190)
        self.setMaximumWidth(290)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(9)

        quick = QHBoxLayout()
        new_button = QPushButton("New task")
        new_button.setObjectName("newTaskButton")
        _themed_icon(new_button, "new_task")
        new_button.setAccessibleName("Create new task")
        new_button.clicked.connect(self.newTaskRequested)
        self.new_button = new_button
        folder_button = QToolButton()
        folder_button.setObjectName("openFolderButton")
        _themed_icon(folder_button, "folder_open")
        folder_button.setToolTip("Open folder (Ctrl+O)")
        folder_button.setAccessibleName("Open project folder")
        folder_button.clicked.connect(self.openFolderRequested)
        self.folder_button = folder_button
        quick.addWidget(new_button, 1)
        quick.addWidget(folder_button)
        layout.addLayout(quick)

        project_header = QHBoxLayout()
        project_label = QLabel("PROJECTS")
        project_label.setObjectName("sectionTitle")
        add_project = QToolButton()
        add_project.setObjectName("bareButton")
        _themed_icon(add_project, "folder_plus")
        add_project.setToolTip("Open another project")
        add_project.setAccessibleName("Open another project")
        add_project.clicked.connect(self.openFolderRequested)
        self.add_project_button = add_project
        remove_project = QToolButton()
        remove_project.setObjectName("bareButton")
        _themed_icon(remove_project, "close")
        remove_project.setToolTip(
            "Remove selected project from this list; its folder stays on this machine"
        )
        remove_project.setAccessibleName(
            "Remove selected project from Projects and keep its folder"
        )
        remove_project.clicked.connect(self._remove_selected_project)
        remove_project.setEnabled(False)
        self.remove_project_button = remove_project
        delete_project = QToolButton()
        delete_project.setObjectName("bareButton")
        _themed_icon(delete_project, "clear")
        delete_project.setToolTip(
            "Permanently delete the selected project folder from this machine"
        )
        delete_project.setAccessibleName(
            "Permanently delete selected project folder from this machine"
        )
        delete_project.clicked.connect(self._delete_selected_project)
        delete_project.setEnabled(False)
        self.delete_project_button = delete_project
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        project_header.addWidget(add_project)
        project_header.addWidget(remove_project)
        project_header.addWidget(delete_project)
        layout.addLayout(project_header)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(240)
        self.projects.setAccessibleName("Projects")
        self.projects.itemClicked.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.itemActivated.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.currentItemChanged.connect(self._project_current_changed)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self._show_project_menu)
        self.project_menu = QMenu(self)
        self.project_add_action = _themed_icon(self.project_menu.addAction("Add project…"), "folder_plus")
        self.project_add_action.triggered.connect(self.openFolderRequested)
        self.project_menu.addSeparator()
        self.project_remove_action = _themed_icon(self.project_menu.addAction("Remove from Projects (keep folder)"), "close")
        self.project_remove_action.triggered.connect(self._remove_selected_project)
        self.project_remove_action.setEnabled(False)
        self.project_delete_action = _themed_icon(self.project_menu.addAction("Delete folder from machine…"), "clear")
        self.project_delete_action.triggered.connect(self._delete_selected_project)
        self.project_delete_action.setEnabled(False)
        layout.addWidget(self.projects)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("railDivider")
        layout.addWidget(divider)
        session_header = QHBoxLayout()
        session_label = QLabel("SESSIONS")
        session_label.setObjectName("sectionTitle")
        self.session_label = session_label
        add_session = QToolButton()
        add_session.setObjectName("bareButton")
        _themed_icon(add_session, "new_task")
        add_session.setToolTip("Add a saved session")
        add_session.setAccessibleName("Add saved session")
        add_session.clicked.connect(self.addSessionRequested)
        self.add_session_button = add_session
        archive = QToolButton()
        archive.setObjectName("bareButton")
        _themed_icon(archive, "archive")
        archive.setToolTip(
            "Remove selected session from this list; keep it in local archive storage"
        )
        archive.setAccessibleName(
            "Remove selected session from Sessions and keep an archived copy"
        )
        archive.clicked.connect(self._archive_selected)
        archive.setEnabled(False)
        self.archive_button = archive
        delete_session = QToolButton()
        delete_session.setObjectName("bareButton")
        _themed_icon(delete_session, "clear")
        delete_session.setToolTip(
            "Permanently delete the selected session from this machine"
        )
        delete_session.setAccessibleName(
            "Permanently delete selected session from this machine"
        )
        delete_session.clicked.connect(self._delete_selected_session)
        delete_session.setEnabled(False)
        self.delete_session_button = delete_session
        session_header.addWidget(session_label)
        session_header.addStretch(1)
        session_header.addWidget(add_session)
        session_header.addWidget(archive)
        session_header.addWidget(delete_session)
        layout.addLayout(session_header)
        self.session_filter = QLineEdit()
        self.session_filter.setPlaceholderText("Filter sessions")
        self.session_filter.setAccessibleName("Filter sessions by name")
        self.session_filter.setClearButtonEnabled(True)
        clear_filter = self.session_filter.findChild(QToolButton)
        if clear_filter is not None:
            clear_filter.setAccessibleName("Clear the session filter")
        self.session_filter.textChanged.connect(self._apply_session_filter)
        layout.addWidget(self.session_filter)
        self.sessions = QListWidget()
        self.sessions.setAccessibleName("Saved sessions")
        self.sessions.itemClicked.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.itemActivated.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.currentItemChanged.connect(self._session_current_changed)
        self.no_sessions = QLabel("No conversations yet. Start one with New task.")
        self.no_sessions.setObjectName("meta")
        self.no_sessions.setWordWrap(True)
        self.no_sessions.hide()
        self.empty_text_enabled = False
        self.sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._show_session_menu)
        self.session_menu = QMenu(self)
        self.session_add_action = _themed_icon(self.session_menu.addAction("Add session"), "new_task")
        self.session_add_action.triggered.connect(self.addSessionRequested)
        self.session_menu.addSeparator()
        self.session_rename_action = _themed_icon(self.session_menu.addAction("Rename…"), "pencil")
        self.session_rename_action.triggered.connect(self._rename_selected_session)
        self.session_rename_action.setEnabled(False)
        self.session_remove_action = _themed_icon(self.session_menu.addAction("Remove from Sessions (keep archived copy)"), "archive")
        self.session_remove_action.triggered.connect(self._archive_selected)
        self.session_remove_action.setEnabled(False)
        self.session_delete_action = _themed_icon(self.session_menu.addAction("Delete session from machine…"), "clear")
        self.session_delete_action.triggered.connect(self._delete_selected_session)
        self.session_delete_action.setEnabled(False)
        layout.addWidget(self.sessions, 1)
        layout.addWidget(self.no_sessions)
        # v2 sessionSearch: message hits from every project under the list.
        self.hits_title = QLabel("IN MESSAGES · ALL PROJECTS")
        self.hits_title.setObjectName("sectionTitle")
        self.hits_title.hide()
        layout.addWidget(self.hits_title)
        self.message_hits = QListWidget()
        self.message_hits.setAccessibleName("Message matches in every project")
        self.message_hits.setWordWrap(True)
        self.message_hits.hide()
        self.message_hits.itemActivated.connect(self._hit_chosen)
        self.message_hits.itemClicked.connect(self._hit_chosen)
        layout.addWidget(self.message_hits, 1)

        export = QPushButton("Export conversation")
        export.setObjectName("railAction")
        _themed_icon(export, "export")
        export.setToolTip("Export this conversation as Markdown (Ctrl+Shift+S)")
        export.setAccessibleName("Export conversation as Markdown")
        export.clicked.connect(self.exportRequested)
        export.setEnabled(False)
        self.export_button = export
        models = QPushButton("Models")
        models.setObjectName("railAction")
        _themed_icon(models, "model")
        models.setToolTip("Manage local models")
        models.setAccessibleName("Manage local models")
        models.clicked.connect(self.modelsRequested)
        self.models_button = models
        settings = QPushButton("Settings")
        settings.setObjectName("railAction")
        _themed_icon(settings, "settings")
        settings.setToolTip("Settings (Ctrl+,)")
        settings.setAccessibleName("Open Onionmind settings")
        settings.clicked.connect(self.settingsRequested)
        self.settings_button = settings
        layout.addWidget(export)
        layout.addWidget(models)
        layout.addWidget(settings)

    hitSelected = Signal(str)

    def _hit_chosen(self, item: QListWidgetItem) -> None:
        session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
        if session_id:
            self.hitSelected.emit(session_id)

    def set_message_hits(self, hits: Optional[list[dict[str, Any]]], project_name: Callable[[Any], str]) -> None:
        """None hides the section; a list (maybe empty) shows it."""
        self.message_hits.clear()
        visible = hits is not None
        self.hits_title.setVisible(visible)
        self.message_hits.setVisible(visible)
        if not visible:
            return
        import html as _html
        if not hits:
            empty = QListWidgetItem("No messages match.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.message_hits.addItem(empty)
            return
        accent = theme_tokens()["accent_text"]
        for hit in hits:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, hit["session_id"])
            plain = f"{project_name(hit.get('workspace'))} · {hit['title']}\n{hit['before']}{hit['match']}{hit['after']}"
            item.setToolTip(plain)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, plain)
            label = QLabel(
                f"<span>{_html.escape(project_name(hit.get('workspace')))} · {_html.escape(hit['title'])}</span><br>"
                f"<span style='color:{theme_tokens()['muted']}'>{_html.escape(hit['before'])}"
                f"<b style='color:{accent}'>{_html.escape(hit['match'])}</b>{_html.escape(hit['after'])}</span>")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setContentsMargins(6, 4, 6, 4)
            item.setSizeHint(QSize(100, max(46, label.sizeHint().height() + 6)))
            self.message_hits.addItem(item)
            self.message_hits.setItemWidget(item, label)

    def _archive_selected(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.archiveRequested.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))

    def _rename_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is None:
            return
        session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
        current_title = item.text().split("\n", 1)[0]
        name, accepted = QInputDialog.getText(
            self, "Rename session", "Session name", text=current_title
        )
        if accepted and name.strip():
            self.renameSessionRequested.emit(session_id, name.strip())

    def _apply_session_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.sessions.count()):
            item = self.sessions.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _delete_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.deleteSessionRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _remove_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.removeProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _delete_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.deleteProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _project_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.remove_project_button.setEnabled(available)
        self.delete_project_button.setEnabled(available)
        self.project_remove_action.setEnabled(available)
        self.project_delete_action.setEnabled(available)

    def _session_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.archive_button.setEnabled(available)
        self.delete_session_button.setEnabled(available)
        self.session_rename_action.setEnabled(available)
        self.session_remove_action.setEnabled(available)
        self.session_delete_action.setEnabled(available)

    def _show_project_menu(self, position: Any) -> None:
        item = self.projects.itemAt(position)
        if item is not None:
            self.projects.setCurrentItem(item)
        self.project_menu.popup(self.projects.viewport().mapToGlobal(position))

    def _show_session_menu(self, position: Any) -> None:
        item = self.sessions.itemAt(position)
        if item is not None:
            self.sessions.setCurrentItem(item)
        self.session_menu.popup(self.sessions.viewport().mapToGlobal(position))

    def set_conversation_available(self, available: bool) -> None:
        self.export_button.setEnabled(bool(available))

    def add_project(self, path: str, select: bool = True) -> None:
        if not path:
            return
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if _path_key(item.data(Qt.ItemDataRole.UserRole)) == normalized:
                if select:
                    self.projects.setCurrentItem(item)
                return
        name = Path(path).name or path
        item = QListWidgetItem(f"{name}\n{path}")
        _themed_icon(item, "folder")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setSizeHint(QSize(100, 47))
        self.projects.insertItem(0, item)
        if select:
            self.projects.setCurrentItem(item)

    def remove_project(self, path: str) -> bool:
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            item_path = _as_text(item.data(Qt.ItemDataRole.UserRole))
            if _path_key(item_path) == normalized:
                self.projects.takeItem(index)
                self.projects.clearSelection()
                self.projects.setCurrentRow(-1)
                return True
        return False

    def clear_projects(self) -> int:
        """Forget every remembered project. The folders themselves are not
        touched - this list is only Onionmind's memory of them."""
        removed = self.projects.count()
        self.projects.clear()
        return removed

    def clear_session_selection(self) -> None:
        self.sessions.clearSelection()
        self.sessions.setCurrentRow(-1)

    def set_session_scope(self, project: Optional[str]) -> None:
        """Name the project whose sessions are listed, so a short list reads as
        scoping rather than loss."""
        name = Path(project).name if project else ""
        self.session_label.setText(f"SESSIONS · {name}" if name else "SESSIONS")
        self.session_label.setToolTip(
            f"Sessions saved in {project}" if project else "Sessions saved with no project open"
        )

    def set_sessions(self, sessions: Iterable[Any], current_id: Optional[str] = None) -> None:
        self.sessions.clear()
        for session in sessions:
            session_id = _as_text(_field(session, "id"))
            title = _as_text(_field(session, "title", "New session"))
            updated = _field(session, "updated_at")
            if isinstance(updated, datetime):
                meta = updated.astimezone().strftime("%b %d, %H:%M")
            else:
                meta = _as_text(updated)[:16].replace("T", " ") or "Saved locally"
            item = QListWidgetItem(f"{title}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            item.setSizeHint(QSize(100, 47))
            self.sessions.addItem(item)
            if session_id == current_id:
                self.sessions.setCurrentItem(item)
        self._apply_session_filter(self.session_filter.text())
        self.sync_empty_text()

    def sync_empty_text(self) -> None:
        self.no_sessions.setVisible(self.empty_text_enabled and self.sessions.count() == 0)


class TerminalPane(QFrame):
    closeRequested = Signal()
    stateChanged = Signal(str)

    def __init__(self, desktop_core: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.desktop_core = desktop_core
        self.setObjectName("terminalPane")
        self.setMinimumHeight(135)
        self.setMaximumHeight(200)
        self.workspace = str(Path.cwd())
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel("TERMINAL")
        title.setObjectName("sectionTitle")
        self.scope_label = QLabel(Path(self.workspace).name)
        self.scope_label.setObjectName("meta")
        stop = QToolButton()
        stop.setObjectName("bareButton")
        _themed_icon(stop, "stop")
        stop.setToolTip(
            "Stop the shell command; child processes started by it may require manual termination"
        )
        stop.setAccessibleName("Stop terminal command")
        stop.clicked.connect(self.stop)
        stop.setEnabled(False)
        self.stop_button = stop
        clear = QToolButton()
        clear.setObjectName("bareButton")
        _themed_icon(clear, "clear")
        clear.setToolTip("Clear terminal")
        clear.setAccessibleName("Clear terminal output")
        clear.clicked.connect(lambda: self.output.clear())
        clear.setEnabled(False)
        self.clear_button = clear
        close = QToolButton()
        close.setObjectName("bareButton")
        _themed_icon(close, "close")
        close.setToolTip("Close terminal drawer (Ctrl+`)")
        close.setAccessibleName("Close terminal drawer")
        close.clicked.connect(self.closeRequested)
        self.close_button = close
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.scope_label)
        header.addSpacing(8)
        header.addWidget(stop)
        header.addWidget(clear)
        header.addWidget(close)
        outer.addLayout(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminalOutput")
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Terminal output")
        self.output.document().setMaximumBlockCount(5000)
        self.output.textChanged.connect(
            lambda: self.clear_button.setEnabled(bool(self.output.toPlainText()))
        )
        outer.addWidget(self.output, 1)
        command_row = QHBoxLayout()
        prompt = QLabel(">")
        prompt.setObjectName("accent")
        self.command = QLineEdit()
        self.command.setObjectName("terminalInput")
        shell_name = "PowerShell" if os.name == "nt" else "/bin/sh"
        self.command.setPlaceholderText(f"Run a {shell_name} command in this project")
        self.command.setToolTip(
            f"Commands run through {shell_name} in the active project directory."
        )
        self.command.setAccessibleName("Terminal command")
        self.command.returnPressed.connect(self.run_current)
        self.process.stateChanged.connect(self._sync_process_state)
        command_row.addWidget(prompt)
        command_row.addWidget(self.command, 1)
        outer.addLayout(command_row)

    def set_workspace(self, path: str) -> None:
        self.workspace = path
        self.scope_label.setText(Path(path).name or path)
        self.scope_label.setToolTip(path)

    def append(self, text: str) -> None:
        if not text:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def run_current(self) -> None:
        command = self.command.text().strip()
        if not command:
            return
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stateChanged.emit("A terminal command is already running.")
            return
        self.command.clear()
        self.append(f"\n{Path(self.workspace).name}> {command}\n")
        argv: tuple[str, ...]
        parser = getattr(self.desktop_core, "parse_terminal_command", None) if self.desktop_core else None
        try:
            if callable(parser):
                interpreter = "powershell" if os.name == "nt" else "sh"
                argv = tuple(parser(command, interpreter=interpreter))
            elif os.name == "nt":
                argv = ("powershell", "-NoLogo", "-NoProfile", "-Command", command)
            else:
                argv = ("/bin/sh", "-lc", command)
        except Exception as exc:
            self.append(f"Could not parse command: {exc}\n")
            return
        if not argv:
            self.append("No command to run.\n")
            return
        self.process.setWorkingDirectory(self.workspace)
        self.process.start(argv[0], list(argv[1:]))
        self.stateChanged.emit(f"Running {command}")

    def _sync_process_state(self, state: QProcess.ProcessState) -> None:
        running = state != QProcess.ProcessState.NotRunning
        self.stop_button.setEnabled(running)
        self.command.setEnabled(not running)

    def stop(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop_button.setEnabled(False)
            self.stateChanged.emit("Stopping terminal command…")
            self.process.terminate()
            QTimer.singleShot(1200, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append(ANSI_ESCAPE.sub("", text))

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        label = "finished" if status == QProcess.ExitStatus.NormalExit else "crashed"
        self.append(f"\n[command {label}, exit {exit_code}]\n")
        self.stateChanged.emit(f"Terminal command {label} with exit code {exit_code}.")

    def _error(self, error: QProcess.ProcessError) -> None:
        del error
        self.append(f"\nCould not start command: {self.process.errorString()}\n")
        self.stateChanged.emit("The terminal command could not start.")


class InspectorPane(QWidget):
    refreshRequested = Signal()
    revertRequested = Signal(list)
    hunksRequested = Signal(str)
    undoRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self.setMinimumWidth(250)
        self.setMaximumWidth(390)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 5, 10, 10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName("Workspace inspector")
        outer.addWidget(self.tabs)

        self.context_tab = QWidget()
        context_layout = QVBoxLayout(self.context_tab)
        context_layout.setContentsMargins(2, 12, 2, 3)
        context_layout.setSpacing(10)
        self.agent_card = QFrame()
        self.agent_card.setObjectName("card")
        agent_layout = QVBoxLayout(self.agent_card)
        agent_layout.setContentsMargins(10, 9, 10, 9)
        self.agent_title = QLabel("Repository guidance")
        self.agent_title.setObjectName("title")
        self.agent_state = QLabel("No AGENTS.md observed")
        self.agent_state.setObjectName("meta")
        self.agent_state.setWordWrap(True)
        agent_layout.addWidget(self.agent_title)
        agent_layout.addWidget(self.agent_state)
        context_layout.addWidget(self.agent_card)

        tree_header = QHBoxLayout()
        files_title = QLabel("PROJECT FILES")
        files_title.setObjectName("sectionTitle")
        self.context_count = QLabel("0 selected")
        self.context_count.setObjectName("meta")
        tree_header.addWidget(files_title)
        tree_header.addStretch(1)
        tree_header.addWidget(self.context_count)
        context_layout.addLayout(tree_header)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAccessibleName("Project files for context")
        self.file_tree.itemChanged.connect(self._context_selection_changed)
        context_layout.addWidget(self.file_tree, 1)

        privacy = QFrame()
        privacy.setObjectName("card")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(10, 9, 10, 9)
        privacy_title = QHBoxLayout()
        p_title = QLabel("Privacy boundary")
        p_title.setObjectName("title")
        self.privacy_state = QLabel("Local inference")
        self.privacy_state.setObjectName("success")
        privacy_title.addWidget(p_title)
        privacy_title.addStretch(1)
        privacy_title.addWidget(self.privacy_state)
        privacy_layout.addLayout(privacy_title)
        privacy_copy = QLabel(
            "Prompts and model inference stay on this machine. Anything that leaves it goes over Tor: Chat search, and the agent, which verifies a circuit before it starts and refuses to run without one."
        )
        privacy_copy.setObjectName("meta")
        privacy_copy.setWordWrap(True)
        privacy_layout.addWidget(privacy_copy)
        context_layout.addWidget(privacy)

        self.changes_tab = QWidget()
        changes_layout = QVBoxLayout(self.changes_tab)
        changes_layout.setContentsMargins(2, 10, 2, 3)
        changes_header = QHBoxLayout()
        self.change_summary = QLabel("No observed changes")
        self.change_summary.setObjectName("title")
        refresh = QToolButton()
        refresh.setObjectName("bareButton")
        _themed_icon(refresh, "refresh")
        refresh.setToolTip("Refresh observed Git changes")
        refresh.setAccessibleName("Refresh Git changes")
        refresh.clicked.connect(self.refreshRequested)
        refresh.setEnabled(False)
        self.refresh_button = refresh
        changes_header.addWidget(self.change_summary)
        changes_header.addStretch(1)
        changes_header.addWidget(refresh)
        changes_layout.addLayout(changes_header)
        self.change_list = QListWidget()
        self.change_list.setMaximumHeight(175)
        self.change_list.setAccessibleName("Observed Git changes")
        changes_layout.addWidget(self.change_list)
        # v2 undo: per-file revert, Revert all, and an Undo bar - git on disk.
        self.undo_enabled = False
        self.undo_note = QLabel("Read from git on disk. Reverting restores a file to its last commit.")
        self.undo_note.setObjectName("meta")
        self.undo_note.setWordWrap(True)
        self.undo_note.hide()
        changes_layout.addWidget(self.undo_note)
        self.revert_all_button = QPushButton("Revert all changes")
        self.revert_all_button.setObjectName("dangerButton")
        self.revert_all_button.setAccessibleName("Revert every changed file to its last commit")
        self.revert_all_button.clicked.connect(lambda: self.revertRequested.emit(list(self._change_paths)))
        self.revert_all_button.hide()
        changes_layout.addWidget(self.revert_all_button)
        self.undo_bar = QFrame()
        self.undo_bar.setObjectName("banner")
        undo_layout = QHBoxLayout(self.undo_bar)
        undo_layout.setContentsMargins(9, 5, 9, 5)
        self.undo_label = QLabel("")
        self.undo_label.setWordWrap(True)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setAccessibleName("Undo the revert and put the changes back on disk")
        self.undo_button.clicked.connect(self.undoRequested)
        undo_layout.addWidget(self.undo_label, 1)
        undo_layout.addWidget(self.undo_button)
        self.undo_bar.hide()
        changes_layout.addWidget(self.undo_bar)
        self._change_paths: list[str] = []
        self.empty_changes_note = ""
        diff_title = QLabel("DIFF")
        diff_title.setObjectName("sectionTitle")
        changes_layout.addWidget(diff_title)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("diffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setAccessibleName("Git diff")
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        changes_layout.addWidget(self.diff_view, 1)

        self.activity_tab = QWidget()
        activity_layout = QVBoxLayout(self.activity_tab)
        activity_layout.setContentsMargins(2, 10, 2, 3)
        activity_copy = QLabel("Observed application events. Agent output is not treated as proof of a file change.")
        activity_copy.setObjectName("meta")
        activity_copy.setWordWrap(True)
        activity_layout.addWidget(activity_copy)
        self.activity = QListWidget()
        self.activity.setAccessibleName("Activity history")
        activity_layout.addWidget(self.activity, 1)
        # v2 zones: the same events in Machine / Tor / Outside lanes.
        self._activity_entries: list[tuple[str, str, str]] = []
        self.lanes = QWidget()
        lanes_layout = QVBoxLayout(self.lanes)
        lanes_layout.setContentsMargins(0, 0, 0, 0)
        lanes_layout.setSpacing(4)
        self.lane_lists: dict[str, QListWidget] = {}
        self.lane_titles: dict[str, QLabel] = {}
        for zone, title in (("machine", "MACHINE"), ("tor", "TOR"), ("outside", "OUTSIDE")):
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            lane = QListWidget()
            lane.setAccessibleName(f"{title.title()} activity")
            lanes_layout.addWidget(heading)
            lanes_layout.addWidget(lane, 2 if zone == "machine" else 1)
            self.lane_lists[zone] = lane
            self.lane_titles[zone] = heading
        self.lanes.hide()
        activity_layout.addWidget(self.lanes, 1)
        # v2 netlog: export what left this machine.
        self.netlog_button = QPushButton("Export network log")
        self.netlog_button.setAccessibleName("Export the network log as JSON or CSV")
        self.netlog_button.hide()
        activity_layout.addWidget(self.netlog_button)

        self.tabs.addTab(self.context_tab, "Context")
        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.activity_tab, "Activity")
        # v2 runPanel: built always, shown as a fourth tab only while the
        # feature is on.
        self.run_panel = RunPanel()

    def set_run_tab(self, visible: bool) -> None:
        index = self.tabs.indexOf(self.run_panel)
        if visible and index < 0:
            self.tabs.addTab(self.run_panel, "Run")
        elif not visible and index >= 0:
            self.tabs.removeTab(index)
        # Four tabs have to fit the 292px inspector; three keep the original spacing.
        self.tabs.tabBar().setStyleSheet(
            "QTabBar::tab { padding: 12px 9px 10px 9px; }" if visible else "")

    def show_run_tab(self) -> None:
        if self.tabs.indexOf(self.run_panel) >= 0:
            self.tabs.setCurrentWidget(self.run_panel)

    _OUTSIDE = re.compile(r"Tor search|search over Tor|over Tor|DuckDuckGo|direct(ly)? download|"
                          r"model download|downloaded|update check|model list|query|search allowed", re.IGNORECASE)
    _TOR = re.compile(r"\bTor\b|circuit|SOCKS|proxy", re.IGNORECASE)

    @classmethod
    def zone_of(cls, text: str) -> str:
        """Machine / Tor / Outside for an event that did not name its zone."""
        if cls._OUTSIDE.search(text):
            return "outside"
        if cls._TOR.search(text):
            return "tor"
        return "machine"

    def append_activity(self, text: str, zone: Optional[str] = None) -> None:
        stamp = datetime.now().strftime("%H:%M")
        clean = _brand_runtime_text(text)
        self.activity.insertItem(0, QListWidgetItem(f"{stamp}  {clean}"))
        while self.activity.count() > 150:
            self.activity.takeItem(self.activity.count() - 1)
        lane = zone if zone in ("machine", "tor", "outside") else self.zone_of(clean)
        self._activity_entries.insert(0, (stamp, clean, lane))
        del self._activity_entries[150:]
        target = self.lane_lists[lane]
        target.insertItem(0, QListWidgetItem(f"{stamp}  {clean}"))
        while target.count() > 150:
            target.takeItem(target.count() - 1)
        self._sync_lane_titles()

    def _sync_lane_titles(self) -> None:
        for zone, title in (("machine", "MACHINE"), ("tor", "TOR"), ("outside", "OUTSIDE")):
            self.lane_titles[zone].setText(f"{title} · {self.lane_lists[zone].count()}")

    def set_zones(self, on: bool) -> None:
        self.activity.setVisible(not on)
        self.lanes.setVisible(on)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.refresh_button.setEnabled(bool(snapshot.get("root")))
        agents = snapshot.get("agents_files") or []
        if agents:
            self.agent_title.setText(Path(agents[0]).name)
            self.agent_state.setText(f"Loaded from {agents[0]}" + (f" and {len(agents) - 1} more" if len(agents) > 1 else ""))
        else:
            self.agent_title.setText("Repository guidance")
            self.agent_state.setText("No AGENTS.md observed in the inspected tree")
        self.populate_tree(snapshot.get("file_tree") or [], bool(snapshot.get("tree_truncated")))
        changes = snapshot.get("changes") or []
        self.change_list.clear()
        self._change_paths = [_as_text(change.get("path", "")) for change in changes]
        revertable = self.undo_enabled and bool(snapshot.get("is_git"))
        for change in changes:
            status = _as_text(change.get("status", "?"))
            path = _as_text(change.get("path", ""))
            original = change.get("original_path")
            text = f"{status:>2}  {path}" + (f"  from {original}" if original else "")
            item = QListWidgetItem(text)
            self.change_list.addItem(item)
            if revertable:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 0, 2, 0)
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                revert = QToolButton()
                revert.setObjectName("bareButton")
                _themed_icon(revert, "revert", 15)
                revert.setToolTip(f"Revert {path} to its last commit")
                revert.setAccessibleName(f"Revert {path}")
                revert.clicked.connect(lambda _checked=False, p=path: self.revertRequested.emit([p]))
                row_layout.addWidget(label, 1)
                row_layout.addWidget(revert)
                if "M" in status and not set(status) & set("ADRCU?"):
                    # Hunks only exist for a file that is in HEAD and still on disk.
                    parts = QToolButton()
                    parts.setObjectName("bareButton")
                    parts.setText("Parts…")
                    parts.setToolTip(f"Choose which changes in {path} to revert")
                    parts.setAccessibleName(f"Revert parts of {path}")
                    parts.clicked.connect(lambda _checked=False, p=path: self.hunksRequested.emit(p))
                    row_layout.insertWidget(1, parts)    # before the revert icon

                self.change_list.setItemWidget(item, row)
        self.undo_note.setVisible(revertable)
        self.revert_all_button.setVisible(revertable and len(changes) > 1)
        if changes:
            self.change_summary.setText(snapshot.get("summary") or f"{len(changes)} observed change(s)")
        else:
            self.change_summary.setText("Working tree clean" if snapshot.get("is_git") else "Not a Git repository")
        diff = snapshot.get("diff") or "No diff to display."
        if not changes and self.empty_changes_note and snapshot.get("is_git"):
            diff = self.empty_changes_note
        self.diff_view.setPlainText(diff)

    def populate_tree(self, paths: list[str], truncated: bool = False) -> None:
        self.file_tree.blockSignals(True)
        self.file_tree.clear()
        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for raw in paths:
            slash_path = raw.replace("\\", "/")
            directory_entry = slash_path.endswith("/")
            normalized = slash_path.strip("/")
            if not normalized or normalized == ".":
                continue
            parts = tuple(part for part in normalized.split("/") if part)
            parent: Optional[QTreeWidgetItem] = None
            for index, part in enumerate(parts):
                key = parts[: index + 1]
                node = nodes.get(key)
                if node is None:
                    node = QTreeWidgetItem(parent if parent is not None else self.file_tree, [part])
                    nodes[key] = node
                    if index == len(parts) - 1 and not directory_entry:
                        node.setData(0, Qt.ItemDataRole.UserRole, normalized)
                        node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        node.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        node.setData(0, _ICON_ROLE, "folder:18")
                        node.setIcon(0, _icon("folder"))
                parent = node
        self.file_tree.blockSignals(False)
        self.file_tree.expandToDepth(0)
        self.context_count.setText("0 selected" + (" · tree limited" if truncated else ""))

    def selected_context_files(self) -> list[str]:
        selected: list[str] = []
        iterator = self.file_tree.invisibleRootItem()

        def walk(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                child = parent.child(index)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path and child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(_as_text(path))
                walk(child)

        walk(iterator)
        return selected

    def _context_selection_changed(self, item: QTreeWidgetItem, column: int) -> None:
        del item, column
        count = len(self.selected_context_files())
        self.context_count.setText(f"{count} selected")
