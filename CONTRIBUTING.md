# Contributing

This is a non-profit, open-source project — contributions are welcome and
there is nothing to buy.

- **Bugs and ideas:** open an issue. Hardware reports are gold: GPU model +
  what happened beats "it's slow".
- **Pull requests:** keep them small and pointed. Run the checks first:
  ```bash
  python build.py --check        # installers carry the canonical payloads?
  python -m py_compile onionmind.py build.py
  bash -n install-onionmind.sh install-onionmind-android.sh matchstick.sh usb/build.sh
  python tests/test_backends.py  # adapter logic (no network needed)
  ```
- **The one rule:** `onionmind.py`, the icon and the logo payloads are
  single-sourced — never hand-edit the copies inside installers. Edit the
  source, run `python build.py`, commit both.
- **Honesty culture:** every claim in the READMEs says how it was verified.
  If you add a feature, add the check that proves it, or say it's unverified.
- **No CLA, no donations, no sponsorships.** MIT in, MIT out.
