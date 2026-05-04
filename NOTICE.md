# Third-Party Notices

Huntova is licensed under **AGPL-3.0-or-later** (see `LICENSE`).

This file lists external work whose **structural patterns** influenced
parts of this codebase, along with the licenses under which that work
was obtained. None of the original third-party code is reproduced
verbatim in Huntova; the modules listed below are independent
reimplementations in Python authored for Huntova specifically. They
are documented here for transparency and to acknowledge the prior art.

---

## OpenClaw — `@openclaw/openclaw`

- **Source:** https://github.com/openclaw/openclaw
- **License:** MIT
- **Copyright:** © OpenClaw contributors
- **Adaptation type:** structural / architectural inspiration only — no
  code or copyrighted text from OpenClaw appears in Huntova.

The following Huntova modules borrow architectural patterns observed in
OpenClaw's open-source reference implementation. Each is a fresh Python
implementation; no TypeScript was ported, and no UI copy, brand assets,
documentation prose, or product wording was reused.

| Huntova file                | Pattern adapted                                                                              |
|-----------------------------|-----------------------------------------------------------------------------------------------|
| `tui.py`                    | Wizard prompt-set shape (intro / outro / note / select / text / password / confirm / spinner). Backed by Python `questionary`; OpenClaw uses `@clack/prompts` (Node). |
| `huntova_daemon.py`         | Local-user daemon pattern — launchd LaunchAgent plist on macOS, systemd `--user` unit on Linux, with `huntova daemon install/uninstall/start/stop/status/logs` verbs. |
| `cli.py` — `cmd_onboard`    | Three-phase first-run wizard pattern: filesystem → provider/key → launch. Banner + step indicators. |
| `static/install.sh`         | Cross-platform shell installer pattern (Python detection → pipx bootstrap → optional Playwright dependency → final next-step hint). Generic; not specific to either project. |
| `templates/download.html`   | Animated terminal demo block on the marketing/install page. Independent CSS implementation. |

The MIT license requires that copyright and permission notices be
preserved when **substantial portions** of the source are copied. Since
no OpenClaw source is reproduced verbatim in Huntova, that condition
does not apply directly; the attribution table above is provided
voluntarily to credit the prior art.

The OpenClaw MIT license text is reproduced in full below.

```
MIT License

Copyright (c) OpenClaw contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Other dependencies

Standard third-party Python libraries used at runtime are listed in
`pyproject.toml`. Each carries its own license (most are MIT, BSD, or
Apache-2.0). Huntova does not redistribute their source; they are
declared as dependencies and installed by `pip` / `pipx`.

If you redistribute Huntova binaries that bundle these dependencies
(e.g. via PyInstaller or a Docker image), you are responsible for
preserving each dependency's own license file in the redistribution.

---

For questions about licensing or attribution, please open an issue on
the Huntova repository.
