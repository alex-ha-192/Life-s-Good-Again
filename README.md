# Life's Good Again

A privacy kit for **rooted** LG webOS TVs (built for a 2023 43" 4K set), plus **Tiles Home**, a tile-style launcher that takes over the Home button.

**Open [`GUIDE.html`](GUIDE.html) for the full guide** (download it and open it in a browser).

The blocking is done by [Life's Good Without Spying](https://github.com/lewisdoesstuff/lifesgoodwithoutspying), an existing Homebrew Channel app. The kit installs and configures it, then adds per-country ad hosts and DoH services, a firewall against DNS bypass, a settings guard, and Tiles Home on Home, power-on and wake.

## Quick start

Root the TV first (see [cani.rootmy.tv](https://cani.rootmy.tv/); not covered here) and turn on SSH Server in Homebrew Channel.

```sh
python3 setup.py --root --skip dns,router --dry-run   # see the plan, change nothing
python3 setup.py --root --skip dns,router             # one run from any laptop; nothing stays on after
python3 setup.py status
python3 setup.py undo
```

Options: `--keep-store` (keep the Content Store working), `--keep-voice`, `--root-tiers base,strict,doh`, `--block-updates`, `--home-code N`, `--no-lgws`.

The stock-TV (no-root) tools from earlier versions are still in the repo (`setup.py` without `--root`, `lgtv_devmode.py`, `router/`), but the guide now covers the rooted path only.

Python 3.8+ on the laptop, no extra packages. `undo` (or `install.sh --uninstall` on the TV) removes everything the kit set up.

| Path | What it is |
|---|---|
| `setup.py` | One guided run. With `--root`: pair, privacy settings, on-screen checklist, Tiles Home, then the on-TV install |
| `lgtv_privacy.py` | Pair, audit, apply/restore privacy settings, `guard` (re-applies settings LG turns back on) |
| `lgtv_devmode.py` | Keeps the Developer Mode session from expiring |
| `blocklist/` | LG ad/ACR/telemetry blocklists in 6 formats plus short cloud-DNS lists (base, strict, doh tiers); sources in `blocklist/src/` |
| `root/` | `install.sh` (runs on the TV: sets up LGWS, extras, firewall, agent), boot hook, on-TV agent (Home button, power-on, settings guard), Home-key finder |
| `router/` | TV-only rules that stop DNS bypass (OpenWrt, MikroTik, pfSense/OPNsense, plain routers) |
| `launcher/`, `dist/` | Tiles Home source and the ready-to-install `.ipk` |
| `tools/` | `.ipk` builder, blocklist builder, DNS bypass checker, mock TV for testing |
| `design/` | Interactive layout mockups |

Unofficial; not affiliated with LG. Tiles Home is an original design. The bundled Open Sans font is under the SIL Open Font License (`launcher/fonts/OFL.txt`).
