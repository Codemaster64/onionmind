---
name: "Onionmind Workbench"
description: "A calm native work loop for local inference, repository work, and honest review."
colors:
  graphite-window: "#181715"
  graphite-workspace: "#191816"
  graphite-panel: "#1c1b19"
  graphite-terminal: "#171615"
  graphite-raised: "#211f1d"
  graphite-card: "#23211f"
  graphite-tool: "#1e1d1b"
  graphite-control: "#24221f"
  charcoal-seam: "#403c36"
  charcoal-seam-subtle: "#37342f"
  bone: "#eee8df"
  bone-strong: "#f5efe7"
  bone-on-accent: "#faf6f0"
  ash: "#aaa39a"
  stone-icon: "#c9c1b7"
  aubergine: "#5c4566"
  aubergine-hover: "#684e73"
  aubergine-selection: "#3a3040"
  aubergine-mode: "#47364f"
  aubergine-on-selection: "#f5edf8"
  aubergine-focus: "#a481b4"
  status-good: "#78b889"
  status-warn: "#c9a36b"
  status-bad: "#d47d6b"
typography:
  brand:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "11pt"
    fontWeight: 650
    lineHeight: "normal"
    letterSpacing: "normal"
  title:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "1em"
    fontWeight: 650
    lineHeight: "normal"
    letterSpacing: "normal"
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "1em"
    fontWeight: 400
    lineHeight: "normal"
    letterSpacing: "normal"
  label:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "8.5pt"
    fontWeight: 650
    lineHeight: "normal"
    letterSpacing: "normal"
  mono:
    fontFamily: "'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace"
    fontSize: "9pt"
    fontWeight: 400
    lineHeight: "normal"
    letterSpacing: "normal"
rounded:
  compact: "3px"
  control: "4px"
  card: "5px"
  avatar: "16px"
spacing:
  xs: "4px"
  sm: "5px"
  md: "8px"
  lg: "10px"
  xl: "12px"
components:
  button-primary:
    backgroundColor: "{colors.aubergine}"
    textColor: "{colors.bone-on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "5px 17px"
  button-primary-hover:
    backgroundColor: "{colors.aubergine-hover}"
    textColor: "{colors.bone-on-accent}"
    rounded: "{rounded.control}"
    padding: "5px 17px"
  button-secondary:
    backgroundColor: "{colors.graphite-control}"
    textColor: "{colors.bone}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "5px 9px"
  composer:
    backgroundColor: "{colors.graphite-control}"
    textColor: "{colors.bone}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px"
  card:
    backgroundColor: "{colors.graphite-card}"
    textColor: "{colors.bone}"
    rounded: "{rounded.card}"
    padding: "9px 10px"
  tool-card:
    backgroundColor: "{colors.graphite-tool}"
    textColor: "{colors.bone}"
    rounded: "{rounded.card}"
    padding: "8px 11px"
  status-pill:
    backgroundColor: "{colors.graphite-raised}"
    textColor: "{colors.bone}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
  mode-selected:
    backgroundColor: "{colors.aubergine-mode}"
    textColor: "{colors.aubergine-on-selection}"
    rounded: "{rounded.control}"
    padding: "4px 16px"
  inspector-tabs:
    backgroundColor: "{colors.graphite-panel}"
    textColor: "{colors.ash}"
    typography: "{typography.body}"
    padding: "12px 16px 10px"
---

# Design System: Onionmind Workbench

## Overview

**Creative North Star: "The Calm Local Work Loop"**

Onionmind is one native workbench for choosing a repository and Onionmind model, describing work, watching an interruptible Chat or Agent run, and verifying what was observed. The transcript stays primary while projects, sessions, terminal output, context, changes, and activity remain part of the same legible loop.

The interface is quiet and operational: matte warm graphite planes, fine charcoal seams, soft bone type, and aubergine held back for selection, focus, and the primary action. Density is compact and information-rich without becoming a dashboard; balanced Workbench A in Operate mode is the canonical expression.

**Key Characteristics:**

- Transcript-dominant three-pane workbench
- Flat, warm, low-chroma material palette
- Compact native controls and platform-neutral monochrome icons
- Explicit privacy, permission, service, and observed-change states
- Keyboard-first behavior with responsive pane collapse

## Colors

Warm near-black graphites establish the planes; bone and ash carry hierarchy; a single muted aubergine family marks interaction.

### Primary

- **Workbench Aubergine** (`aubergine`): the primary Send action and intentional high-emphasis controls.
- **Aubergine Hover** (`aubergine-hover`): primary hover and the stronger selected-mode plane.
- **Aubergine Selection** (`aubergine-selection`): selected projects, sessions, and list rows without turning the whole screen purple.
- **Aubergine Focus** (`aubergine-focus`): the visible one-pixel keyboard-focus boundary.

### Neutral

- **Warm Graphite** (`graphite-window`, `graphite-workspace`): the window and dominant transcript canvas.
- **Graphite Planes** (`graphite-panel`, `graphite-raised`): toolbar, inspector, composer surround, and status containers.
- **Dark Work Surfaces** (`graphite-terminal`, `graphite-card`, `graphite-tool`, `graphite-control`): terminal, cards, tool rows, fields, and controls.
- **Charcoal Seams** (`charcoal-seam`, `charcoal-seam-subtle`): fine structural dividers and control boundaries.
- **Soft Bone and Ash** (`bone`, `bone-strong`, `ash`): primary copy, strong labels, and subdued metadata.
- **Warm Stone** (`stone-icon`): authored monochrome action icons.

### Status

- **Ready Green**, **Caution Ochre**, and **Failure Coral** (`status-good`, `status-warn`, `status-bad`): small state dots paired with explicit text, never standalone meaning.

**The Restrained Aubergine Rule.** Aubergine marks selection, focus, and the primary action; it never becomes ambient decoration.

## Typography

**UI Font:** the platform's proportional system UI family, with Segoe UI on Windows and Noto Sans or DejaVu Sans fallbacks on Linux.

**Monospace Font:** Cascadia Mono, Consolas, or DejaVu Sans Mono.

**Character:** Compact, familiar, and highly legible. The application registers available platform fonts but preserves the operating system's UI point size instead of imposing a fixed pixel scale.

### Hierarchy

- **Brand** (650, 11pt, normal): Onionmind's compact toolbar identity; never a page-sized heading.
- **Title** (650, system-scaled, normal): message authors, card titles, repository names, and change summaries.
- **Body** (400, system-scaled, normal): transcript and explanatory copy, capped at a responsive 74-character measure.
- **Label** (650, 8.5pt, normal): small section labels such as PROJECTS, SESSIONS, TERMINAL, and DIFF.
- **Mono** (400, 9pt, normal): terminal output, terminal input, and diff content only; tool activity and ordinary file paths stay proportional.

**The Proportional Workbench Rule.** Use the platform-scaled proportional UI face everywhere except terminal output, terminal input, and diff content.

## Layout

The canonical review viewport is 1440×900; the window opens at 1420×900 and remains usable down to 760×620. A 57px toolbar and 24px status bar frame the horizontal splitter. Its default panes are a 224px project/session rail, an 860px transcript work area, and a 292px inspector; the rail is constrained to 190–290px, the inspector to 250–390px, and the center keeps at least 450px.

The transcript uses 48px inline margins, 20px above, and 24px below. Messages target the measured width of 74 platform-font characters and tool cards target 78; both shrink to the available pane, and message height grows from the wrapped plain text so no transcript content clips. Tool rows are indented 44px to align with message copy. The terminal drawer is 135–200px tall and the composer is 148–178px tall.

Responsive behavior is progressive. Below 1280px the redundant model-status pill hides while the single Tor status/action button remains visible. Below 1100px the branch hides and the model chooser becomes compact; below 1080px the inspector collapses; below 900px the brand block narrows and the redundant repository label hides; below 820px the rail collapses. Toolbar toggles keep both side panes reachable.

Use the small spacing scale inside controls and cards, with the 10–12px steps for pane padding and grouped content. Preserve the broad transcript breathing room; do not apply card padding to the whole work area.

**The Transcript-First Rule.** The center transcript remains the largest pane; at narrow widths the inspector collapses before the project rail.

## Elevation & Depth

The workbench has no shadows. Depth comes from closely spaced matte graphite tones and one-pixel charcoal seams: the terminal is darkest, controls and cards step slightly lighter, and the transcript remains a quiet uninterrupted plane. Hover and selection change tone within that vocabulary rather than lifting elements.

**The Tonal Depth Rule.** Separate planes with matte tone shifts and one-pixel seams; do not add shadows, glow, gradients, glass, or blur.

## Shapes

The form language is compact and nearly square. List rows, attachments, checkboxes, and progress treatments use the compact radius; controls and status containers use the control radius; cards and tool activity use the card radius. Terminal and diff surfaces are square at zero radius. The 16px radius is reserved for 32px identity avatars, not general controls.

Action icons are authored on an 18×18 canvas with a 1.45px warm-stone stroke, rounded caps and joins, and no fill unless the glyph requires a solid stop mark. Use the same platform-neutral line vocabulary across Windows and Linux; the supplied Onionmind logo is the only branded exception.

**The Compact Native Rule.** Keep controls square and compact at 3–5px radii; reserve the 16px avatar radius for 32px identity marks.

## Components

### Buttons

- **Shape:** compact native rectangles with the control radius and a minimum 18px content height.
- **Primary:** Workbench Aubergine with soft on-accent text and 5px × 17px padding; hover strengthens both fill and border, press returns to the workspace graphite.
- **Secondary:** graphite control fill, one-pixel seam, and 5px × 9px padding; hover changes to a slightly lighter warm plane.
- **Bare tools:** 18px authored icons with 4px padding and a transparent rest state; they gain a subtle graphite box on hover.
- **Focus / Disabled:** replace the normal boundary with the one-pixel focus color for keyboard focus. Disabled controls retain their silhouette and use subdued text, fill, and seam colors.

### Inputs / Fields

- **Composer:** multiline proportional text on the graphite control plane, 10px internal padding, visible focus border, Enter to send, Shift+Enter for a newline, and file drop support.
- **Terminal input:** the sole compact command field, using the mono role and the terminal plane. Terminal and diff output remain selectable.

### Navigation

- **Rail:** left-aligned compact actions and 47px two-line project/session rows. Hover is tonal; selection uses the restrained aubergine plane plus text, not color alone.
- **Inspector tabs:** proportional labels with 12px × 16px padding and a two-pixel aubergine underline for the active tab; the pane itself begins with a one-pixel seam.
- **Responsive access:** pane toggles stay in the toolbar whenever automatic collapse hides a rail or inspector.

### Cards / Containers

- **Context and privacy cards:** card radius, one-pixel seam, no shadow, and 9px × 10px internal padding.
- **Tool activity:** tool-card plane, card radius, one-pixel seam, and 8px × 11px padding; title and item count lead rows whose values remain selectable.
- **Transcript messages:** 32px identity avatar, 12px avatar-to-copy gap, selectable literal text, 74-character target measure, and dynamically recomputed wrapped height.

### Status and Privacy

- **Status containers:** compact 4px radius, 4px × 8px padding, a 6px state dot inside a 10px indicator, and a textual prefix plus value.
- **Tor control:** use one compact native power button for both status and action. Visible copy stays scannable (`Tor · Off`, `Tor · Checking…`, `Tor · Ready`, `Tor · Unavailable`); tooltip and accessibility copy name the next action. Gray means off, amber is only the transient check, green requires a verified Tor circuit, and red means retry is needed. An external proxy remains controllable as an Onionmind connection without implying its process will be killed.
- **Privacy boundary:** present Onionmind inference, Tor-routed Chat search, and Agent networking as separate sentences and states. Success green may support “Local inference,” but wording carries the meaning.

**The Honest State Rule.** Onionmind model, Tor, Agent, permission, activity, and observed-change states are always named in text and never encoded by color alone.

**The Separate Boundaries Rule.** Present Onionmind inference, Tor-routed Chat search, and Agent networking as separate facts; never imply Agent traffic is Tor-confined.

## Do's and Don'ts

### Do:

- **Do** preserve the platform UI point size and use proportional text throughout the workbench except in terminal and diff surfaces.
- **Do** keep the transcript largest, with a responsive 74-character message measure and 78-character tool measure.
- **Do** use the authored monochrome icon set for actions and the supplied Onionmind asset for product identity.
- **Do** provide visible keyboard focus, accessible names, selectable output, and textual state labels.
- **Do** distinguish Onionmind inference, Tor search, and Agent networking wherever privacy boundaries appear.
- **Do** keep backend vendor names out of ordinary UI copy; use Onionmind model tiers, Onionmind Chat, and Onionmind Agent.

### Don't:

- **Don't** add shadows, glow, gradients, glass, blur, or neon terminal styling.
- **Don't** turn compact controls into pills or introduce oversized headings and decorative cards.
- **Don't** substitute emoji, Unicode symbols, or platform-dependent glyphs for authored action icons.
- **Don't** use monospace for transcript prose, navigation, tool activity, or ordinary file paths.
- **Don't** imply a repository changed until the application has observed the change on disk.
