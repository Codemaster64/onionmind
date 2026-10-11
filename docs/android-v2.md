# Workbench v2 on Android: what fits a phone

The desktop workbench gained 24 optional features (see
[TECHNICAL.md → Workbench v2 features](../TECHNICAL.md#workbench-v2-features) and
[v2-gaps.md](v2-gaps.md)). The Android app is a different product: one chat,
one model at a time through `llama-server`, and a per-turn "Allow Tor search"
box. It has no coding agent, no projects, no git, no Ollama, no hardware
keyboard and one conversation. This file goes through all 24 features: which
ones were built for the phone, how they were adapted, and why the rest stay on
the desktop.

The rules are the same as on the desktop:

- **Same keys, same default.** The phone uses the desktop's flag names, and
  every flag starts off except **webAsk**. With the flags off the app looks
  and behaves as before, in the Nocturne palette.
- **Real data only.** Every number on screen comes from the program: llama-server's own
  `timings` and `usage`, `ActivityManager` memory, the real search results.
  When the server does not report something, nothing is shown. Nothing is
  estimated without saying so.
- **The privacy boundary does not move.** No new network path. Search is
  still Tor-only, still per turn, still fail-closed. No feature can turn
  search on by itself: webAsk only asks, and your answer is that turn's
  permission.

## The 24 features

| Feature | On Android | What it does on the phone, and why |
|---|---|---|
| **webAsk** | Built (on by default) | When a message looks like it needs current information and the box is not ticked, a card asks "Search over Tor?" with *Search over Tor* / *Answer without searching*. The pattern is the desktop's `WEB_ASK_PATTERN`, word for word; a core test keeps the page, the Kotlin core and the desktop identical. Ticking the box skips the card. |
| **provenance** | Built | Under each reply: "On this phone · SPARK", then "Tor search · 5 results · mozilla.org, …", "No web search" or "Tor search failed". The counts and domains come from the parsed onion results, not the model's text. |
| **thinking** | Adapted | "Reasoning hidden" when the model produced reasoning that the filter removed, and the turn's real duration. The desktop says "Thought for Ns"; the phone does not stream, so the reasoning time can't be measured on its own, and the line gives the honest total instead. |
| **modelPlus** | Adapted | One model at a time, so there is no model per mode. The live-stats half is built: the last reply's speed (llama-server `predicted_per_second`) and the phone's free RAM in a status line. |
| **contextMeter** | Built | "context 3.1k / 16k" with a bar. The figure is llama-server's own prompt and completion count after each reply, shown as "≈" (characters ÷ 4) only until the first reply. It warns at 80%: a full context means the model can't answer, so clear the conversation. |
| **ladder** | Adapted | The model list labels each model Fits / Tight / Too big, from the real file size against the phone's RAM. It is an estimate and says so: 60% of RAM, which reproduces the existing recommendation. Switching models loads the new one at once, and the model dot says "loading" while it does. There is no load percentage, because llama-server reports none. |
| **boundary** | Built | A "Map" sheet with three zones: on this phone (inference, the conversation, settings), over Tor (search, Tor state, today's count) and direct (model downloads over HTTPS, today's count). The direct zone matters most on Android, because downloads are the one place the phone's address is visible. |
| **netlog** | Built | While netlog or boundary is on, every connection that leaves the phone is logged (`filesDir/egress.jsonl`): time, route, host and kind. That covers the onion search ("5 results"), starting Tor, and model downloads (host and model name). It never records the query text or a URL path, and keeps the last 1000 entries. Settings shares the log as JSON or CSV through Android's share sheet, or clears it. |
| **msgActions** | Built (no branch) | "edit" on your messages puts the text back in the composer and drops that turn and everything after it. "ask again" on a reply sends its question again. Asking again never reuses a past search permission. Branching needs more than one conversation, so it is not built. |
| **presets** | Built | Default, Concise, Explain step by step and Reviewer, as chips above the composer. The system prompt is added on the server for that request only and never saved into the conversation. With the flag off the request is byte-for-byte what it was. There is no preset editor yet. |
| **errors** | Built | Cards with fixes that really exist. **The model could not start** → *Try again* (restarts it). **Out of memory** comes from llama-server's own log, because the chat only sees the server vanish → *Use \<lighter installed model\> and retry*, or *Models*. **A Tor search failed** → *Search again* (fresh circuit) or *Keep this answer*. |
| **empty** | Built | Starter prompts on an empty conversation. They fill the composer and never send. One of them shows webAsk. |
| **lock** | Adapted | **Auto-delete** removes the conversation after 1, 7 or 30 days without a new message, before it is shown again. **Private screen** sets `FLAG_SECURE`, so the conversation never shows in the app switcher or in screenshots. The desktop's passphrase lock is left out: the phone's own screen lock does that job better. |
| **appearance** | Adapted | Light, dark or follow system, in the Nocturne light and dark palettes. Text size was already a setting. Notifications are left out: a reply only arrives while the app is open, and Android 13+ would need a new permission prompt. |
| **setup** | Already there | The app has always had a first-run model picker by RAM, with download progress. |
| **approvals** | Not on Android | The Agent's protected actions. There is no coding agent on the phone. |
| **runPanel** | Not on Android | The Agent's Run tab. No agent. |
| **timeline** | Not on Android | The Agent's tool steps. Chat's one tool, search, is already summed up by provenance. |
| **undo** | Not on Android | Reverting the Agent's file changes. No agent, no project files. |
| **rules** | Not on Android | Per-project Agent rules. No agent, no projects. |
| **zones** | Not on Android | Lanes in the desktop's Activity tab. The phone has no activity pane, and the boundary map shows the same three routes. |
| **sessionSearch** | Not yet | Needs more than one conversation. The phone keeps exactly one. Multiple conversations would be the next step, and branching and search would follow from it. |
| **palette** | Not on Android | A Ctrl+K command palette needs a keyboard and many destinations. A phone has neither. |
| **keyboard** | Not on Android | The shortcuts page. Enter-to-send is already a setting. |

**Result:** 14 features on the phone (9 built as on the desktop, 5 adapted),
setup already present, and 9 left on the desktop for the reasons above.

## Where the code is

- `android/core/.../Workbench.kt` (no Android imports, JVM-tested):
  - flags (`Features`)
  - `WebAsk`, `Presets`, `ErrorKind`, `ModelFit`, `ContextWindow`
  - `TurnStats` (per-reply numbers)
  - `EgressLog`
- `Agent.turn(..., stats)` fills `TurnStats` from llama-server's `usage`
  and `timings`, and `Agent.searchWeb` returns the hits behind the text the
  model reads. `ConversationStore.expire` handles auto-delete, and
  `WorkbenchPreferences` holds the theme, preset, auto-delete and private
  screen.
- `android/app/.../Workbench.kt` keeps the flags and preferences in private
  storage, writes the egress log only while it is enabled, and asks the
  activity for `FLAG_SECURE`.
- New token-gated endpoints in `Server.kt`: `/api/features`, `/api/netlog`
  and `/api/share` (the WebView has no `navigator.share`, so the app opens
  Android's share sheet). `/api/chat` now returns `stats`, `model` and an
  error `kind`, and `/api/status` adds the model loading state, free RAM, fit
  labels and today's counts.
- `android/app/src/main/assets/index.html` is the whole UI, restyled in Nocturne.

## How it was checked

- **Kotlin:** `./gradlew :core:test` passes 54 tests: the 40 existing ones,
  plus 14 for the flags, webAsk parity with the page and the desktop,
  presets, error sorting, fit, context figures, the egress log (capped, no
  request text), the preferences, auto-delete, and the context
  limit matching llama-server's `-c`. Two tests run a stub
  llama-server to check that the stats come from its `timings` and `usage`,
  and that missing numbers stay unknown. `:app:assembleDebug` builds.
- **The page:** driven in a phone-sized Chromium against a stand-in for
  `Server.kt` with the same routes. The run checks:
  - with only the defaults, nothing new shows
  - webAsk asks first, the permission lasts one turn, and a ticked box skips
    the card
  - Turn all on and Turn all off
  - provenance, thinking, the status line and the context meter all show
    the server's numbers
  - earlier replies keep their details
  - edit and ask again
  - the three error cards
  - the boundary map
  - the light theme
  - starters fill the composer and never send
  - fit labels
  - the page throws no errors

  Screenshots were taken of the default view, everything on in dark, the map,
  and light with an empty conversation.
- **Not verified: anything on a phone.** No device or emulator ran this
  build. Like the 1.4 APK, it is debug-signed and built by hand
  (`android/Dockerfile`, which also cross-compiles llama-server).
