#!/usr/bin/env python3
"""Inject the canonical Python sources, logo.svg and onionmind.ico into the
installers. Run after editing any of them.

    python build.py            # inject, verify, report
    python build.py --check    # verify only; non-zero exit if stale (for CI)

onionmind.py used to exist in three places: the repo copy, a PowerShell here-string
and a shell heredoc. They drifted, and a fix applied to one silently missed the
others - that is how the Windows MODEL-rewrite bug shipped. This file makes the
repo copy the single source and the installers pure build artefacts.

The same rule now covers the desktop-icon payloads: the .ico (rendered from
logo.svg - regenerate with librsvg+imagemagick, see README) goes into the .ps1
base64, and logo.svg itself into the .sh, where .desktop files can use it
directly.

Line endings are enforced per target: the .ps1 is CRLF, the .sh is LF. A CRLF
shell script dies on Linux with `bad interpreter: /usr/bin/env bash^M`, which has
also already happened here once.
"""
import ast, base64, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / "onionmind.py"

AUXILIARY_SOURCES = [
    # source,                         path,                   opening delimiter,          closing delimiter,       newline
    ("onionmind_desktop_core.py",    "onionmind-setup.cmd",  "$desktopCore = @'\n",     "\n'@\n",                "\r\n"),
    ("onionmind_desktop_core.py",    "install-onionmind.sh", "<<'DESKTOPCOREEOF'\n",    "\nDESKTOPCOREEOF\n",    "\n"),
    ("onionmind_desktop.py",         "onionmind-setup.cmd",  "$desktopUi = @'\n",       "\n'@\n",                "\r\n"),
    ("onionmind_desktop.py",         "install-onionmind.sh", "<<'DESKTOPUIEOF'\n",      "\nDESKTOPUIEOF\n",      "\n"),
    ("onionmind_desktop_theme.py",   "onionmind-setup.cmd",  "$desktopTheme = @'\n",    "\n'@\n",                "\r\n"),
    ("onionmind_desktop_theme.py",   "install-onionmind.sh", "<<'DESKTOPTHEMEEOF'\n",   "\nDESKTOPTHEMEEOF\n",   "\n"),
    ("onionmind_desktop_widgets.py", "onionmind-setup.cmd",  "$desktopWidgets = @'\n",  "\n'@\n",                "\r\n"),
    ("onionmind_desktop_widgets.py", "install-onionmind.sh", "<<'DESKTOPWIDGETSEOF'\n", "\nDESKTOPWIDGETSEOF\n", "\n"),
    ("onionmind_desktop_panes.py",   "onionmind-setup.cmd",  "$desktopPanes = @'\n",    "\n'@\n",                "\r\n"),
    ("onionmind_desktop_panes.py",   "install-onionmind.sh", "<<'DESKTOPPANESEOF'\n",   "\nDESKTOPPANESEOF\n",   "\n"),
    ("onionmind_desktop_dialogs.py", "onionmind-setup.cmd",  "$desktopDialogs = @'\n",  "\n'@\n",                "\r\n"),
    ("onionmind_desktop_dialogs.py", "install-onionmind.sh", "<<'DESKTOPDIALOGSEOF'\n", "\nDESKTOPDIALOGSEOF\n", "\n"),
    ("onionmind_desktop_vault.py",        "onionmind-setup.cmd",  "$desktopVault = @'\n",      "\n'@\n",                "\r\n"),
    ("onionmind_desktop_vault.py",        "install-onionmind.sh", "<<'DESKTOPVAULTEOF'\n",    "\nDESKTOPVAULTEOF\n",    "\n"),
    ("onionmind_desktop_netlog.py",       "onionmind-setup.cmd",  "$desktopNetlog = @'\n",     "\n'@\n",                "\r\n"),
    ("onionmind_desktop_netlog.py",       "install-onionmind.sh", "<<'DESKTOPNETLOGEOF'\n",   "\nDESKTOPNETLOGEOF\n",   "\n"),
    ("onionmind_desktop_update.py",       "onionmind-setup.cmd",  "$desktopUpdate = @'\n",     "\n'@\n",                "\r\n"),
    ("onionmind_desktop_update.py",       "install-onionmind.sh", "<<'DESKTOPUPDATEEOF'\n",   "\nDESKTOPUPDATEEOF\n",   "\n"),
    ("onionmind_desktop_hardware.py",     "onionmind-setup.cmd",  "$desktopHardware = @'\n",   "\n'@\n",                "\r\n"),
    ("onionmind_desktop_hardware.py",     "install-onionmind.sh", "<<'DESKTOPHARDWAREEOF'\n", "\nDESKTOPHARDWAREEOF\n", "\n"),
    ("onionmind_desktop_remote.py",       "onionmind-setup.cmd",  "$desktopRemote = @'\n",     "\n'@\n",                "\r\n"),
    ("onionmind_desktop_remote.py",       "install-onionmind.sh", "<<'DESKTOPREMOTEEOF'\n",   "\nDESKTOPREMOTEEOF\n",   "\n"),
]

# The only intentional per-platform differences. Everything else is identical by
# construction. Keep this list short - each entry is a place drift can hide.
WINDOWS_SUBS = []

TARGETS = [
    # path,                     opening delimiter,        closing delimiter,  newline, subs
    ("onionmind-setup.cmd",   "$search = @'\n",         "\n'@\n",           "\r\n",  WINDOWS_SUBS),
    ("install-onionmind.sh",    "<<'PYEOF'\n",            "\nPYEOF\n",        "\n",    []),
    ("install-onionmind-android.sh", "<<'PYEOF'\n",       "\nPYEOF\n",        "\n",    []),
]

# The desktop-icon payloads, derived not hand-maintained: onionmind.ico is
# rendered from logo.svg (see README Files table for the render command), the
# .sh embeds the SVG itself because .desktop Icon= takes SVGs.
def icon_payloads():
    b64 = base64.b64encode((ROOT / "onionmind.ico").read_bytes()).decode()
    ico = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    svg = (ROOT / "logo.svg").read_text(encoding="utf-8").rstrip("\n")
    return {
        ("onionmind-setup.cmd", "$OnionIco = @'\n", "\n'@\n", "\r\n"): ico,
        ("install-onionmind.sh",  "<<'SVGEOF'\n",     "\nSVGEOF\n", "\n"): svg,
    }

ICON_TARGETS = icon_payloads()


# Web installers: the same installers, generated from the full ones, with every
# embedded file swapped for a download from the rolling release that must match
# the SHA-256 recorded here at build time. ~40 KB instead of ~0.9 MB; they need
# the network and that release, and an old copy refuses a newer release rather
# than install something it never pinned. The full installers stay the default.
RELEASE_URL = "https://github.com/Codemaster64/onionmind/releases/download/desktop-latest"
WEB_VARIANTS = [
    # full installer,               web installer,                       kind
    ("install-onionmind.sh",         "install-onionmind-web.sh",          "sh"),
    ("install-onionmind-android.sh", "install-onionmind-android-web.sh",  "sh"),
    ("onionmind-setup.cmd",          "onionmind-setup-web.cmd",           "cmd"),
]

SH_FETCH = """\
# web installer: program files come from the release, each checked against the
# SHA-256 build.py pinned below. Set ONIONMIND_RELEASE to use a mirror.
ONIONMIND_RELEASE="${ONIONMIND_RELEASE:-%s}"
fetch_source() {  # destination, release asset, sha256
  _tmp="$1.download"
  curl -fsSL --retry 3 -o "$_tmp" "$ONIONMIND_RELEASE/$2" || { rm -f "$_tmp"; die "could not download $2 from $ONIONMIND_RELEASE"; }
  _got=$( (sha256sum "$_tmp" 2>/dev/null || shasum -a 256 "$_tmp") | cut -d' ' -f1)
  if [ "$_got" != "$3" ]; then
    rm -f "$_tmp"
    die "$2 from the release does not match this installer (sha256 $_got, expected $3). The release has moved on: download the current web installer, or use the full installer, which carries every file."
  fi
  mv "$_tmp" "$1"
}
""" % RELEASE_URL

CMD_FETCH = """\
# web installer: program files come from the release, each checked against the
# SHA-256 build.py pinned below. Set ONIONMIND_RELEASE to use a mirror.
$OnionmindRelease = if ($env:ONIONMIND_RELEASE) { $env:ONIONMIND_RELEASE } else { '%s' }
function Get-OnionmindBytes([string]$Name, [string]$Sha256) {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
  try { $bytes = (New-Object Net.WebClient).DownloadData("$OnionmindRelease/$Name") }
  catch { throw "Could not download $Name from $OnionmindRelease : $($_.Exception.Message)" }
  $sha = [Security.Cryptography.SHA256]::Create()
  $got = (-join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }))
  if ($got -ne $Sha256) {
    throw "$Name from the release does not match this installer (sha256 $got, expected $Sha256). The release has moved on: download the current web installer, or use the full installer, which carries every file."
  }
  return ,$bytes
}
function Get-OnionmindSource([string]$Name, [string]$Sha256) {
  return [Text.Encoding]::UTF8.GetString((Get-OnionmindBytes $Name $Sha256))
}
""" % RELEASE_URL


def _sha256(name: str) -> str:
    import hashlib
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def _payload_spans(target: str):
    """(opener, closer, release asset) for every file embedded in `target`."""
    spans = [(o, c, "onionmind.py") for n, o, c, _nl, _s in TARGETS if n == target]
    spans += [(o, c, src) for src, n, o, c, _nl in AUXILIARY_SOURCES if n == target]
    for (n, o, c, _nl) in ICON_TARGETS:
        if n == target:
            spans.append((o, c, "onionmind.ico" if n.endswith(".cmd") else "logo.svg"))
    return spans


def web_variant(full: str, target: str, kind: str) -> str:
    """The web installer for `target`, from the full installer's text (LF)."""
    text = full
    for opener, closer, asset in _payload_spans(target):
        start = text.index(opener)
        end = text.index(closer, start + len(opener)) + len(closer)
        sha = _sha256(asset)
        if kind == "sh":
            line = text.rfind("\n", 0, start) + 1
            head = text[line:start]
            if not (head.startswith("cat > ") and head.endswith(" ")):
                sys.exit(f"ERROR: {target}: unexpected payload line {head!r}")
            text = text[:line] + f"fetch_source {head[6:-1]} {asset} {sha}\n" + text[end:]
        elif opener == "$OnionIco = @'\n":
            text = text[:start] + f"$OnionIco = [Convert]::ToBase64String((Get-OnionmindBytes '{asset}' '{sha}'))\n" + text[end:]
        else:
            text = text[:start] + opener[:-len("@'\n")] + f"Get-OnionmindSource '{asset}' '{sha}'\n" + text[end:]
    if kind == "sh":
        die = text.index("\ndie()") + 1
        die_end = text.index("\n", die) + 1
        text = text[:die_end] + SH_FETCH + text[die_end:]
        first = text.index("\n") + 1
        text = (text[:first] + f"# Generated by build.py from {target}: the same installer, with the program\n"
                "# files downloaded from the release and checked against pinned SHA-256s.\n" + text[first:])
    else:
        say = text.index("\nfunction Say(") + 1
        say_end = text.index("\n", say) + 1
        text = text[:say_end] + CMD_FETCH + text[say_end:]
        marker = "#__ONION" + "MIND_PS__\n"
        at = text.index(marker) + len(marker)
        text = (text[:at] + f"# Generated by build.py from {target}: the same installer, with the program\n"
                "# files downloaded from the release and checked against pinned SHA-256s.\n" + text[at:])
    return text


def render(source: str, subs) -> str:
    out = source
    for old, new in subs:
        if old not in out:
            sys.exit(f"ERROR: substitution target missing from onionmind.py:\n  {old[:70]}")
        out = out.replace(old, new, 1)
    return out


def splice(path: pathlib.Path, opener: str, closer: str, payload: str) -> str:
    text = path.read_text(encoding="utf-8")          # normalises CRLF -> \n
    i = text.index(opener) + len(opener)
    j = text.index(closer, i)
    return text[:i] + payload + text[j:]


def has_expected_newlines(path: pathlib.Path, newline: str) -> bool:
    """Check physical newlines; ``Path.read_text`` intentionally hides them."""

    raw = path.read_bytes()
    if newline == "\r\n":
        remainder = raw.replace(b"\r\n", b"")
        return b"\r" not in remainder and b"\n" not in remainder
    return b"\r" not in raw


def main() -> int:
    check_only = "--check" in sys.argv
    source = SOURCE.read_text(encoding="utf-8")
    ast.parse(source)                                 # never ship a broken source
    stale = 0

    for name, opener, closer, newline, subs in TARGETS:
        path = ROOT / name
        payload = render(source, subs).rstrip("\n")
        ast.parse(payload)                            # and never ship a broken copy
        current = path.read_text(encoding="utf-8")
        i = current.index(opener) + len(opener)
        j = current.index(closer, i)
        embedded = current[i:j]

        if embedded == payload:
            print(f"  up to date   {name}")
            continue
        stale += 1
        if check_only:
            print(f"  STALE        {name}")
            continue
        path.write_text(splice(path, opener, closer, payload), encoding="utf-8", newline=newline)
        print(f"  injected     {name}  ({len(payload.splitlines())} lines, {newline!r} endings)")

    for source_name, target_name, opener, closer, newline in AUXILIARY_SOURCES:
        source_path = ROOT / source_name
        target_path = ROOT / target_name
        payload = source_path.read_text(encoding="utf-8").rstrip("\n")
        ast.parse(payload)
        current = target_path.read_text(encoding="utf-8")
        i = current.index(opener) + len(opener)
        j = current.index(closer, i)
        if current[i:j] == payload:
            print(f"  up to date   {target_name} ({source_name})")
            continue
        stale += 1
        if check_only:
            print(f"  STALE        {target_name} ({source_name})")
            continue
        target_path.write_text(
            splice(target_path, opener, closer, payload),
            encoding="utf-8",
            newline=newline,
        )
        print(f"  injected     {target_name} ({source_name}, {len(payload.splitlines())} lines)")

    for (name, opener, closer, newline), payload in ICON_TARGETS.items():
        path = ROOT / name
        current = path.read_text(encoding="utf-8")
        i = current.index(opener) + len(opener)
        j = current.index(closer, i)
        if current[i:j] == payload:
            print(f"  up to date   {name} (icon payload)")
            continue
        stale += 1
        if check_only:
            print(f"  STALE        {name} (icon payload)")
            continue
        path.write_text(splice(path, opener, closer, payload), encoding="utf-8", newline=newline)
        print(f"  injected     {name}  (icon payload, {len(payload.splitlines())} lines)")

    for full_name, web_name, kind in WEB_VARIANTS:
        newline = "\r\n" if kind == "cmd" else "\n"
        web = web_variant((ROOT / full_name).read_text(encoding="utf-8"), full_name, kind)
        path = ROOT / web_name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == web and has_expected_newlines(path, newline):
            print(f"  up to date   {web_name} (web installer, {len(web.encode()) // 1024} KB)")
            continue
        stale += 1
        if check_only:
            print(f"  STALE        {web_name} (web installer)")
            continue
        path.write_text(web, encoding="utf-8", newline=newline)
        print(f"  generated    {web_name} (web installer, {len(web.encode()) // 1024} KB)")

    line_ending_targets = {
        name: newline for name, _opener, _closer, newline, _subs in TARGETS
    }
    line_ending_targets.update(
        {name: newline for _source, name, _opener, _closer, newline in AUXILIARY_SOURCES}
    )
    line_ending_targets.update({"onionmind-setup.cmd": "\r\n"})
    for name, newline in line_ending_targets.items():
        path = ROOT / name
        if has_expected_newlines(path, newline):
            continue
        stale += 1
        if check_only:
            print(f"  STALE        {name} (expected {newline!r} line endings)")
            continue
        normalized = path.read_text(encoding="utf-8")
        path.write_text(normalized, encoding="utf-8", newline=newline)
        print(f"  normalized   {name} ({newline!r} line endings)")

    # onionmind-setup.cmd is one file doing two jobs: cmd.exe reads the batch
    # header, stops at `exit /b`, and hands the file to powershell, which skips
    # to the marker and runs everything after it. Splicing payloads happens
    # below the marker, so the header should be untouched - assert that rather
    # than trust it, because losing these six lines turns the one-click
    # installer into a file that opens in Notepad.
    setup = (ROOT / "onionmind-setup.cmd").read_text(encoding="utf-8")
    marker = "#__ONION" + "MIND_PS__"
    for required in ("@echo off", 'set "ONIONMIND_SETUP=%~f0"', "exit /b %ERRORLEVEL%", marker):
        if required not in setup.split(marker)[0] + marker:
            sys.exit(f"ERROR: onionmind-setup.cmd lost its batch header: {required!r}")
    if setup.index(marker) > 2000:
        sys.exit("ERROR: onionmind-setup.cmd marker is not in the header")

    if check_only and stale:
        print(f"\n{stale} generated-file issue(s) - run: python build.py")
        return 1
    print(f"\nok - {len(TARGETS)} installer(s) carry onionmind.py, "
          f"{len(AUXILIARY_SOURCES)} desktop source payload(s), "
          f"{len(ICON_TARGETS)} icon payload(s), and the one-click cmd; "
          f"{len(WEB_VARIANTS)} web installer(s) pin them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
