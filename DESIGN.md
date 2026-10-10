---
name: "Onionmind Workbench · Nocturne"
description: "A calm native work loop for local inference, repository work, and honest review."
colors:
  nocturne-bg: "#161826"
  nocturne-surface: "#232532"
  nocturne-text: "#e9e9ed"
  nocturne-accent: "#9184d9"
  nocturne-divider: "color-mix(in srgb, #e9e9ed 16%, transparent)"
  neutral-100: "#f3f5fe"
  neutral-200: "#e4e7f5"
  neutral-300: "#cfd3e5"
  neutral-400: "#b2b6ca"
  neutral-500: "#9397ab"
  neutral-600: "#75798c"
  neutral-700: "#595d6c"
  neutral-800: "#3f424d"
  neutral-900: "#292b31"
  accent-100: "#f5f4ff"
  accent-200: "#e7e5fe"
  accent-300: "#d2cefd"
  accent-400: "#b5abfc"
  accent-500: "#968ae0"
  accent-600: "#796cbf"
  accent-700: "#5d5294"
  accent-800: "#423a6a"
  accent-900: "#2b2741"
  status-good: "#74c692"
  status-warn: "#e3b667"
  status-bad: "#eb8373"
  light-bg: "#f2f3f8"
  light-surface: "#fbfbfe"
  light-text: "#1d1f2b"
  light-accent: "#6b5fc0"
typography:
  brand:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "11pt"
    fontWeight: 500
    lineHeight: "normal"
    letterSpacing: "normal"
  title:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
    fontSize: "1em"
    fontWeight: 500
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
    fontWeight: 500
    lineHeight: "normal"
    letterSpacing: "normal"
  mono:
    fontFamily: "'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace"
    fontSize: "9pt"
    fontWeight: 400
    lineHeight: "normal"
    letterSpacing: "normal"
rounded:
  compact: "4px"
  control: "8px"
  card: "12px"
  avatar: "16px"
spacing:
  xs: "4px"
  sm: "5px"
  md: "8px"
  lg: "10px"
  xl: "12px"
components:
  button-primary:
    backgroundColor: "transparent"
    borderColor: "{colors.nocturne-accent}"
    textColor: "{colors.nocturne-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "5px 17px"
  button-primary-hover:
    backgroundColor: "color-mix(in srgb, {colors.nocturne-accent} 12%, transparent)"
    textColor: "{colors.nocturne-accent}"
    rounded: "{rounded.control}"
    padding: "5px 17px"
  button-secondary:
    backgroundColor: "{colors.nocturne-surface}"
    textColor: "{colors.nocturne-text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "5px 9px"
  composer:
    backgroundColor: "{colors.nocturne-surface}"
    textColor: "{colors.nocturne-text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px"
  card:
    backgroundColor: "{colors.nocturne-surface}"
    textColor: "{colors.nocturne-text}"
    rounded: "{rounded.card}"
    padding: "9px 10px"
  tool-card:
    backgroundColor: "{colors.nocturne-surface}"
    textColor: "{colors.nocturne-text}"
    rounded: "{rounded.card}"
    padding: "8px 11px"
  status-pill:
    backgroundColor: "{colors.nocturne-surface}"
    textColor: "{colors.nocturne-text}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
  mode-selected:
    backgroundColor: "{colors.accent-800}"
    textColor: "{colors.neutral-100}"
    rounded: "{rounded.control}"
    padding: "4px 16px"
  inspector-tabs:
    backgroundColor: "{colors.nocturne-bg}"
    textColor: "{colors.neutral-400}"
    typography: "{typography.body}"
    padding: "12px 16px 10px"
---

# Design System: Onionmind Workbench

## Overview

**Creative North Star: "The Calm Local Work Loop"**

Onionmind is one native workbench for choosing a repository and Onionmind model, describing work, watching an interruptible Chat or Agent run, and verifying what was observed. The transcript stays primary while projects, sessions, terminal output, context, changes, and activity remain part of the same legible loop.

The interface is quiet and operational: a cool blue-black Nocturne ground, one-pixel divider seams, near-white type, and a blurple accent held back for selection, focus, and the primary action. Density is compact and information-rich without becoming a dashboard; balanced Workbench A in Operate mode is the canonical expression.

**Key Characteristics:**

- Transcript-dominant three-pane workbench
- Flat, warm, low-chroma material palette
- Compact native controls and platform-neutral monochrome icons
- Explicit privacy, permission, service, and observed-change states
- Keyboard-first behavior with responsive pane collapse

## Colors

The palette is **Nocturne**: a cool blue-black ground, one surface step above it, near-white text, and a single blurple accent. The stylesheet never names a hex value directly; `theme_tokens()` in `onionmind_desktop.py` derives every semantic role (pane, card, field, line, muted text, accent hover, status tints) from the palette, so the light theme is the same sheet over a different palette.

### Roles

- **Ground** (`nocturne-bg`): the window and the transcript canvas.
- **Pane** (surface at 35% over the ground): toolbar, rail, inspector, status bar.
- **Surface** (`nocturne-surface`): cards, popovers, menus, tooltips.
- **Divider** (text at 16%): every seam and control boundary.
- **Text ramp** (`neutral-100`…`neutral-900`): strong labels at 100, body at the text token, metadata at 400–500, disabled at 600.
- **Accent** (`nocturne-accent`, ramp `accent-100`…`accent-900`): focus, selection, the primary action's outline, the active tab, progress.
- **Status** (`status-good`, `status-warn`, `status-bad`): sRGB renderings of oklch(0.76 0.11 155), oklch(0.80 0.11 80) and oklch(0.72 0.13 30); tinted at 12% for amber and red cards. Always paired with text.

### Light theme

Behind the appearance feature: ground `light-bg`, surface `light-surface`, text `light-text`, accent `light-accent`, with both ramps reversed so a step means the same contrast in either theme. Status colours are darkened for contrast on the light ground.

**The Restrained Accent Rule.** The accent marks focus, selection and the primary action; it never becomes ambient decoration.

## Typography

**UI Font:** Inter when the system has it (it is not bundled), otherwise the platform's proportional system UI family, with Segoe UI on Windows and Noto Sans or DejaVu Sans fallbacks on Linux. Headings use weight 500.

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

The workbench has no shadows. Depth comes from closely spaced Nocturne tones and one-pixel divider seams: the terminal is darkest, controls and cards step slightly lighter, and the transcript remains a quiet uninterrupted plane. Hover and selection change tone within that vocabulary rather than lifting elements.

**The Tonal Depth Rule.** Separate planes with matte tone shifts and one-pixel seams; do not add shadows, glow, gradients, glass, or blur.

## Shapes

The form language is compact and nearly square. List rows, attachments, checkboxes, and progress treatments use the compact radius; controls and status containers use the control radius; cards and tool activity use the card radius. Terminal and diff surfaces are square at zero radius. The 16px radius is reserved for 32px identity avatars, not general controls.

Action icons are authored on an 18×18 canvas with a 1.45px warm-stone stroke, rounded caps and joins, and no fill unless the glyph requires a solid stop mark. Use the same platform-neutral line vocabulary across Windows and Linux; the supplied Onionmind logo is the only branded exception.

**The Compact Native Rule.** Controls use the 8px radius and cards and popovers the 12px radius; reserve the 16px avatar radius for 32px identity marks.

## Components

### Buttons

- **Shape:** compact native rectangles with the control radius and a minimum 18px content height.
- **Primary:** an accent outline with accent text and no fill, 5px × 17px padding; hover and press tint the inside with the accent at 12% and 22%.
- **Secondary:** control fill, one-pixel divider, and 5px × 9px padding; hover lifts it by 7% text.
- **Bare tools:** 18px authored icons with 4px padding and a transparent rest state; they gain a subtle tinted box on hover.
- **Focus / Disabled:** keyboard focus is a 2px accent boundary on every control. Disabled controls retain their silhouette and use subdued text, fill, and seam colors.

### Inputs / Fields

- **Composer:** multiline proportional text on the field plane, 10px internal padding, visible focus border, Enter to send, Shift+Enter for a newline, and file drop support.
- **Terminal input:** the sole compact command field, using the mono role and the terminal plane. Terminal and diff output remain selectable.

### Navigation

- **Rail:** left-aligned compact actions and 47px two-line project/session rows. Hover is tonal; selection uses the restrained accent tint plus text, not color alone.
- **Inspector tabs:** proportional labels with 12px × 16px padding and a two-pixel accent underline for the active tab; the pane itself begins with a one-pixel seam.
- **Responsive access:** pane toggles stay in the toolbar whenever automatic collapse hides a rail or inspector.

### Cards / Containers

- **Context and privacy cards:** card radius, one-pixel seam, no shadow, and 9px × 10px internal padding.
- **Tool activity:** tool-card plane, card radius, one-pixel seam, and 8px × 11px padding; title and item count lead rows whose values remain selectable.
- **Transcript messages:** 32px identity avatar, 12px avatar-to-copy gap, selectable literal text, 74-character target measure, and dynamically recomputed wrapped height.

### Status and Privacy

- **Status containers:** compact 4px radius, 4px × 8px padding, a 6px state dot inside a 10px indicator, and a textual prefix plus value.
- **Tor control:** translate the selected circular power-target direction into the compact toolbar: a prominent 36px state target and binary label share one persistent bordered button surface and click target. Off and on use one solid disc with one correctly oriented power mark; verification replaces them with one amber spinner on a quiet halo so no rings or glyphs overlap. Visible copy remains `Tor is off` or `Tor is on`; tooltip, accessibility copy, and the status bar carry transitional detail and name the next action. Green `Tor is on` requires a verified Tor circuit. Startup and error states conservatively retain `Tor is off`. An external proxy remains controllable as an Onionmind connection without implying its process will be killed.
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
