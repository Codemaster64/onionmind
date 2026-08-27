---
version: 1
slug: "onionmind-desktop-py"
primary_target: "onionmind_desktop.py"
related_targets: []
---

# Onionmind desktop workbench

## Surface

- Target: `onionmind_desktop.py`
- Mode: Operate
- Audience: developers working in a local repository
- Job: converse with an Onionmind model, hand repository-changing work to Onionmind Agent, run project commands, and review context and git changes in one native application
- Primary action: describe a task in the composer and send it in Chat or Agent mode

## Chosen direction

Approved comp: `.impeccable/mocks/onionmind-workbench-a.png`

The balanced workbench keeps the transcript primary, with projects and sessions on the left and context, changes, and activity on the right. A compact terminal drawer and multiline composer complete the same task surface. The visual world is matte warm graphite, fine seams, soft bone text, and restrained Onionmind aubergine selection—not neon “AI terminal” styling.

## Composition commitments

| Visible ingredient | Commitment | Implementation medium |
| --- | --- | --- |
| Native top toolbar | Repository, branch, one Onionmind readiness state, and Tor state stay visible without exposing backend selectors | Qt Widgets layouts and controls |
| Project/session rail | 224px default; New task, Open folder, recent sessions, Models and Settings | Qt Widgets list views and buttons |
| Agent transcript | Largest region; literal, selectable user/assistant turns plus grouped tool and status rows | Accessible Qt plain-text labels and authored activity rows |
| Inspector | 292px default with Context, Changes, Activity; changes show real git data | Qt tab widget, lists, and monospace diff viewer |
| Terminal drawer | Compact command runner scoped to the selected workspace; output remains selectable | `QProcess` plus a monospace plain-text view |
| Composer | Multiline, attachment affordance, Chat/Agent, permission disclosure, model, Stop/Send | Qt input and standard controls |
| Permission boundary | Onionmind inference, Tor search, and Agent project-edit access are separate status facts; Agent shell and web tools are disabled | Semantic labels and status indicators |
| Onionmind identity | Existing onion icon, product name, and named model tiers | Existing `.ico`/SVG assets and text |

## Interaction and responsive behavior

- Keyboard first: Ctrl+N new task, Ctrl+O open folder, Ctrl+L focus composer, Ctrl+` toggle terminal, Ctrl+Shift+I toggle inspector, Escape stops an active run.
- At narrow widths the inspector collapses first, then the left rail; both remain reachable from toolbar actions.
- Chat mode uses the existing local streaming engine and Tor search tool. Agent mode starts in the selected workspace with automatic file-edit approval and no shell or web tools. Missing prerequisites and stopped runs are reported plainly in Onionmind product language.
- Git and project inspection are read-only. Onionmind never claims a change until it observes it on disk.

## Module seams

- `SessionStore` hides durable local session/settings persistence behind create, load, save, and list behavior.
- `WorkspaceInspector` hides project file enumeration and git status/diff behind snapshot operations.
- `AgentSpec` and `AgentBridge` hide the pinned coding Adapter, loopback endpoint, permission policy, and process lifecycle behind preflight, start, cancel, output, and completion events.
- The native window adapts the existing `onionmind.py` Ollama/Tor functions into daemon background jobs and UI-safe events.

## Unresolved decisions

- Nuitka produces the standalone Qt bundle. Code signing remains separate release work.
