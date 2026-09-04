# Contributing

This is a non-profit, open-source project — contributions are welcome and
there is nothing to buy.

- **Bugs and ideas:** open an issue. Hardware reports are gold: GPU model +
  what happened beats "it's slow".
- **Pull requests:** keep them small and pointed. For the complete local suite,
  install `requirements-desktop.txt` and the `ollama-tor[test]` extra, then run:
  ```bash
  python build.py --check        # installers carry the canonical payloads?
  python -m unittest discover -s tests -p "test_*.py"
  python -m pytest ollama-tor/tests
  (cd android && ./gradlew :core:test :app:testDebugUnitTest)  # Kotlin suites; needs a JDK 17
  bash -n install-onionmind.sh install-onionmind-android.sh matchstick.sh usb/build.sh
  ```
- **The one rule:** `onionmind.py`, the icon and the logo payloads are
  single-sourced — never hand-edit the copies inside installers. Edit the
  source, run `python build.py`, commit both.
- **Honesty culture:** every claim in the READMEs says how it was verified.
  If you add a feature, add the check that proves it, or say it's unverified.
- **No CLA, no donations, no sponsorships.** MIT in, MIT out.

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md),
not in a public issue.
