# Product

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

## Users

The primary user is a developer working in a local repository who wants a private coding assistant without sending prompts, source code, or model inference to a cloud service. The product also serves existing Onionmind users who want general chat and Tor-routed web search in the same desktop application.

## Product Purpose

Onionmind is a local AI workbench powered by models served on the user's machine. The desktop experience should make conversational work, repository context, model management, coding-agent handoff, command output, and review feel like one coherent program. Success means a user can open a project, understand the agent's current model and permissions, work through a task, and inspect what happened without assembling separate browser tabs.

## Positioning

Onionmind combines local Ollama inference with Tor-routed search and a DeepSeek Harness coding path. The Onionmind maintainers receive no chat content, source code, account data, or telemetry. Chat search sends its query to DuckDuckGo's onion service through Tor; model downloads, updates, and Harness network-capable tools have separately disclosed direct-network boundaries.

## Operating Context

Users run Onionmind on Windows or Linux, often beside a code editor and terminal, with Ollama or llama.cpp serving a local model. Coding sessions start in a chosen project folder, but Harness tools and approvals—not the working directory—govern their actual access. DeepSeek Harness is launched through Ollama by `onionmind.py --agent`, which verifies a Tor circuit before starting it and refuses to run without one; the agent's proxy and socket shims put everything it runs on Tor. Existing installs expose `onionmind` for the desktop workbench, `onionmind-code` for one-shot Harness work, and `onionmind-chat` as a compatibility alias for the workbench.

## Capabilities and Constraints

- Preserve the existing CLI, streaming chat, Ollama and llama.cpp backends, model discovery and download, image input, conversation export, stop control, and Tor search behavior.
- The desktop surface must be a standalone native program, not a hosted site or a browser-only wrapper.
- Repository-changing agent work is delegated to the public DeepSeek Harness integration; the UI must not imply a file was changed when it only chatted about it.
- Model display keeps Onionmind's named tiers: SPARK, EMBER, BLAZE, INFERNO, CINDER, WILDFIRE, FLASHPOINT, PHOENIX, NOVA, and PYRE. Backend identifiers remain available internally, while the normal UI uses Onionmind model names.
- The product UI names the coding path **Onionmind Agent**. Backend vendor names belong in technical/legal documentation and internal diagnostics, not ordinary interface copy.
- Installers provision the isolated PySide6 runtime and mark it ready only after an import check; if provisioning fails, the legacy local chat UI remains available as a fallback.
- Tor readiness and local model-server readiness are separate states and must be shown honestly.
- DeepSeek Harness is a developer-preview dependency and may not be installed yet; the app needs an actionable unavailable state.

## Brand Commitments

The product name is **Onionmind**. Keep the supplied onion logo and the existing fire-based model tier names. The workbench should sit alongside Claude Code and Codex in interaction quality and familiar coding-agent conventions while remaining visibly Onionmind, not presenting itself as either product.

## Evidence on Hand

- Product and feature claims: `README.md`, `TECHNICAL.md`, and `LEGAL.md`.
- Existing native desktop implementation and local inference/search core: `onionmind.py`.
- Brand assets: `logo.svg`, `logo-small.svg`, and `onionmind.ico`.
- DeepSeek Harness Tor integration: `dsh-onionmind-tor-search.js` and `dsh-onionmind-tor.patch.yml`.
- Install and update flows: `onionmind-setup.cmd`, `install-onionmind.ps1`, `install-onionmind.sh`, `update-onionmind.ps1`, and `update-onionmind.sh`.
- No customer, benchmark, or feature-parity claims beyond the repository evidence should be fabricated.

## Product Principles

1. Local work is legible: model, project, connection, permission, and activity states are always explicit.
2. Capabilities are real: every prominent control either works or explains exactly what must be installed or configured.
3. The agent earns trust through reviewable actions, clear boundaries, and interruptibility.
4. Privacy is described precisely; local inference and Tor-routed search are not conflated.
5. Familiar coding-agent patterns reduce friction, while Onionmind's identity and model vocabulary remain its own.

## Accessibility & Inclusion

The desktop UI must support full keyboard operation, visible focus, readable contrast, scalable system text, and status communication that does not rely on color alone.
