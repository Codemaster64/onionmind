#!/usr/bin/env python3
"""Onionmind in the terminal - a Textual front end over the same core.

  python onionmind_tui.py                 # needs: pip install textual
  python onionmind_tui.py --model blaze   # start on a specific installed model

Inference, Tor and search stay in onionmind.py; sessions go through the
desktop's SessionStore (onionmind_desktop_core.py) in the desktop's data
folder, so a conversation started here is listed in the workbench too.
Opening the TUI starts no network service: Tor is only touched when a
listener is already up (to verify it) or when a turn is armed for search.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import Footer, Input, Label, ListItem, ListView, Markdown, OptionList, Static
from textual.widgets.option_list import Option

import onionmind_desktop_core as desktop_core

AI_NOTICE = ("You are talking to an AI. It can be wrong; you are responsible "
             "for what you do with the output.")
SLASH_COMMANDS = ["/new", "/search ", "/model", "/save ", "/title ", "/delete", "/help", "/quit"]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Nocturne, straight from DESIGN.md.
NOCTURNE = Theme(
    name="nocturne",
    primary="#9184d9",
    secondary="#5d5294",
    accent="#b5abfc",
    foreground="#e9e9ed",
    background="#161826",
    surface="#232532",
    panel="#1c1e2c",
    success="#74c692",
    warning="#e3b667",
    error="#eb8373",
    dark=True,
    variables={
        "text-muted": "#9397ab",
        "footer-key-foreground": "#b5abfc",
        "footer-background": "#1c1e2c",
        "input-selection-background": "#423a6a",
        "block-cursor-background": "#423a6a",
        "block-cursor-foreground": "#f3f5fe",
    },
)
NOCTURNE_LIGHT = Theme(
    name="nocturne-light",
    primary="#6b5fc0",
    secondary="#796cbf",
    accent="#5d5294",
    foreground="#1d1f2b",
    background="#f2f3f8",
    surface="#fbfbfe",
    panel="#e4e7f5",
    success="#2f8a55",
    warning="#a8741c",
    error="#c2493a",
    dark=False,
    variables={"text-muted": "#595d6c", "block-cursor-background": "#d2cefd",
               "block-cursor-foreground": "#1d1f2b"},
)

# A bulb with one inner layer and roots; every row is 17 cells wide.
ONION = [
    "        ╷        ",
    "       ╱ ╲       ",
    "     ╭╯╱ ╲╰╮     ",
    "   ╭─╯╱   ╲╰─╮   ",
    "  ╱  ╱  ◉  ╲  ╲  ",
    " │  │       │  │ ",
    "  ╲  ╲     ╱  ╱  ",
    "   ╰─╮╰───╯╭─╯   ",
    "     ╰─┬┬┬─╯     ",
    "       ╱│╲       ",
]
# Light at the tip, deepening toward the roots (DESIGN.md accent ramp).
_ROW_COLOURS = ["#d2cefd", "#d2cefd", "#b5abfc", "#b5abfc", "#968ae0",
                "#968ae0", "#796cbf", "#5d5294", "#5d5294", "#595d6c"]


def onion_logo() -> Text:
    """The logo as rich text, with the wordmark under it."""
    art = Text(justify="center")
    for row, colour in zip(ONION, _ROW_COLOURS):
        for char in row:
            art.append(char, style="bold #f5f4ff" if char == "◉" else colour)
        art.append("\n")
    art.append("\n")
    art.append("o n i o n ", style="bold #e9e9ed")
    art.append("m i n d", style="bold #b5abfc")
    return art


def default_data_root() -> Path:
    """Where the desktop keeps its data (QStandardPaths' AppDataLocation for
    org "Onionmind", app "OnionmindDesktop"), so both front ends share sessions.

    ponytail: mirrors Qt's rules by hand to keep Qt out of the TUI; verified on
    Linux only. If Qt's Windows/macOS layout ever differs, sessions simply start
    a separate list - nothing is lost.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "Onionmind" / "OnionmindDesktop" / "desktop"


def session_title(text: str, limit: int = 48) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def relative_time(stamp: str) -> str:
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    for size, unit in ((86400 * 7, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def stats_line(stats: dict) -> str:
    parts = []
    if stats.get("tokens_per_second"):
        parts.append(f"{stats['tokens_per_second']:.1f} tok/s")
    if stats.get("eval_count"):
        parts.append(f"{stats['eval_count']} tok")
    if stats.get("total_duration"):
        parts.append(f"{stats['total_duration'] / 1e9:.1f}s")
    if stats.get("reasoning_seconds"):
        parts.append(f"thought {stats['reasoning_seconds']:.1f}s")
    return " · ".join(parts)


def model_label(raw_id: str, short: bool = True) -> str:
    """The model's real name; short drops the weight class for tight spots."""
    try:
        name = desktop_core.describe_model(raw_id).display_name
    except ValueError:
        return raw_id or "no model"
    return name.split(" · ")[0] if short else name


# ── transcript pieces ─────────────────────────────────────────────────────────

class UserMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(Text(text), classes="user")
        self.border_title = "you"


class Note(Static):
    """A quiet one-line event: tool activity, model switches, errors."""

    def __init__(self, text: str, kind: str = "info") -> None:
        super().__init__(Text(text), classes=f"note {kind}")


class BotMessage(Vertical):
    def __init__(self, text: str = "", meta: str = "", pending: bool = False) -> None:
        super().__init__(classes="bot")
        self._text, self._meta, self._pending = text, meta, pending
        self._frame, self._started, self._phase = 0, time.monotonic(), "Thinking"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="bot-head"):
            yield Label("◉ onionmind", classes="bot-name")
            yield Label(self._meta, classes="bot-meta")
        yield Static("", classes="thinking")
        yield Markdown(self._text, classes="bot-body")

    def on_mount(self) -> None:
        self.query_one(".thinking").display = self._pending
        self.query_one(".bot-body").display = not self._pending
        if self._pending:
            self._ticker = self.set_interval(1 / 12, self._spin)

    def _spin(self) -> None:
        self._frame = (self._frame + 1) % len(SPINNER)
        elapsed = time.monotonic() - self._started
        line = Text()
        line.append(SPINNER[self._frame] + " ", style="bold #b5abfc")
        line.append(self._phase, style="italic")
        line.append(f"  {elapsed:.0f}s", style="#75798c")
        thinking = self.query_one(".thinking", Static)
        # Once text shows, the spinner moves up next to the name.
        target = thinking if thinking.display else self.query_one(".bot-meta", Label)
        target.update(line)

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    async def show(self, text: str) -> None:
        body = self.query_one(".bot-body", Markdown)
        if text:
            self.query_one(".thinking").display = False
            body.display = True
            if self._phase == "Thinking":
                self._phase = "Writing"
        await body.update(text)

    def done(self, meta: str = "") -> None:
        if getattr(self, "_ticker", None):
            self._ticker.stop()
        self.query_one(".thinking").display = False
        self.query_one(".bot-body").display = True
        if meta:
            self.query_one(".bot-meta", Label).update(meta)


# ── modal screens ─────────────────────────────────────────────────────────────

class ModelPicker(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, models: list[dict], current: str, loaded: set[str]) -> None:
        super().__init__()
        self.models, self.current, self.loaded = models, current, loaded

    def compose(self) -> ComposeResult:
        with Vertical(id="picker", classes="dialog"):
            yield Label("Choose a model", classes="dialog-title")
            options = []
            for m in self.models:
                name = m["name"]
                row = Text()
                current = _plain(name) == _plain(self.current)
                row.append("● " if current else "  ", style="bold #74c692")
                row.append(f"{model_label(name, short=False)}\n", style="bold")
                row.append(f"  {name}", style="#9397ab")
                if m.get("size"):
                    row.append(f"  ·  {m['size'] / 1e9:.1f} GB", style="#9397ab")
                if _plain(name) in self.loaded:
                    row.append("  ·  in memory", style="#e3b667")
                options.append(Option(row, id=name))
            if not options:
                options.append(Option(Text("No models installed — run: ollama pull <name>",
                                           style="#9397ab"), id="", disabled=True))
            yield OptionList(*options, id="model-options")
            yield Label("↑↓ choose · enter use · esc close", classes="dialog-hint")

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "No"), Binding("n", "dismiss(False)", "No"),
                Binding("y", "dismiss(True)", "Yes")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog danger"):
            yield Label(self.title_text, classes="dialog-title")
            yield Static(self.body, classes="dialog-body")
            yield Label("y delete · n / esc keep", classes="dialog-hint")


HELP = """\
## Keys

| Key | Action |
|---|---|
| `enter` | send |
| `esc` | stop the running turn |
| `ctrl+s` | arm Tor search for the **next** turn only |
| `ctrl+o` | choose model |
| `ctrl+n` | new session |
| `ctrl+d` | delete this session |
| `ctrl+e` | export conversation to a text file |
| `ctrl+b` | show / hide sessions |
| `ctrl+t` | dark / light |
| `ctrl+p` | command palette |
| `ctrl+q` | quit |

## Commands

`/search <question>` · `/model [name]` · `/new` · `/title <name>` ·
`/save <file>` · `/delete` · `/quit`

## Privacy

Chat is **local-only** by default. Search is off unless you arm it for one
turn; it then goes to DuckDuckGo's onion service over a verified Tor circuit,
or not at all.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,f1,q", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide"):
            yield Markdown(HELP)
            yield Label("esc close", classes="dialog-hint")


class OnionCommands(Provider):
    """Everything the keys do, findable from ctrl+p."""

    def _commands(self):
        app = self.app
        yield "New session", app.action_new_session, "Start a fresh conversation"
        yield "Choose model", app.action_pick_model, "Switch the model for this session"
        yield "Arm Tor search for next turn", app.action_toggle_search, "One turn, then off"
        yield "Export conversation", app.action_export, "Write the transcript to a text file"
        yield "Delete session", app.action_delete_session, "Remove it from this machine"
        yield "Toggle sidebar", app.action_toggle_sidebar, "Show or hide sessions"
        yield "Toggle light / dark", app.action_toggle_dark, "Nocturne or Nocturne light"
        yield "Help", app.action_help, "Keys, commands and privacy"

    async def discover(self) -> Hits:
        for name, callback, help_text in self._commands():
            yield DiscoveryHit(name, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, callback, help_text in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)


def _plain(name: str) -> str:
    return name[:-7] if name.endswith(":latest") else name


# ── the app ───────────────────────────────────────────────────────────────────

class OnionmindTUI(App):
    TITLE = "onionmind"
    COMMANDS = App.COMMANDS | {OnionCommands}
    BINDINGS = [
        Binding("escape", "stop", "Stop", show=False),
        Binding("ctrl+s", "toggle_search", "Tor search", priority=True),
        Binding("ctrl+o", "pick_model", "Model", priority=True),
        Binding("ctrl+n", "new_session", "New", priority=True),
        Binding("ctrl+e", "export", "Export", priority=True),
        Binding("ctrl+d", "delete_session", "Delete", priority=True, show=False),
        Binding("ctrl+b", "toggle_sidebar", "Sessions", priority=True),
        Binding("ctrl+t", "toggle_dark", "Theme", priority=True, show=False),
        Binding("f1", "help", "Help"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    CSS = """
    Screen { background: $background; }

    #topbar { height: 1; background: $panel; padding: 0 1; }
    #brand { width: auto; text-style: bold; color: $accent; }
    #brand-sub { width: auto; color: $text-muted; margin-left: 1; }
    #topbar .grow { width: 1fr; }
    .pill { width: auto; padding: 0 1; margin-left: 1; color: $text-muted; }
    .pill:hover { background: $boost; }
    #model-pill { color: $foreground; }
    #tor-pill.ready { color: $success; }
    #tor-pill.checking { color: $warning; }
    #tor-pill.bad { color: $error; }
    #search-pill.armed { background: $secondary; color: $foreground; text-style: bold; }

    #body { height: 1fr; }

    #sidebar { width: 34; background: $surface; padding: 1 0 0 0; }
    #sidebar.hidden { display: none; }
    .side-title { color: $text-muted; text-style: bold; padding: 0 2; margin-bottom: 1; }
    #new-session { margin: 0 1 1 1; padding: 0 1; color: $accent; width: 1fr; }
    #new-session:hover { background: $boost; }
    #sessions { background: $surface; height: 1fr; padding: 0 1; }
    #sessions > ListItem { background: $surface; padding: 0 1; margin-bottom: 1; }
    #sessions > ListItem.current { border-left: outer $primary; }
    #sessions > ListItem.-highlight { background: $boost; }
    #sessions:focus > ListItem.-highlight { background: $secondary 50%; }
    .s-title { color: $foreground; }
    .s-meta { color: $text-muted; }
    #machine { height: auto; padding: 1 2; color: $text-muted; border-top: solid $panel; }

    #main { width: 1fr; }
    #transcript { height: 1fr; padding: 1 3 0 3; scrollbar-size-vertical: 1; }
    #transcript > * { max-width: 120; }

    #welcome-box { height: 1fr; align: center middle; }
    #welcome { width: 1fr; height: auto; text-align: center; }
    #welcome-tips { width: 1fr; height: auto; text-align: center; color: $text-muted; margin-top: 1; }

    .user { height: auto; margin: 1 0 0 10; padding: 0 1; border: round $secondary;
            border-title-color: $text-muted; border-title-align: right; background: $surface; }
    .bot { height: auto; margin: 1 10 0 0; padding: 0 1 0 2; border-left: thick $primary; }
    .bot-head { height: 1; }
    .bot-name { color: $accent; text-style: bold; width: auto; }
    .bot-meta { color: $text-muted; margin-left: 2; width: auto; }
    .thinking { height: 1; margin-top: 1; }
    .bot-body { margin: 0; padding: 0; background: transparent; }
    .bot-body MarkdownFence { margin: 1 0; }
    .note { height: auto; margin: 1 0 0 2; color: $text-muted; text-style: italic; }
    .note.tool { color: $accent; }
    .note.good { color: $success; }
    .note.bad { color: $error; text-style: none; }

    #composer { height: auto; padding: 0 3 0 3; background: $background; }
    #prompt { border: round $secondary; background: $surface; padding: 0 1; }
    #prompt:focus { border: round $primary; }
    #prompt.armed { border: round $warning; }
    #prompt.busy { border: round $panel; }
    #hint { height: 1; padding: 0 2; color: $text-muted; }
    #hint-right { width: auto; color: $text-muted; }
    #hint-left { width: 1fr; }

    ModalScreen { align: center middle; background: $background 70%; }
    .dialog { width: 68; height: auto; max-height: 80%; background: $surface;
              border: round $primary; padding: 1 2; }
    .dialog.wide { width: 76; }
    .dialog.danger { border: round $error; }
    .dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .dialog-body { margin-bottom: 1; }
    .dialog-hint { color: $text-muted; margin-top: 1; }
    #model-options { height: auto; max-height: 20; background: $surface; border: none; }
    #model-options > .option-list--option { padding: 0 1; }
    """

    def __init__(self, core: Any, data_root: Optional[Path] = None, model: Optional[str] = None) -> None:
        super().__init__()
        self.core = core
        self.store = desktop_core.SessionStore((data_root or default_data_root()) / "sessions")
        self.wanted_model = model
        self.session: Optional[desktop_core.ChatSession] = None
        self.models: list[dict] = []
        self.online = False
        self.busy = False
        self.search_armed = False
        self.stop_event = threading.Event()
        self._raw = ""
        self._shown = None
        self._live: Optional[BotMessage] = None
        self._turn_started = 0.0

    # layout ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("◉ onionmind", id="brand")
            yield Label("local-first workbench", id="brand-sub")
            yield Label("", classes="grow")
            yield Label("", id="model-pill", classes="pill")
            yield Label("", id="tor-pill", classes="pill")
            yield Label("", id="search-pill", classes="pill")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("SESSIONS", classes="side-title")
                yield Label("+ new session   ^N", id="new-session")
                yield ListView(id="sessions")
                yield Static("", id="machine")
            with Vertical(id="main"):
                yield VerticalScroll(id="transcript")
                with Vertical(id="composer"):
                    yield Input(placeholder="Ask anything…  ( / for commands )", id="prompt",
                                suggester=SuggestFromList(SLASH_COMMANDS, case_sensitive=False))
                    with Horizontal(id="hint"):
                        yield Label("", id="hint-left")
                        yield Label("", id="hint-right")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(NOCTURNE)
        self.register_theme(NOCTURNE_LIGHT)
        self.theme = "nocturne"
        self.set_interval(1 / 12, self._render_live)
        self.query_one("#transcript").anchor()
        self._paint_status()
        self._refresh_sessions()
        sessions = self.store.list()
        if sessions:
            self._open_session(sessions[0])
        else:
            self._show_welcome()
        self.query_one("#prompt").focus()
        self._start()

    # status ------------------------------------------------------------------

    def _paint_status(self, tor: Optional[str] = None) -> None:
        core = self.core
        model = self.query_one("#model-pill", Label)
        if self.online:
            model.update(Text.assemble(("▣ ", "#b5abfc"), model_label(core.MODEL)))
        else:
            model.update(Text("▣ no model service", style="#eb8373"))
        if tor is not None:
            pill = self.query_one("#tor-pill", Label)
            states = {"off": ("○ Tor off", ""), "checking": ("◌ Tor checking…", "checking"),
                      "ready": ("● Tor ready", "ready"), "bad": ("✗ Tor unverified", "bad")}
            label, cls = states[tor]
            pill.update(label)
            pill.set_classes(f"pill {cls}".strip())
        search = self.query_one("#search-pill", Label)
        search.update("⌕ search armed" if self.search_armed else "⌕ search off")
        search.set_class(self.search_armed, "armed")
        prompt = self.query_one("#prompt", Input)
        prompt.set_class(self.search_armed and not self.busy, "armed")
        prompt.set_class(self.busy, "busy")
        left = self.query_one("#hint-left", Label)
        if self.busy:
            left.update(Text("esc to stop", style="#e3b667"))
        elif self.search_armed:
            left.update(Text("next turn may search DuckDuckGo's onion service over Tor",
                             style="#e3b667"))
        else:
            left.update("local only · nothing leaves this machine")
        right = self.query_one("#hint-right", Label)
        count = sum(m.get("role") in ("user", "assistant") and bool(m.get("content"))
                    for m in self.session.messages) if self.session else 0
        right.update(f"{count} message{'s' * (count != 1)}" if self.session else "")

    def _paint_machine(self) -> None:
        lines = Text()
        lines.append("MACHINE\n", style="bold")
        backend = getattr(self.core, "BACKEND", None) or "offline"
        lines.append(f"backend  {backend}\n")
        lines.append(f"models   {len(self.models)} installed\n")
        try:
            vram = desktop_core.gpu_vram_mb()
            ram = desktop_core.machine_memory_mb()
        except Exception:
            vram = ram = None
        if vram:
            lines.append(f"vram     {desktop_core.format_gb(vram)} GB\n")
        if ram:
            lines.append(f"ram      {desktop_core.format_gb(ram)} GB")
        self.query_one("#machine", Static).update(lines)

    @work(thread=True, group="startup")
    def _start(self) -> None:
        core = self.core
        try:
            core.detect_backend()
            self.online = True
        except (Exception, SystemExit) as exc:
            self.call_from_thread(self._note, core.user_error(exc) if hasattr(core, "user_error")
                                  else str(exc), "bad")
        if self.online:
            self.models = core.installed_model_details() if core.BACKEND == "ollama" else []
            wanted = self.wanted_model or (self.session.model if self.session else None)
            names = {_plain(m["name"]) for m in self.models}
            if wanted and (_plain(wanted) in names or not names):
                core.MODEL = _plain(wanted)
        self.call_from_thread(self._paint_status)
        self.call_from_thread(self._paint_machine)
        # Verify Tor only if a listener already exists: launch never starts one.
        if core.tor_proxy_port() is None:
            self.call_from_thread(self._paint_status, "off")
            return
        self.call_from_thread(self._paint_status, "checking")
        try:
            core.tor_check()
            self.call_from_thread(self._paint_status, "ready")
        except (Exception, SystemExit):
            self.call_from_thread(self._paint_status, "bad")

    # sessions ----------------------------------------------------------------

    def _refresh_sessions(self) -> None:
        view = self.query_one("#sessions", ListView)
        view.clear()
        for s in self.store.list():
            item = ListItem(
                Label(Text(session_title(s.title, 26)), classes="s-title"),
                Label(f"{relative_time(s.updated_at)} · {_plain(s.model) or '—'}", classes="s-meta"),
                name=s.id,
            )
            if self.session and s.id == self.session.id:
                item.add_class("current")
            view.append(item)

    def _clear_transcript(self) -> VerticalScroll:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.remove_children()
        return transcript

    def _show_welcome(self) -> None:
        self.session = None
        transcript = self._clear_transcript()
        box = Vertical(id="welcome-box")
        transcript.mount(box)
        box.mount(Static(onion_logo(), id="welcome"))
        tips = Text()
        tips.append(AI_NOTICE + "\n\n", style="italic")
        tips.append("enter", style="bold #b5abfc"); tips.append(" send   ")
        tips.append("ctrl+s", style="bold #b5abfc"); tips.append(" Tor search, one turn   ")
        tips.append("ctrl+o", style="bold #b5abfc"); tips.append(" model   ")
        tips.append("ctrl+p", style="bold #b5abfc"); tips.append(" commands   ")
        tips.append("f1", style="bold #b5abfc"); tips.append(" help")
        box.mount(Static(tips, id="welcome-tips"))
        self._paint_status()
        self._refresh_sessions()

    def _open_session(self, session: desktop_core.ChatSession) -> None:
        self.session = session
        if session.model and self.online:
            names = {_plain(m["name"]) for m in self.models}
            if not names or _plain(session.model) in names:
                self.core.MODEL = _plain(session.model)
        transcript = self._clear_transcript()
        widgets = []
        for m in session.messages:
            role, content = m.get("role"), m.get("content")
            if role == "user":
                text = content if isinstance(content, str) else "[image]"
                widgets.append(UserMessage(text + (" [image]" if m.get("images") else "")))
            elif role == "assistant":
                for call in m.get("tool_calls") or []:
                    fn = call.get("function", {})
                    query = (fn.get("arguments") or {}).get("query", "")
                    widgets.append(Note(f"⌕ searched over Tor: {query}", "tool"))
                visible = self.core.strip_thinking(content or "")
                if visible:
                    widgets.append(BotMessage(visible))
        if not widgets:
            widgets.append(Note(f"New session · {model_label(self.core.MODEL)}"))
        transcript.mount_all(widgets)
        transcript.scroll_end(animate=False)
        self._paint_status()
        self._refresh_sessions()

    @on(ListView.Selected, "#sessions")
    def _session_chosen(self, event: ListView.Selected) -> None:
        if self.busy or not event.item.name:
            return
        session = self.store.load(event.item.name)
        if session:
            self._open_session(session)
            self.query_one("#prompt").focus()

    def on_click(self, event) -> None:
        target = getattr(event.widget, "id", None)
        actions = {"new-session": self.action_new_session, "model-pill": self.action_pick_model,
                   "search-pill": self.action_toggle_search}
        if target in actions:
            actions[target]()

    # sending -----------------------------------------------------------------

    @on(Input.Submitted, "#prompt")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.busy:
            return
        event.input.value = ""
        if text.startswith("/"):
            self._command(text)
        else:
            self._send(text, allow_search=self.search_armed)

    def _command(self, text: str) -> None:
        name, _, arg = text.partition(" ")
        arg = arg.strip()
        if name == "/search":
            if arg:
                self._send(arg, allow_search=True)
            else:
                self.action_toggle_search()
        elif name == "/model":
            if arg:
                self._use_model(arg)
            else:
                self.action_pick_model()
        elif name in ("/new", "/clear"):
            self.action_new_session()
        elif name == "/save":
            self._export(arg) if arg else self._note("usage: /save <file>", "bad")
        elif name == "/title" and arg and self.session:
            self.session.title = session_title(arg, 80)
            self.store.save(self.session)
            self._refresh_sessions()
        elif name == "/delete":
            self.action_delete_session()
        elif name == "/help":
            self.action_help()
        elif name in ("/quit", "/exit"):
            self.exit()
        else:
            self._note(f"unknown command {name} — f1 lists them", "bad")

    def _note(self, text: str, kind: str = "info") -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        for welcome in transcript.query("#welcome-box"):
            welcome.remove()
        transcript.mount(Note(text, kind))

    def _send(self, text: str, allow_search: bool) -> None:
        if not self.online:
            self._note("No model service is answering on this machine. Start Ollama "
                       "(or llama-server) and restart onionmind.", "bad")
            return
        if self.session is None:
            self.session = self.store.create(title=session_title(text), model=self.core.MODEL)
            self._clear_transcript()
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(UserMessage(text))
        self._live = BotMessage(pending=True)
        transcript.mount(self._live)
        transcript.anchor()
        self.search_armed = False
        self.busy = True
        self._raw, self._shown = "", None
        self._turn_started = time.monotonic()
        self.stop_event.clear()
        history = self.session.messages
        before = len(history)
        history.append({"role": "user", "content": text})
        self._paint_status()
        self._refresh_sessions()
        self._run_turn(history, before, text, allow_search, self._live)

    @work(thread=True, exclusive=True, group="turn")
    def _run_turn(self, history: list, before: int, text: str, allow_search: bool,
                  bot: BotMessage) -> None:
        core = self.core
        stats: dict = {}

        def on_text(chunk: str) -> None:
            self._raw += chunk

        def on_event(event: dict) -> None:
            kind = event.get("kind")
            if kind == "stats":
                stats.update(event)
            elif kind == "generating":
                phase = "Thinking" if event.get("reasoning") else "Writing"
                if event.get("seconds"):              # Ollama streams a token per chunk
                    phase += f" · {event['chunks'] / event['seconds']:.0f} tok/s"
                bot.set_phase(phase)
            else:
                self.call_from_thread(self._core_event, event)

        answer, error = None, None
        try:
            if allow_search:
                self.call_from_thread(self._paint_status, "checking")
                core.start_tor_hidden(stop_event=self.stop_event)
            answer = core.turn_stream(history, on_text, self.stop_event,
                                      on_event=on_event, allow_search=allow_search)
        except (Exception, SystemExit) as exc:
            error = core.user_error(exc) if hasattr(core, "user_error") else str(exc)
            del history[before:]                     # a failed turn leaves no half-history
        self.call_from_thread(self._finish_turn, bot, answer, error, stats, text)

    def _core_event(self, event: dict) -> None:
        kind, name = event.get("kind"), event.get("name", "")
        if kind == "tool_started":
            self._raw = ""                           # the next round streams fresh text
            query = (event.get("arguments") or {}).get("query", "")
            self._live.set_phase("Searching")
            self._note_before_live(f"⌕ searching over Tor: {query}", "tool")
        elif kind == "tool_refused":
            self._note_before_live(f"✗ model asked to use {name}; search was not armed", "bad")
        elif kind == "tor_verified":
            self._paint_status("ready")
            self._note_before_live("● Tor circuit verified", "good")
        elif kind == "tool_finished" and name == "web_search":
            domains = ", ".join(event.get("domains") or []) or "no domains"
            self._note_before_live(f"✓ {event.get('results', 0)} results · {domains}", "tool")

    def _note_before_live(self, text: str, kind: str) -> None:
        self.query_one("#transcript").mount(Note(text, kind), before=self._live)

    async def _render_live(self) -> None:
        if self._live is None or self._raw == self._shown:
            return
        self._shown = self._raw
        visible = self.core.strip_thinking(self._raw)   # fails closed on half tags
        if visible:
            await self._live.show(visible)

    async def _finish_turn(self, bot: BotMessage, answer: Optional[str], error: Optional[str],
                           stats: dict, text: str) -> None:
        self._live = None
        self.busy = False
        meta = stats_line(stats) or f"{time.monotonic() - self._turn_started:.1f}s"
        if error:
            await bot.remove()
            self._note(f"✗ {error}", "bad")
            prompt = self.query_one("#prompt", Input)
            if not prompt.value:
                prompt.value = text                  # give the question back
        else:
            await bot.show(answer or "")
            bot.done(f"{model_label(self.core.MODEL)} · {meta}")
            self.session.model = self.core.MODEL
            self.store.save(self.session)
        tor = "ready" if getattr(self.core, "_port", None) else None
        self._paint_status(tor)
        self._refresh_sessions()

    # actions -----------------------------------------------------------------

    def action_stop(self) -> None:
        if self.busy:
            self.stop_event.set()
            if self._live:
                self._live.set_phase("Stopping")

    def action_toggle_search(self) -> None:
        if self.busy:
            return
        self.search_armed = not self.search_armed
        self._paint_status()

    def action_new_session(self) -> None:
        if not self.busy:
            self._show_welcome()
            self.query_one("#prompt").focus()

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("hidden")

    def action_toggle_dark(self) -> None:
        self.theme = "nocturne-light" if self.theme == "nocturne" else "nocturne"

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_pick_model(self) -> None:
        if self.busy:
            return
        if not self.online:
            self._note("No model service is running, so there is nothing to choose from.", "bad")
            return
        self._load_picker()

    @work(thread=True, exclusive=True, group="picker")
    def _load_picker(self) -> None:
        core = self.core
        if core.BACKEND == "ollama":
            self.models = core.installed_model_details()
        loaded = {_plain(m["name"]) for m in core.loaded_models()} if hasattr(core, "loaded_models") else set()
        self.call_from_thread(self.push_screen, ModelPicker(self.models, core.MODEL, loaded),
                              self._picked)

    def _picked(self, name: Optional[str]) -> None:
        if name:
            self._use_model(name)

    def _use_model(self, name: str) -> None:
        name = _plain(name.strip())
        if name == self.core.MODEL:
            return
        known = {_plain(m["name"]) for m in self.models}
        if known and name not in known:
            self._note(f"{name} is not installed. ollama pull {name}", "bad")
            return
        self.core.MODEL = name
        if self.session:
            self.session.model = name
            self.store.save(self.session)
        self._note(f"▣ now using {model_label(name)}", "tool")
        self._paint_status()
        self._paint_machine()
        self._refresh_sessions()

    def action_export(self) -> None:
        if not self.session or not self.session.messages:
            self._note("Nothing to export yet.", "bad")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        self._export(f"onionmind-{stamp}.txt")

    def _export(self, path: str) -> None:
        if not self.session:
            self._note("Nothing to export yet.", "bad")
            return
        target = os.path.abspath(os.path.expanduser(path))
        try:
            self.core._save(self.session.messages, target)
            self._note(f"✓ saved to {target}", "good")
        except OSError as exc:
            self._note(f"✗ could not save: {exc}", "bad")

    def action_delete_session(self) -> None:
        if self.busy or not self.session:
            return
        title = self.session.title

        def answer(yes: Optional[bool]) -> None:
            if yes and self.session:
                self.store.delete(self.session.id)
                self._show_welcome()
                self.notify(f"Deleted “{title}”", severity="warning")

        self.push_screen(Confirm("Delete session?",
                                 f"“{title}” will be removed from this machine. "
                                 "This cannot be undone."), answer)

    def on_unmount(self) -> None:
        self.stop_event.set()
        try:
            self.core.stop_managed_tor()              # only ours, never a reused proxy
        except Exception:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onionmind in the terminal.")
    parser.add_argument("--model", help="installed model to start with (raw name)")
    parser.add_argument("--data-dir", type=Path,
                        help="session folder root (default: the desktop's data folder)")
    args = parser.parse_args(argv)
    import onionmind as core
    OnionmindTUI(core, data_root=args.data_dir, model=args.model).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
