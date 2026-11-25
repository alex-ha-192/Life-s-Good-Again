# Life's Good Again

A privacy kit for LG webOS TVs, stock or rooted (built for a 2023 43" 4K set), plus **Tiles Home**, a tile-style launcher.

**Open [`GUIDE.html`](GUIDE.html) for the full guide** (download it and open it in a browser).

## Quick start

```sh
python3 setup.py --dry-run                      # see the whole plan, change nothing
python3 setup.py --pihole http://192.168.1.2    # or --adguard http://192.168.1.2:3000
python3 setup.py status
python3 setup.py undo
python3 setup.py --root --tv 192.168.1.50       # ROOTED TV (Homebrew Channel): Home button -> Tiles Home,
                                                # blocking + firewall + settings guard on the TV itself
```

**No computer you can leave on?** You don't need one. Tiles Home renews Developer Mode by itself each week you open it. For DNS without a Pi-hole, use the short lists in `blocklist/*.cloud*.txt` with a cloud DNS service, or root the TV and use `--root`. See "No computer left on?" in the guide.

Python 3.8+, no extra packages. Without `--root`, nothing touches the TV's firmware or system files. Every change is an LG setting (backed up first), a Developer Mode app install, or something on your own computer, Pi-hole or AdGuard that `undo` removes.

| Path | What it is |
|---|---|
| `setup.py` | One guided run: pair, privacy settings, on-screen checklist, Developer Mode, Tiles Home, DNS, router rules, timer |
| `lgtv_privacy.py` | Pair, audit, apply/restore privacy settings, `guard` (re-applies settings LG turns back on) |
| `lgtv_devmode.py` | Keeps the Developer Mode session from expiring |
| `blocklist/` | LG ad/ACR/telemetry blocklists in 6 formats plus short cloud-DNS lists (base, strict, doh tiers); sources in `blocklist/src/` |
| `root/` | Rooted TVs: `install.sh` (runs on the TV), boot hook, on-TV agent (Home button, power-on, settings guard), Home-key finder |
| `router/` | TV-only rules that stop DNS bypass (OpenWrt, MikroTik, pfSense/OPNsense, plain routers) |
| `launcher/`, `dist/` | Tiles Home source and the ready-to-install `.ipk` |
| `tools/` | `.ipk` builder, blocklist builder, DNS bypass checker, mock TV for testing |
| `design/` | Interactive layout mockups |

Unofficial; not affiliated with LG. Tiles Home is an original design. The bundled Open Sans font is under the SIL Open Font License (`launcher/fonts/OFL.txt`).
