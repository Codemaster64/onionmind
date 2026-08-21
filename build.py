#!/usr/bin/env python3
"""Inject the canonical onionmind.py, logo.svg and onionmind.ico into both
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
import ast, base64, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / "onionmind.py"

# The only intentional per-platform differences. Everything else is identical by
# construction. Keep this list short - each entry is a place drift can hide.
WINDOWS_SUBS = [
    ("PORTS  = (9050, 9150)                            # 9050 = tor daemon, 9150 = Tor Browser",
     "PORTS  = (9150, 9050)                            # 9150 = Tor Browser, 9050 = tor daemon"),
    ('Needs a tor daemon on 9050 (systemctl start tor) or Tor Browser on 9150.',
     'Needs Tor Browser open (it owns SOCKS on 9150) or a tor daemon on 9050.'),
    ('sys.exit("No Tor proxy on 9050/9150. Try: sudo systemctl start tor")',
     'sys.exit("No Tor proxy on 9150/9050. Open Tor Browser and leave it running.")'),
]

TARGETS = [
    # path,                     opening delimiter,        closing delimiter,  newline, subs
    ("install-onionmind.ps1",   "$search = @'\n",         "\n'@\n",           "\r\n",  WINDOWS_SUBS),
    ("install-onionmind.sh",    "<<'PYEOF'\n",            "\nPYEOF\n",        "\n",    []),
]

# The desktop-icon payloads, derived not hand-maintained: onionmind.ico is
# rendered from logo.svg (see README Files table for the render command), the
# .sh embeds the SVG itself because .desktop Icon= takes SVGs.
def icon_payloads():
    b64 = base64.b64encode((ROOT / "onionmind.ico").read_bytes()).decode()
    ico = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    svg = (ROOT / "logo.svg").read_text(encoding="utf-8").rstrip("\n")
    return {
        ("install-onionmind.ps1", "$OnionIco = @'\n", "\n'@\n", "\r\n"): ico,
        ("install-onionmind.sh",  "<<'SVGEOF'\n",     "\nSVGEOF\n", "\n"): svg,
    }

ICON_TARGETS = icon_payloads()


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

    if check_only and stale:
        print(f"\n{stale} installer(s) stale - run: python build.py")
        return 1
    print(f"\nok - {len(TARGETS)} installer(s) carry onionmind.py verbatim, "
          f"plus {len(ICON_TARGETS)} icon payload(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
