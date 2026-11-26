#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
lgtv_devmode.py - keep LG's Developer Mode session from expiring, on a timer.

LG's Developer Mode app gives you about 1,000 hours, then removes dev apps
(like Tiles Home) unless you press "Extend" in the app. This helper presses it
for you, the same way LG's own CLI does (`ares-launch com.palmdts.devmode
-p '{"extend":true}'`): it asks the TV to open the Developer Mode app with
extend=true over the pairing you already made with lgtv_privacy.py.

It never changes firmware, settings or files on the TV. Run it on your PC,
Mac or a Raspberry Pi; `schedule` prints the cron / systemd / launchd /
Task Scheduler line for you to add yourself.

    python3 lgtv_devmode.py renew --force        # extend now (try this first)
    python3 lgtv_devmode.py status               # when it last worked, hours left
    python3 lgtv_devmode.py renew --polite       # what the timer runs
    python3 lgtv_devmode.py schedule             # print the timer for this OS

Optional, for an exact "hours left" readout and a check that the extend took:
    python3 lgtv_devmode.py fetch-token --key ~/.ssh/lgtv_webos   # one-time, over Dev Mode SSH
    python3 lgtv_devmode.py set-token <token>                      # or paste it yourself

Exit codes (useful for monitoring): 0 ok or nothing due, 1 error,
2 overdue (no success for --warn-days), 75 TV not reachable / deferred, will retry.
"""
import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lgtv_privacy as core  # noqa: E402  (same folder; reuses pairing + config)

DEVMODE_APP = "com.palmdts.devmode"
STATE_FILE = core.CONFIG_DIR / "devmode.json"
LG_API = os.environ.get("LGTV_DEVMODE_API", "https://developer.lge.com").rstrip("/")
FULL_HOURS = 1000
# Apps that count as "nobody is watching anything": LG home, our launcher, screensaver.
IDLE_APPS = {"", "com.webos.app.home", "com.webos.app.homeconnect", "com.webos.app.screensaver",
             core.LAUNCHER_ID, DEVMODE_APP}
EX_TEMPFAIL = 75


def now():
    return time.time()


def fmt_age(ts):
    if not ts:
        return "never"
    d = now() - ts
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400 * 2:
        return f"{d / 3600:.1f} h ago"
    return f"{d / 86400:.1f} days ago"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except ValueError:
            pass
    return {}


def save_state(st):
    core.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))
    try:
        os.chmod(STATE_FILE, 0o600)  # holds the session token
    except OSError:
        pass


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# LG developer-site session check (optional; needs the session token)
# --------------------------------------------------------------------------
def lg_call(endpoint, token, timeout=15):
    url = f"{LG_API}/secure/{endpoint}.dev?sessionToken={urllib.parse.quote(token)}"
    req = urllib.request.Request(url, headers={"User-Agent": "lgtv-devmode-renew/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except ValueError:
        raise RuntimeError(f"unexpected reply from LG: {body[:200]!r}")


def hours_left(token):
    """Hours left on the session per LG's site, or None if it can't tell."""
    r = lg_call("CheckDevModeSession", token)
    m = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})\s*", str(r.get("errorMsg", "")))
    if r.get("result") != "success" or not m:
        raise RuntimeError(f"LG says: {r}")
    h, mi, s = map(int, m.groups())
    return h + mi / 60 + s / 3600


# --------------------------------------------------------------------------
# TV side
# --------------------------------------------------------------------------
def connect(args):
    """Open the paired TV, or return (None, reason) if it isn't reachable."""
    try:
        return core.open_tv(args), None
    except SystemExit as e:  # open_tv reports unreachable / unpaired this way
        return None, str(e).splitlines()[0]
    except (OSError, ConnectionError, TimeoutError) as e:
        return None, str(e)


def foreground(tv):
    try:
        r = tv.request("com.webos.applicationManager/getForegroundAppInfo")
        return r.get("appId", "") or ""
    except Exception:  # noqa: BLE001 - older firmware may not answer; treat as unknown
        return None


def extend_on_tv(tv, return_to=None, settle=6.0):
    r = tv.request("system.launcher/launch", {"id": DEVMODE_APP, "params": {"extend": True}})
    if r.get("returnValue") is False:
        raise RuntimeError(f"TV refused to open Developer Mode: {r.get('errorText') or r}. "
                           "Is the Developer Mode app still installed and signed in?")
    time.sleep(settle)  # the app does the extend as it starts
    if return_to and return_to not in IDLE_APPS:
        tv.request("system.launcher/launch", {"id": return_to})
    else:
        tv.request("system.launcher/close", {"id": DEVMODE_APP})


def toast(tv, text):
    try:
        tv.request("system.notifications/createToast", {"message": text})
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_renew(args):
    st = load_state()
    token = st.get("token")
    last_ok = st.get("last_ok")
    overdue = bool(last_ok) and now() - last_ok > args.warn_days * 86400

    # 1. Is it due?
    left = None
    if token:
        try:
            left = hours_left(token)
            st["hours_left"], st["hours_left_at"] = round(left, 1), now()
        except Exception as e:  # noqa: BLE001
            print(f"[{stamp()}] (couldn't ask LG for hours left: {e}; falling back to the calendar)")
    if not args.force:
        if left is not None:
            due = left < FULL_HOURS - args.every_days * 24
        else:
            due = not last_ok or now() - last_ok >= args.every_days * 86400
        if not due:
            why = f"{left:.0f} h left" if left is not None else f"last extended {fmt_age(last_ok)}"
            print(f"[{stamp()}] Not due ({why}; renews every {args.every_days} days).")
            save_state(st)
            return 0

    st["last_attempt"] = now()
    if args.dry_run:
        print(f"[{stamp()}] Due. Would open {DEVMODE_APP} with extend=true on the TV (dry run).")
        return 0

    # 2. Reach the TV.
    tv, err = connect(args)
    if tv is None:
        st["last_error"] = f"TV not reachable: {err}"
        save_state(st)
        if args.web_fallback and token:
            return web_fallback(st, token)
        print(f"[{stamp()}] TV not reachable ({err}). Will try again next run.")
        if overdue:
            print(f"WARNING: the last successful extend was {fmt_age(last_ok)}. "
                  f"Open Developer Mode on the TV and press Extend.", file=sys.stderr)
            return 2
        return EX_TEMPFAIL

    try:
        fg = foreground(tv)
        polite_grace = args.polite_grace_days * 86400
        really_late = last_ok and now() - last_ok > (args.every_days * 86400 + polite_grace)
        if args.polite and fg not in IDLE_APPS and fg is not None and not really_late:
            print(f"[{stamp()}] Due, but {fg} is on screen; not interrupting (--polite). Will try again.")
            st["last_error"] = f"deferred: {fg} in use"
            save_state(st)
            return EX_TEMPFAIL

        # 3. Extend, then put the screen back.
        extend_on_tv(tv, return_to=None if args.no_return else fg)

        # 4. Verify if we can.
        if token:
            time.sleep(2)
            try:
                after = hours_left(token)
                st["hours_left"], st["hours_left_at"] = round(after, 1), now()
                if after < FULL_HOURS - 24 and (left is None or after <= left + 0.5):
                    raise RuntimeError(f"LG still reports {after:.0f} h left; the extend didn't take")
                print(f"[{stamp()}] Extended. LG reports {after:.0f} h left.")
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001 - LG site down: not the TV's fault
                print(f"[{stamp()}] Extend sent; couldn't verify with LG ({e}).")
        else:
            print(f"[{stamp()}] Extend sent to the TV (the Developer Mode app opened with extend=true).")
        st["last_ok"], st["last_error"] = now(), None
        save_state(st)
        if args.toast:
            toast(tv, "Developer Mode session extended")
        return 0
    except Exception as e:  # noqa: BLE001
        st["last_error"] = str(e)
        save_state(st)
        print(f"[{stamp()}] Extend failed: {e}", file=sys.stderr)
        toast(tv, "Dev Mode extend failed - open Developer Mode and press Extend")
        return 1
    finally:
        tv.close()


def web_fallback(st, token):
    """LG's web reset endpoint. Reported to return success without always resetting
    the TV's own timer, so we only count it if a fresh check shows ~1000 h."""
    try:
        r = lg_call("ResetDevModeSession", token)
        after = hours_left(token)
    except Exception as e:  # noqa: BLE001
        print(f"[{stamp()}] TV off and web reset failed: {e}. Will retry.")
        return EX_TEMPFAIL
    st["hours_left"], st["hours_left_at"] = round(after, 1), now()
    if r.get("result") == "success" and after > FULL_HOURS - 24:
        st["last_ok"], st["last_error"] = now(), None
        save_state(st)
        print(f"[{stamp()}] TV was off; extended through LG's site. {after:.0f} h left.")
        return 0
    save_state(st)
    print(f"[{stamp()}] Web reset answered {r} but {after:.0f} h left; will retry on the TV.")
    return EX_TEMPFAIL


def cmd_status(args):
    st = load_state()
    print(f"Last successful extend: {fmt_age(st.get('last_ok'))}")
    print(f"Last attempt:           {fmt_age(st.get('last_attempt'))}")
    if st.get("last_error"):
        print(f"Last problem:           {st['last_error']}")
    tok = st.get("token")
    if tok:
        try:
            print(f"Hours left (LG):        {hours_left(tok):.0f} of ~{FULL_HOURS}")
        except Exception as e:  # noqa: BLE001
            print(f"Hours left (LG):        unknown ({e})")
    else:
        if st.get("last_ok"):
            est = FULL_HOURS - (now() - st["last_ok"]) / 3600
            print(f"Hours left (estimate):  ~{est:.0f}  (add a token for the exact figure)")
        print("Token:                  not set (optional; see fetch-token)")
    tv, err = connect(args)
    if tv is None:
        print(f"TV:                     not reachable now ({err})")
    else:
        print(f"TV:                     reachable, showing {foreground(tv) or 'nothing/unknown'}")
        tv.close()
    return 0


def cmd_set_token(args):
    tok = args.token.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{8,}", tok):
        raise SystemExit("That doesn't look like a Developer Mode session token.")
    st = load_state()
    st["token"] = tok
    save_state(st)
    try:
        print(f"Saved. LG reports {hours_left(tok):.0f} h left.")
    except Exception as e:  # noqa: BLE001
        print(f"Saved, but LG didn't confirm it yet ({e}).")
    return 0


def cmd_fetch_token(args):
    """Read the session token over Developer Mode's own SSH (user 'prisoner', port 9922).
    That's the same read-only access webOS Dev Manager uses; nothing is changed."""
    cfg = core.load_config()
    host = args.host or cfg.get("host")
    if not host:
        raise SystemExit("No TV known yet. Pair first, or pass --host.")
    key = os.path.expanduser(args.key)
    if not os.path.exists(key):
        raise SystemExit(
            f"No key at {key}. Turn on Key Server in the Developer Mode app, then:\n"
            f"  curl -o {shlex.quote(key)} http://{host}:9991/webos_rsa && chmod 600 {shlex.quote(key)}\n"
            "ssh will ask for the passphrase shown in the Developer Mode app.")
    cmd = ["ssh", "-i", key, "-p", str(args.port),
           "-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
           "-o", "StrictHostKeyChecking=accept-new", f"prisoner@{host}",
           "cat /var/luna/preferences/devmode_enabled"]
    print("Running: " + " ".join(shlex.quote(c) for c in cmd), file=sys.stderr)
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60).stdout
    except FileNotFoundError:
        raise SystemExit("No `ssh` on this machine. Paste the token with set-token instead.")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"ssh failed: {e.stderr.strip() or e}")
    args.token = out.strip()
    return cmd_set_token(args)


def cmd_schedule(args):
    py = shlex.quote(sys.executable)
    me = shlex.quote(str(Path(__file__).resolve()))
    flags = "renew --polite"
    logf = core.CONFIG_DIR / "devmode.log"
    env = f"LGTV_PRIVACY_HOME={shlex.quote(str(core.CONFIG_DIR))} " if "LGTV_PRIVACY_HOME" in os.environ else ""
    system = args.os or platform.system().lower()
    print("Runs every 3 hours; it only acts when an extend is due (every 7 days by default),")
    print("waits for the home screen unless it's 2+ days late, and logs to", logf)
    print()
    if system in ("linux", "darwin", "cron"):
        print("# cron (Linux, Raspberry Pi, macOS): run `crontab -e` and add this line")
        print(f"17 */3 * * * {env}{py} {me} {flags} >> {shlex.quote(str(logf))} 2>&1")
        print()
    if system in ("linux", "systemd"):
        print("# or systemd (user timer; survives reboots, catches up after sleep)")
        print("mkdir -p ~/.config/systemd/user && cd ~/.config/systemd/user")
        print("cat > lgtv-devmode.service <<'EOF'\n[Unit]\nDescription=Extend LG Developer Mode session\n"
              "Wants=network-online.target\nAfter=network-online.target\n\n[Service]\nType=oneshot\n"
              + (f"Environment=LGTV_PRIVACY_HOME={core.CONFIG_DIR}\n" if env else "")
              + f"ExecStart={sys.executable} {Path(__file__).resolve()} {flags}\n"
              "SuccessExitStatus=75\nEOF")
        print("cat > lgtv-devmode.timer <<'EOF'\n[Unit]\nDescription=Every 3 hours: LG Dev Mode extend check\n\n"
              "[Timer]\nOnCalendar=*-*-* 00/3:17:00\nPersistent=true\nRandomizedDelaySec=5min\n\n"
              "[Install]\nWantedBy=timers.target\nEOF")
        print("systemctl --user daemon-reload && systemctl --user enable --now lgtv-devmode.timer")
        print("loginctl enable-linger \"$USER\"   # Pi/headless: keep user timers running when logged out")
        print()
    if system in ("darwin", "launchd"):
        plist = Path.home() / "Library/LaunchAgents/io.github.lgprivacykit.devmode.plist"
        print(f"# or launchd (macOS): save as {plist}, then `launchctl load {plist}`")
        print(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.lgprivacykit.devmode</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string><string>{Path(__file__).resolve()}</string>
    <string>renew</string><string>--polite</string></array>
  <key>StartInterval</key><integer>10800</integer>
  <key>StandardOutPath</key><string>{logf}</string>
  <key>StandardErrorPath</key><string>{logf}</string>
</dict></plist>""")
        print()
    if system in ("windows", "schtasks"):
        exe = sys.executable if system == "windows" else r"C:\Path\To\python.exe"
        script = str(Path(__file__).resolve()) if system == "windows" else r"C:\Path\To\kit\lgtv_devmode.py"
        print("# Windows: run once in a normal (not admin) Command Prompt")
        print(f'schtasks /Create /TN "LG Dev Mode extend" /SC HOURLY /MO 3 /F '
              f'/TR "\\"{exe}\\" \\"{script}\\" {flags}"')
        print("# Then Task Scheduler > that task > Settings: tick 'Run task as soon as possible after a "
              "scheduled start is missed'.")
        print()
    print("Remove it again: delete the cron line / `systemctl --user disable --now lgtv-devmode.timer` /")
    print("`launchctl unload` the plist / `schtasks /Delete /TN \"LG Dev Mode extend\"`.")
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=argparse.SUPPRESS, help="TV IP (default: the one you paired)")
    common.add_argument("--insecure-port", action="store_true", default=argparse.SUPPRESS,
                        help="ws://:3000 only (testing with tools/mock_tv.py)")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("renew", parents=[common], help="extend the session if due (what the timer runs)")
    s.add_argument("--force", action="store_true", help="extend now even if not due")
    s.add_argument("--every-days", type=float, default=7, help="how often to extend (default 7; LG allows ~41)")
    s.add_argument("--warn-days", type=float, default=30,
                   help="exit 2 and warn if no success for this long (default 30)")
    s.add_argument("--polite", action="store_true",
                   help="don't interrupt an app that's playing; wait for home/standby")
    s.add_argument("--polite-grace-days", type=float, default=2,
                   help="with --polite, interrupt anyway once this many days late (default 2)")
    s.add_argument("--no-return", action="store_true", help="don't switch back to the previous app")
    s.add_argument("--toast", action="store_true", help="show a small notice on the TV when done")
    s.add_argument("--web-fallback", action="store_true",
                   help="if the TV is off and a token is set, try LG's web reset (counted only if verified)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_renew)

    s = sub.add_parser("status", parents=[common], help="last extend, hours left, TV reachable?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("set-token", parents=[common], help="store the Dev Mode session token (optional)")
    s.add_argument("token")
    s.set_defaults(fn=cmd_set_token)
    s = sub.add_parser("fetch-token", parents=[common], help="read the token over Dev Mode SSH (optional)")
    s.add_argument("--key", default="~/.ssh/lgtv_webos", help="Dev Mode key from http://TV:9991/webos_rsa")
    s.add_argument("--port", type=int, default=9922)
    s.set_defaults(fn=cmd_fetch_token)
    s = sub.add_parser("schedule", parents=[common], help="print the timer entry for this machine")
    s.add_argument("--os", choices=["linux", "darwin", "windows", "cron", "systemd", "launchd", "schtasks"])
    s.set_defaults(fn=cmd_schedule)

    args = p.parse_args()
    args.host = getattr(args, "host", None)
    args.insecure_port = getattr(args, "insecure_port", False)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
