#!/usr/bin/env python3
"""
setup.py - one run that sets up the whole kit on a stock (unrooted) LG TV.

    python3 setup.py                     # guided: finds the TV, asks before each step
    python3 setup.py --dry-run           # show everything it would do, change nothing
    python3 setup.py --yes --tv 192.168.1.50 --pihole http://192.168.1.2
    python3 setup.py status              # what's set up, what's not
    python3 setup.py undo                # put everything back

Steps (each can be skipped with --skip NAME or picked with --only NAME,NAME):
  pair      find the TV on your network and pair (accept the prompt on the TV)
  privacy   switch off Live Plus/ACR, ads, promotions, recommendations (backup first)
  consents  open TV Settings and walk you through the on-screen-only switches
  devmode   check Developer Mode, store its session token if possible, extend it now
  launcher  snapshot your apps, build Tiles Home, install it, open it
  root      ROOTED TVs only (--root): copy the kit to the TV over Homebrew Channel's SSH and
            run root/install.sh there: Home button and power-on open Tiles Home, blocklist in
            the TV's own hosts file, firewall, settings guard on the TV. No computer needed after.
  dns       add the blocklists to Pi-hole v6 or AdGuard Home over their APIs, then test
  router    write your router's anti-bypass rules with your TV's IP and MAC filled in
  schedule  add one timer: extend Developer Mode when due + re-check privacy settings
  report    write an HTML summary to ~/.config/lgtv-privacy/setup-report.html

Rooted TVs: `python3 setup.py --root` (one run from any laptop; nothing has to stay on after).
Without --root, nothing on the TV's firmware or system files is touched. Every change is either
an LG setting (backed up first), a Developer Mode app install, or something on
your own computer / Pi-hole / AdGuard that `undo` removes again.
Stdlib only (Python 3.8+).
"""
import argparse
import base64
import getpass
import html
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lgtv_privacy as core  # noqa: E402

STATE_FILE = core.CONFIG_DIR / "setup.json"
MARK = "lgtv-privacy-kit"
STEPS = ["pair", "privacy", "consents", "devmode", "launcher", "root", "dns", "router", "schedule", "report"]
PY = sys.executable


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
class Ctx:
    def __init__(self, a):
        self.a = a
        self.st = load_state()
        self.results = {}  # step -> (status, detail)

    def ok(self, step, detail):
        self.results[step] = ("done", detail)
        say(f"  ✓ {detail}")

    def skip(self, step, detail):
        self.results[step] = ("skipped", detail)
        say(f"  – {detail}")

    def todo(self, step, detail):
        self.results[step] = ("needs you", detail)
        say(f"  ! {detail}")

    def fail(self, step, detail):
        self.results[step] = ("failed", detail)
        say(f"  ✗ {detail}")

    def ask(self, q, default=True):
        if self.a.yes or self.a.dry_run:
            return default
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            r = input(f"  {q} {hint} ").strip().lower()
            if not r:
                return default
            if r in ("y", "yes", "n", "no"):
                return r.startswith("y")

    def tv_args(self, host=None):
        return argparse.Namespace(host=host or self.st.get("tv"), insecure_port=self.a.insecure_port,
                                  press_enter=self.a.press_enter)

    def flags(self):
        f = ["--host", self.st["tv"]] if self.st.get("tv") else []
        return f + (["--insecure-port"] if self.a.insecure_port else [])


def say(msg=""):
    print(msg, flush=True)


def head(n, title):
    say()
    say(f"[{n}/{len(STEPS)}] {title}")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(st):
    core.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def run(cmd, timeout=180, check=False, input_bytes=None):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_bytes)
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])}… failed: {err.strip() or out.strip()}")
    return p.returncode, out, err


def launch_points(tv):
    r = tv.request("com.webos.applicationManager/listLaunchPoints")
    return {lp.get("id"): lp for lp in r.get("launchPoints", [])}


# --------------------------------------------------------------------------
# 1. find + pair
# --------------------------------------------------------------------------
def discover(timeout=3.0):
    """SSDP search for LG webOS TVs (the same broadcast LG's phone app sends)."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:lge-com:service:webos-second-screen:1\r\n\r\n").encode()
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(0.5)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for _ in range(2):
            s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = s.recvfrom(2048)
            except socket.timeout:
                continue
            text = data.decode("utf-8", "replace")
            if "webos" in text.lower() or "lge" in text.lower():
                m = re.search(r"(?im)^server:\s*(.+)$", text)
                found[ip] = m.group(1).strip() if m else "LG webOS TV"
    except OSError:
        pass
    finally:
        s.close()
    return found


def step_pair(c):
    head(1, "Find and pair with the TV")
    host = c.a.tv or c.st.get("tv") or core.load_config().get("host")
    if not host:
        say("  Looking for LG TVs on your network…")
        tvs = discover()
        if not tvs:
            c.fail("pair", "No TV answered. Check it's on and on the same network, then re-run with --tv <ip>.")
            return False
        if len(tvs) == 1 or c.a.yes:
            host = next(iter(tvs))
        else:
            for i, (ip, name) in enumerate(tvs.items(), 1):
                say(f"    {i}. {ip}  {name}")
            host = list(tvs)[int(input("  Which one? ") or 1) - 1]
        say(f"  Found {host}")
    c.st["tv"] = host
    key = core.load_config().get("keys", {}).get(host)
    if key:
        try:
            tv = core.open_tv(c.tv_args(host))
            info = tv.request("system/getSystemInfo")
            tv.close()
            c.st["model"] = info.get("modelName")
            save_state(c.st)
            c.ok("pair", f"Already paired with {host} ({info.get('modelName', 'model unknown')}).")
            return True
        except SystemExit as e:
            c.fail("pair", str(e).splitlines()[0])
            return False
    if c.a.dry_run:
        c.skip("pair", f"Would pair with {host} (you accept the prompt on the TV).")
        return True
    say("  Pairing: accept the request on the TV within 60 seconds.")
    try:
        core.cmd_pair(argparse.Namespace(host=host, insecure_port=c.a.insecure_port))
    except SystemExit as e:
        c.fail("pair", str(e).splitlines()[0])
        return False
    save_state(c.st)
    c.ok("pair", f"Paired with {host}.")
    return True


# --------------------------------------------------------------------------
# 2. privacy settings
# --------------------------------------------------------------------------
def step_privacy(c):
    head(2, "Switch off ACR, ads, promotions and recommendations")
    tv = core.open_tv(c.tv_args())
    entries = core.selected(core.PROFILES)
    cur = core.read_current(tv, entries)
    tv.close()
    todo = [(cat, k, w, l) for _, cat, k, w, l in entries if cur.get((cat, k)) is not None and cur[(cat, k)] != w]
    readable = sum(v is not None for v in cur.values())
    if not todo:
        c.ok("privacy", f"All {readable} readable settings already off.")
        return
    for cat, k, w, l in todo:
        say(f"    {l}: {cur[(cat, k)]} → {w}")
    if c.a.dry_run:
        c.skip("privacy", f"Would switch off {len(todo)} setting(s), after a backup.")
        return
    if not c.ask(f"Switch off these {len(todo)}? (backed up, `undo` restores)"):
        c.skip("privacy", "Left as they are.")
        return
    before = set(core.BACKUP_DIR.glob("*.json")) if core.BACKUP_DIR.exists() else set()
    core.cmd_apply(argparse.Namespace(host=c.st["tv"], insecure_port=c.a.insecure_port, profile=None,
                                      dry_run=False, press_enter=c.a.press_enter))
    new = sorted(set(core.BACKUP_DIR.glob("*.json")) - before)
    if new:
        c.st.setdefault("backups", []).append(str(new[-1]))
        save_state(c.st)
    tv = core.open_tv(c.tv_args())
    after = core.read_current(tv, entries)
    tv.close()
    left = [l for _, cat, k, w, l in entries if after.get((cat, k)) is not None and after[(cat, k)] != w]
    if left:
        c.fail("privacy", f"{len(left)} didn't stick: {', '.join(left)}. Re-run with --press-enter.")
    else:
        c.ok("privacy", f"{len(todo)} switched off and verified; backup saved.")


# --------------------------------------------------------------------------
# 3. on-screen consents
# --------------------------------------------------------------------------
def step_consents(c):
    head(3, "On-screen privacy switches (LG only allows these with the remote)")
    say(core.MANUAL_STEPS)
    if c.a.dry_run or c.a.yes:
        c.todo("consents", "Do the on-screen steps above once with the remote (`lgtv_privacy.py manual` reprints them).")
        return
    if c.ask("Open Settings on the TV now?"):
        try:
            tv = core.open_tv(c.tv_args())
            tv.request("system.launcher/launch", {"id": "com.palm.app.settings"})
            tv.close()
        except Exception as e:  # noqa: BLE001
            say(f"  (couldn't open Settings: {e})")
    input("  Press Enter when you've been through the list (or to do it later)… ")
    if c.ask("Did you untick the User Agreements and ad options?", default=False):
        c.st["consents_done"] = time.time()
        save_state(c.st)
        c.ok("consents", "On-screen switches done.")
    else:
        c.todo("consents", "On-screen switches still to do (`lgtv_privacy.py manual`).")


# --------------------------------------------------------------------------
# 4. Developer Mode
# --------------------------------------------------------------------------
def step_devmode(c):
    head(4, "Developer Mode: check, token, extend")
    if c.a.root:
        c.skip("devmode", "Rooted: Homebrew Channel keeps Tiles Home installed, so the Dev Mode timer doesn't matter.")
        return
    tv = core.open_tv(c.tv_args())
    lps = launch_points(tv)
    tv.close()
    if "com.palmdts.devmode" not in lps:
        c.todo("devmode", "Developer Mode app not found. Install 'Developer Mode' from the LG Content Store, sign in "
               "with a free webostv.developer.lge.com account, turn Dev Mode Status on and Key Server on, then re-run "
               "`setup.py --only devmode,launcher,schedule`.")
        return
    key = Path(os.path.expanduser(c.a.dev_key))
    if not key.exists() and not c.a.dry_run and c.ask(f"Fetch the Dev Mode SSH key to {key}? (Key Server must be on)"):
        try:
            key.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(f"http://{c.st['tv']}:9991/webos_rsa", timeout=8) as r:
                key.write_bytes(r.read())
            os.chmod(key, 0o600)
            say(f"  Saved {key}. ssh will ask for the passphrase shown in the Developer Mode app.")
        except Exception as e:  # noqa: BLE001
            say(f"  (couldn't fetch the key: {e}. Turn on Key Server in the Developer Mode app.)")
    dev = json.loads((core.CONFIG_DIR / "devmode.json").read_text()) if (core.CONFIG_DIR / "devmode.json").exists() else {}
    if key.exists() and not dev.get("token") and not c.a.dry_run and shutil.which("ssh"):
        say("  Reading the session token over Dev Mode SSH (read-only)…")
        subprocess.run([PY, str(HERE / "lgtv_devmode.py"), *c.flags(), "fetch-token", "--key", str(key)])
    if c.a.dry_run:
        c.skip("devmode", "Would extend the Developer Mode session now.")
        return
    rc = subprocess.run([PY, str(HERE / "lgtv_devmode.py"), *c.flags(), "renew", "--force"]).returncode
    if rc == 0:
        c.ok("devmode", "Developer Mode session extended.")
    else:
        c.fail("devmode", f"Extend didn't work (exit {rc}). Open Developer Mode on the TV and check you're signed in.")


# --------------------------------------------------------------------------
# 5. Tiles Home
# --------------------------------------------------------------------------
def install_ipk_ssh(c, ipk, key):
    tmp = f"/media/developer/temp/{ipk.name}"
    base = ["-i", str(key), "-p", "9922", "-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
            "-o", "StrictHostKeyChecking=accept-new"]
    target = f"prisoner@{c.st['tv']}"
    say("  Copying the app to the TV over Dev Mode SSH…")
    rc, _, err = run(["ssh", *base, target, f"mkdir -p /media/developer/temp && cat > {shlex.quote(tmp)}"],
                     input_bytes=ipk.read_bytes(), timeout=120)
    if rc:
        raise RuntimeError(f"copy failed: {err.strip()}")
    payload = json.dumps({"id": "com.ares.defaultName", "ipkUrl": tmp, "subscribe": False})
    rc, out, err = run(["ssh", *base, target,
                        f"luna-send-pub -n 1 luna://com.webos.appInstallService/dev/install {shlex.quote(payload)}"],
                       timeout=120)
    if rc:
        raise RuntimeError(f"install call failed: {err.strip() or out.strip()}")


def step_launcher(c):
    head(5, "Tiles Home launcher")
    if c.a.dry_run:
        c.skip("launcher", "Would snapshot your apps and icons, rebuild the .ipk, install it, and open it.")
        return
    say("  Snapshotting your installed apps and icons…")
    core.cmd_export_tiles(argparse.Namespace(host=c.st["tv"], insecure_port=c.a.insecure_port, no_icons=False))
    rc, out, err = run([PY, str(HERE / "tools" / "build_ipk.py")])
    ipks = sorted((HERE / "dist").glob("*.ipk"), key=lambda p: p.stat().st_mtime)
    if rc or not ipks:
        c.fail("launcher", f"Building the .ipk failed: {err.strip() or out.strip()}")
        return
    ipk = ipks[-1]
    if c.a.root:
        c.ok("launcher", f"Built {ipk.name} with your apps; the root step installs it.")
        return
    tv = core.open_tv(c.tv_args())
    already = core.LAUNCHER_ID in launch_points(tv)
    tv.close()
    key = Path(os.path.expanduser(c.a.dev_key))
    method = None
    try:
        if shutil.which("ares-install") and c.a.ares_device:
            run(["ares-install", "-d", c.a.ares_device, str(ipk)], check=True)
            method = "ares-install"
        elif key.exists() and shutil.which("ssh"):
            install_ipk_ssh(c, ipk, key)
            method = "Dev Mode SSH"
    except RuntimeError as e:
        c.fail("launcher", f"Install failed ({e}). Drag {ipk} into webOS Dev Manager instead.")
        return
    if not method:
        c.todo("launcher", f"Built {ipk.name}. Install it with webOS Dev Manager (drag the file in), or re-run with "
               f"--ares-device <name> / --dev-key <path>.")
        return
    say(f"  Installed with {method}; waiting for the TV to list it…")
    for _ in range(30):
        tv = core.open_tv(c.tv_args())
        present = core.LAUNCHER_ID in launch_points(tv)
        if present:
            tv.request("system.launcher/launch", {"id": core.LAUNCHER_ID})
        tv.close()
        if present:
            c.ok("launcher", f"Tiles Home {'updated' if already else 'installed'} and opened. Pin it to a long-press number key.")
            return
        time.sleep(2)
    c.fail("launcher", "The TV didn't list Tiles Home after 60 s. Check Developer Mode is on.")


# --------------------------------------------------------------------------
# 6. DNS: Pi-hole v6 / AdGuard Home
# --------------------------------------------------------------------------
def http(method, url, body=None, headers=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:200]}


def blocklist_lines(name):
    p = HERE / "blocklist" / name
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))]


def pihole_rules(tier):
    rx = blocklist_lines(f"lg-{tier}.pihole-regex.txt")
    comp = [re.compile(r) for r in rx]
    exact = [d for d in blocklist_lines(f"lg-{tier}.domains.txt") if not any(r.search(d) for r in comp)]
    return rx, exact


class PiHole:
    def __init__(self, base, password):
        self.base = base.rstrip("/")
        code, r = http("POST", self.base + "/api/auth", {"password": password})
        sid = (r.get("session") or {}).get("sid")
        if code != 200 or not sid:
            raise RuntimeError(f"Pi-hole login failed ({code}): {r}. Is this Pi-hole v6 and the right password?")
        self.h = {"X-FTL-SID": sid}

    def call(self, method, path, body=None):
        return http(method, self.base + "/api" + path, body, self.h)

    def group(self, name):
        _, r = self.call("GET", "/groups")
        for g in r.get("groups", []):
            if g.get("name") == name:
                return g["id"]
        code, r = self.call("POST", "/groups", {"name": name, "comment": MARK, "enabled": True})
        for g in r.get("groups", []):
            if g.get("name") == name:
                return g["id"]
        raise RuntimeError(f"couldn't create group {name}: {code} {r}")

    def logout(self):
        try:
            self.call("DELETE", "/auth")
        except Exception:  # noqa: BLE001
            pass


def root_ssh_base(c):
    base = ["ssh", "-p", str(c.a.root_port), "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
    if c.a.root_key:
        base += ["-i", os.path.expanduser(c.a.root_key)]
    return base + [f"root@{c.st['tv']}"]


def step_root(c):
    head(6, "Rooted TV: Tiles Home on the Home button, blocking and guard on the TV")
    if not c.a.root:
        c.skip("root", "Not asked for. If Homebrew Channel has root on this TV, re-run with --root.")
        return
    if not shutil.which("ssh"):
        c.fail("root", "No ssh command on this computer. Use the by-hand steps in GUIDE.html (Rooted TV).")
        return
    if not sorted((HERE / "dist").glob("io.github.lgprivacykit.tiles_*.ipk")):
        run([PY, str(HERE / "tools" / "build_ipk.py")])
    rc, out, err = run([PY, str(HERE / "tools" / "build_root.py")])
    if rc:
        c.fail("root", f"Bundling failed: {err.strip() or out.strip()}")
        return
    bundle = HERE / "dist" / "lgkit-root.tar.gz"
    tiers = ",".join(c.a.root_tiers)
    args = ["--from", "/tmp/lgkit-src", "--tiers", tiers]
    if c.a.block_updates:
        args.append("--block-updates")
    if c.a.home_code:
        args += ["--home-code", str(c.a.home_code)]
    say(f"  Sends {bundle.name} ({bundle.stat().st_size // 1024} KB) to root@{c.st['tv']}:{c.a.root_port} and runs")
    say(f"  root/install.sh {' '.join(args)}")
    say("  Blocks: " + tiers + (" + LG firmware updates" if c.a.block_updates else "")
        + ". The firewall stops apps reaching public DNS directly. Undo: `python3 setup.py undo`.")
    if c.a.dry_run:
        c.skip("root", "Would install it on the TV.")
        return
    if not c.ask("Install on the TV now? (ssh asks for the root password if you haven't set up a key;"
                 " Homebrew Channel's default is alpine: change it)"):
        c.skip("root", "Not installed.")
        return
    remote = ("rm -rf /tmp/lgkit-src && mkdir -p /tmp/lgkit-src && tar -xzf - -C /tmp/lgkit-src && sh /tmp/lgkit-src/root/install.sh "
              + " ".join(shlex.quote(x) for x in args))
    p = subprocess.run(root_ssh_base(c) + [remote], input=bundle.read_bytes(), stdout=subprocess.PIPE, timeout=300)
    text = p.stdout.decode("utf-8", "replace")
    for line in text.splitlines():
        say("  | " + line)
    if p.returncode or "Done." not in text:
        c.fail("root", f"install.sh didn't finish (exit {p.returncode}). Check root SSH is on in Homebrew Channel.")
        return
    c.st["root"] = {"port": c.a.root_port, "key": c.a.root_key, "tiers": tiers, "block_updates": c.a.block_updates,
                    "at": int(time.time())}
    save_state(c.st)
    c.ok("root", "Installed on the TV. Press Home: Tiles Home should open. Status: ssh root@TV "
                 "sh /var/lib/webosbrew/lgkit/install.sh --status")


def step_dns(c):
    head(7, "DNS blocklists")
    tiers = c.a.tiers
    tv_only = [t for t in tiers if t in ("strict", "doh")]
    if c.a.pihole:
        rx_n = sum(len(pihole_rules(t)[0]) for t in tiers)
        ex_n = sum(len(pihole_rules(t)[1]) for t in tiers)
        say(f"  Pi-hole {c.a.pihole}: {rx_n} regex + {ex_n} exact deny rules ({', '.join(tiers)})"
            + (f"; {', '.join(tv_only)} only for the TV via group 'lg-tv'" if tv_only else ""))
        if c.a.dry_run:
            c.skip("dns", "Would add them over the Pi-hole v6 API (tagged so `undo` can remove them).")
            return
        pw = c.a.pihole_password or os.environ.get("PIHOLE_PASSWORD") or getpass.getpass("  Pi-hole password: ")
        try:
            ph = PiHole(c.a.pihole, pw)
            prev = c.st.get("dns") or {}
            added = [x for x in prev.get("added", [])] if prev.get("kind") == "pihole" else []
            gid = ph.group("lg-tv") if tv_only else None
            if gid is not None:
                code, r = ph.call("POST", "/clients", {"client": c.st["tv"], "comment": f"{MARK}: LG TV", "groups": [0, gid]})
                if code not in (200, 201) and "UNIQUE" not in json.dumps(r):
                    ph.call("PUT", f"/clients/{urllib.parse.quote(c.st['tv'], safe='')}",
                            {"comment": f"{MARK}: LG TV", "groups": [0, gid]})
            for t in tiers:
                groups = [gid] if t in tv_only else [0]
                rx, ex = pihole_rules(t)
                for kind, items in (("regex", rx), ("exact", ex)):
                    for d in items:
                        code, r = ph.call("POST", f"/domains/deny/{kind}",
                                          {"domain": d, "comment": f"{MARK} {t}", "groups": groups, "enabled": True})
                        if code in (200, 201):
                            if [kind, d] not in added:
                                added.append([kind, d])
                        elif "UNIQUE" not in json.dumps(r) and "already" not in json.dumps(r).lower():
                            say(f"    ! {d}: {code} {r}")
            ph.logout()
        except (RuntimeError, OSError) as e:
            c.fail("dns", str(e))
            return
        c.st["dns"] = {"kind": "pihole", "url": c.a.pihole, "added": added, "group": "lg-tv" if tv_only else None}
        save_state(c.st)
        host = urllib.parse.urlparse(c.a.pihole).hostname
    elif c.a.adguard:
        rules = []
        for t in tiers:
            for r in blocklist_lines(f"lg-{t}.adguard.txt"):
                rules.append(f"{r}$client='{c.st['tv']}'" if t in tv_only else r)
        block = [f"! >>> {MARK} (managed by setup.py; `setup.py undo` removes this block)"] + rules + [f"! <<< {MARK}"]
        say(f"  AdGuard Home {c.a.adguard}: {len(rules)} rules ({', '.join(tiers)})"
            + (f"; {', '.join(tv_only)} only for {c.st['tv']}" if tv_only else ""))
        if c.a.dry_run:
            c.skip("dns", "Would add them to Custom filtering rules as one marked block.")
            return
        pw = c.a.adguard_password or os.environ.get("ADGUARD_PASSWORD") or getpass.getpass("  AdGuard Home password: ")
        auth = {"Authorization": "Basic " + base64.b64encode(f"{c.a.adguard_user}:{pw}".encode()).decode()}
        base = c.a.adguard.rstrip("/")
        try:
            code, stt = http("GET", base + "/control/filtering/status", headers=auth)
            if code != 200:
                raise RuntimeError(f"AdGuard Home login failed ({code}): {stt}")
            current = strip_block(stt.get("user_rules") or [])
            code, r = http("POST", base + "/control/filtering/set_rules", {"rules": current + block}, auth)
            if code != 200:
                raise RuntimeError(f"set_rules failed ({code}): {r}")
        except (RuntimeError, OSError) as e:
            c.fail("dns", str(e))
            return
        c.st["dns"] = {"kind": "adguard", "url": c.a.adguard, "user": c.a.adguard_user}
        save_state(c.st)
        host = urllib.parse.urlparse(c.a.adguard).hostname
    else:
        c.todo("dns", "No --pihole or --adguard given. Add blocklist/lg-base.* by hand (GUIDE.html, step 4), "
               "or re-run with --pihole http://<ip> or --adguard http://<ip>:<port>.")
        return
    c.st["dns_ip"] = c.a.dns_ip or host
    save_state(c.st)
    say("  Testing the block…")
    rc = subprocess.run([PY, str(HERE / "tools" / "dns_check.py"), "--dns", c.st["dns_ip"]]).returncode
    if rc == 0:
        c.ok("dns", "Blocklists added and the check passed.")
    elif rc == 3:
        c.ok("dns", "Blocklists added. The check found ways around them: run the router step.")
    else:
        c.fail("dns", "Blocklists added, but the check couldn't reach the DNS server. Is --dns-ip right?")


def strip_block(lines):
    out, inside = [], False
    for l in lines:
        if l.startswith(f"! >>> {MARK}"):
            inside = True
            continue
        if l.startswith(f"! <<< {MARK}"):
            inside = False
            continue
        if not inside:
            out.append(l)
    return out


# --------------------------------------------------------------------------
# 7. router rules with your values filled in
# --------------------------------------------------------------------------
def mac_for(ip):
    for cmd in (["ip", "neigh", "show", ip], ["arp", "-n", ip], ["arp", "-a", ip]):
        rc, out, _ = run(cmd, timeout=5)
        m = re.search(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", out, re.I) if rc == 0 else None
        if m:
            return m.group(0).replace("-", ":").lower()
    return None


def step_router(c):
    head(8, "Router rules that stop the TV going around your DNS")
    tv = c.st["tv"]
    mac = c.a.tv_mac or mac_for(tv)
    dns = c.st.get("dns_ip") or c.a.dns_ip
    out = core.CONFIG_DIR / "router"
    say(f"  TV {tv}, MAC {mac or 'unknown'}, DNS filter {dns or 'unknown'}")
    if c.a.dry_run:
        c.skip("router", f"Would write filled-in router files to {out}.")
        return
    out.mkdir(parents=True, exist_ok=True)
    for f in (HERE / "router").iterdir():
        t = f.read_text().replace("TV_IP", tv)
        if mac:
            t = t.replace("TV_MAC", mac)
        if dns:
            t = t.replace("PIHOLE_IP", dns)
        (out / f.name).write_text(t)
    missing = [n for n, v in (("TV_MAC", mac), ("PIHOLE_IP", dns)) if not v]
    note = f" Still to fill in: {', '.join(missing)}." if missing else ""
    c.todo("router", f"Your router rules are in {out} (openwrt.sh, mikrotik.rsc, pfsense-opnsense.txt, "
           f"generic-router.txt). Paste the one for your router.{note}")


# --------------------------------------------------------------------------
# 8. one timer: Dev Mode extend + privacy guard
# --------------------------------------------------------------------------
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
PLIST = Path.home() / "Library" / "LaunchAgents" / "io.github.lgprivacykit.maintain.plist"
TASK = "LG Privacy Kit maintain"


def maintain_cmd():
    return [PY, str(HERE / "setup.py"), "maintain"]


def step_schedule(c):
    head(9, "Timer: extend Developer Mode when due, re-check privacy settings")
    if c.a.root or c.st.get("root"):
        c.skip("schedule", "Rooted: the TV re-checks its own settings, so no computer timer is needed.")
        return
    system = platform.system().lower()
    logf = core.CONFIG_DIR / "maintain.log"
    env = {"LGTV_PRIVACY_HOME": str(core.CONFIG_DIR)} if "LGTV_PRIVACY_HOME" in os.environ else {}
    cmd = maintain_cmd()
    say(f"  Every 3 hours: {' '.join(shlex.quote(x) for x in cmd)}  (log: {logf})")
    say("  Optional. Tiles Home already extends Developer Mode by itself each week when you open it, so a")
    say("  computer that is only on now and then is fine: the timer just catches up and re-checks settings.")
    if c.a.dry_run:
        c.skip("schedule", f"Would install it for {system}.")
        return
    if not c.ask("Install the timer on this computer?"):
        c.skip("schedule", "Not installed. `lgtv_devmode.py schedule` prints the lines to add by hand.")
        return
    try:
        if system == "linux" and shutil.which("systemctl") and run(["systemctl", "--user", "show-environment"])[0] == 0:
            SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
            envline = "".join(f"Environment={k}={v}\n" for k, v in env.items())
            (SYSTEMD_DIR / "lgtv-privacy-maintain.service").write_text(
                "[Unit]\nDescription=LG Privacy Kit: Dev Mode extend + privacy guard\nWants=network-online.target\n"
                f"After=network-online.target\n\n[Service]\nType=oneshot\n{envline}"
                f"ExecStart={' '.join(shlex.quote(x) for x in cmd)}\nSuccessExitStatus=75\n")
            (SYSTEMD_DIR / "lgtv-privacy-maintain.timer").write_text(
                "[Unit]\nDescription=Every 3 hours: LG Privacy Kit maintain\n\n[Timer]\nOnCalendar=*-*-* 00/3:17:00\n"
                "OnBootSec=10min\nPersistent=true\nRandomizedDelaySec=5min\n\n[Install]\nWantedBy=timers.target\n")
            run(["systemctl", "--user", "daemon-reload"], check=True)
            run(["systemctl", "--user", "enable", "--now", "lgtv-privacy-maintain.timer"], check=True)
            run(["loginctl", "enable-linger", getpass.getuser()])
            c.st["schedule"] = "systemd"
        elif system == "darwin":
            PLIST.parent.mkdir(parents=True, exist_ok=True)
            envxml = "".join(f"<key>{k}</key><string>{html.escape(v)}</string>" for k, v in env.items())
            PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.lgprivacykit.maintain</string>
  <key>ProgramArguments</key><array>{''.join(f'<string>{html.escape(x)}</string>' for x in cmd)}</array>
  <key>EnvironmentVariables</key><dict>{envxml}</dict>
  <key>StartInterval</key><integer>10800</integer><key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{html.escape(str(logf))}</string>
  <key>StandardErrorPath</key><string>{html.escape(str(logf))}</string>
</dict></plist>
""")
            run(["launchctl", "unload", str(PLIST)])
            run(["launchctl", "load", str(PLIST)], check=True)
            c.st["schedule"] = "launchd"
        elif system == "windows":
            tr = " ".join(f'"{x}"' for x in cmd)
            run(["schtasks", "/Create", "/TN", TASK, "/SC", "HOURLY", "/MO", "3", "/F", "/TR", tr], check=True)
            c.st["schedule"] = "schtasks"
        elif shutil.which("crontab"):
            _, cur, _ = run(["crontab", "-l"])
            lines = [l for l in cur.splitlines() if MARK not in l]
            envs = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
            lines.append(f"17 */3 * * * {envs}{' '.join(shlex.quote(x) for x in cmd)} >> {shlex.quote(str(logf))} 2>&1 # {MARK}")
            run(["crontab", "-"], input_bytes=("\n".join(lines) + "\n").encode(), check=True)
            c.st["schedule"] = "cron"
        else:
            raise RuntimeError("no systemd, launchd, Task Scheduler or crontab found")
    except RuntimeError as e:
        c.fail("schedule", f"{e}. Use `lgtv_devmode.py schedule` to add it by hand.")
        return
    save_state(c.st)
    c.ok("schedule", f"Timer installed ({c.st['schedule']}).")


def remove_schedule(kind):
    if kind == "systemd":
        run(["systemctl", "--user", "disable", "--now", "lgtv-privacy-maintain.timer"])
        for n in ("lgtv-privacy-maintain.service", "lgtv-privacy-maintain.timer"):
            if (SYSTEMD_DIR / n).exists():
                (SYSTEMD_DIR / n).unlink()
        run(["systemctl", "--user", "daemon-reload"])
    elif kind == "launchd":
        run(["launchctl", "unload", str(PLIST)])
        if PLIST.exists():
            PLIST.unlink()
    elif kind == "schtasks":
        run(["schtasks", "/Delete", "/TN", TASK, "/F"])
    elif kind == "cron":
        _, cur, _ = run(["crontab", "-l"])
        lines = [l for l in cur.splitlines() if MARK not in l]
        run(["crontab", "-"], input_bytes=("\n".join(lines) + "\n").encode())


# --------------------------------------------------------------------------
# 9. HTML report
# --------------------------------------------------------------------------
def step_report(c):
    head(10, "Summary")
    colours = {"done": "#0a7c66", "skipped": "#6b7280", "needs you": "#b45309", "failed": "#b91c1c"}
    rows = "".join(
        f'<tr><td>{html.escape(s)}</td><td><b style="color:{colours[r[0]]}">{html.escape(r[0])}</b></td>'
        f"<td>{html.escape(r[1])}</td></tr>" for s, r in c.results.items())
    todo = [s for s, r in c.results.items() if r[0] in ("needs you", "failed")]
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LG TV setup report</title><style>
:root{{color-scheme:light dark;--bg:light-dark(#fff,#0d1117);--ink:light-dark(#111,#e6edf3);--line:light-dark(#d0d7de,#30363d)}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif;padding:32px max(5vw,16px)}}
h1{{font-weight:400;font-size:34px;margin:0 0 6px}}table{{border-collapse:collapse;width:100%;max-width:1000px;margin-top:20px}}
td{{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}}td:first-child{{font-weight:600;white-space:nowrap}}
p{{max-width:70ch}}</style></head><body>
<h1>{'Everything is set up' if not todo else f'{len(todo)} step(s) need you'}</h1>
<p>TV {html.escape(str(c.st.get('tv')))} {html.escape(str(c.st.get('model') or ''))} · {time.strftime('%d %b %Y, %H:%M')}{' · dry run, nothing changed' if c.a.dry_run else ''}</p>
<table>{rows}</table>
<p>Re-run any step with <code>python3 setup.py --only STEP</code>. Undo everything with <code>python3 setup.py undo</code>.</p>
</body></html>"""
    core.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = core.CONFIG_DIR / "setup-report.html"
    out.write_text(page)
    for s, r in c.results.items():
        say(f"  {s:9} {r[0]:10} {r[1]}")
    say(f"\n  Report: {out}")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_run(a):
    steps = [s for s in STEPS if (not a.only or s in a.only) and s not in a.skip]
    if "report" not in steps:
        steps.append("report")
    c = Ctx(a)
    say("LG TV Privacy Kit setup" + ("  (dry run: nothing will be changed)" if a.dry_run else ""))
    if "pair" not in steps and not c.st.get("tv"):
        steps.insert(0, "pair")
    fns = {"pair": step_pair, "privacy": step_privacy, "consents": step_consents, "devmode": step_devmode,
           "launcher": step_launcher, "root": step_root, "dns": step_dns, "router": step_router, "schedule": step_schedule,
           "report": step_report}
    for s in steps:
        try:
            if s == "pair":
                if not step_pair(c):
                    step_report(c)
                    return 1
            elif a.dry_run and s in ("privacy", "devmode", "launcher") and not a.root and \
                    not core.load_config().get("keys", {}).get(c.st.get("tv")):
                c.skip(s, "Needs pairing first; a real run pairs, then does this step.")
            else:
                fns[s](c)
        except SystemExit as e:  # TV unreachable mid-run
            c.fail(s, str(e).splitlines()[0] if str(e) else "stopped")
        except KeyboardInterrupt:
            c.skip(s, "Interrupted.")
            break
        except Exception as e:  # noqa: BLE001
            c.fail(s, f"{type(e).__name__}: {e}")
    return 0 if all(r[0] != "failed" for r in c.results.values()) else 1


def cmd_maintain(a):
    st = load_state()
    flags = (["--host", st["tv"]] if st.get("tv") else []) + (["--insecure-port"] if a.insecure_port else [])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] maintain", flush=True)
    r1 = subprocess.run([PY, str(HERE / "lgtv_devmode.py"), *flags, "renew", "--polite"]).returncode
    r2 = subprocess.run([PY, str(HERE / "lgtv_privacy.py"), *flags, "guard"]).returncode
    for rc in (r1, r2):
        if rc not in (0, 75):
            return rc
    return 75 if 75 in (r1, r2) else 0


def cmd_status(a):
    st = load_state()
    say(f"TV:        {st.get('tv', 'not set up')} {st.get('model') or ''}")
    say(f"Backups:   {len(st.get('backups', []))}")
    say(f"Consents:  {'done ' + time.strftime('%d %b', time.localtime(st['consents_done'])) if st.get('consents_done') else 'not confirmed'}")
    d = st.get("dns") or {}
    say(f"DNS:       {d.get('kind', 'not added by setup')} {d.get('url', '')}")
    say(f"Timer:     {st.get('schedule', 'not installed')}")
    if st.get("root"):
        say(f"Rooted:    installed on the TV {time.strftime('%d %b %Y', time.localtime(st['root']['at']))} "
            f"(tiers {st['root']['tiers']})")
    if st.get("tv"):
        subprocess.run([PY, str(HERE / "lgtv_devmode.py"), "--host", st["tv"], *(["--insecure-port"] if a.insecure_port else []), "status"])
        subprocess.run([PY, str(HERE / "lgtv_privacy.py"), "--host", st["tv"], *(["--insecure-port"] if a.insecure_port else []), "audit"])
    return 0


def cmd_undo(a):
    st = load_state()
    say("Undo" + (" (dry run)" if a.dry_run else ""))
    if st.get("schedule"):
        say(f"  timer: remove ({st['schedule']})")
        if not a.dry_run:
            remove_schedule(st["schedule"])
            st.pop("schedule")
    d = st.get("dns") or {}
    if d.get("kind") == "adguard":
        say(f"  AdGuard Home: remove the {MARK} rule block")
        if not a.dry_run:
            pw = a.adguard_password or os.environ.get("ADGUARD_PASSWORD") or getpass.getpass("  AdGuard Home password: ")
            auth = {"Authorization": "Basic " + base64.b64encode(f"{d.get('user', 'admin')}:{pw}".encode()).decode()}
            code, stt = http("GET", d["url"].rstrip("/") + "/control/filtering/status", headers=auth)
            if code == 200:
                http("POST", d["url"].rstrip("/") + "/control/filtering/set_rules", {"rules": strip_block(stt.get("user_rules") or [])}, auth)
                st.pop("dns")
            else:
                say(f"    ! couldn't log in ({code})")
    elif d.get("kind") == "pihole":
        say(f"  Pi-hole: remove {len(d.get('added', []))} rules" + (", group lg-tv and the TV client" if d.get("group") else ""))
        if not a.dry_run:
            pw = a.pihole_password or os.environ.get("PIHOLE_PASSWORD") or getpass.getpass("  Pi-hole password: ")
            ph = PiHole(d["url"], pw)
            for kind, dom in d.get("added", []):
                ph.call("DELETE", f"/domains/deny/{kind}/{urllib.parse.quote(dom, safe='')}")
            if d.get("group"):
                ph.call("DELETE", f"/clients/{urllib.parse.quote(st['tv'], safe='')}")
                ph.call("DELETE", f"/groups/{urllib.parse.quote(d['group'], safe='')}")
            ph.logout()
            st.pop("dns")
    r = st.get("root")
    if r:
        say("  rooted TV: run install.sh --uninstall over SSH (hosts, firewall, agent, boot hook)")
        if not a.dry_run and st.get("tv"):
            cmd = ["ssh", "-p", str(r.get("port", 22)), "-o", "ConnectTimeout=10"] + \
                  (["-i", os.path.expanduser(r["key"])] if r.get("key") else []) + \
                  [f"root@{st['tv']}", "f=/var/lib/webosbrew/lgkit/install.sh; if [ -f $f ]; then sh $f --uninstall; "
                   "else echo 'already removed from the TV'; fi"]
            if subprocess.run(cmd).returncode == 0:
                st.pop("root")
            else:
                say("    ! couldn't reach the TV; run that command on it yourself")
    for b in reversed(st.get("backups", [])):
        say(f"  TV settings: restore {b}")
        if not a.dry_run:
            core.cmd_restore(argparse.Namespace(host=st.get("tv"), insecure_port=a.insecure_port, file=b,
                                                dry_run=False, press_enter=a.press_enter))
    if not a.dry_run:
        st["backups"] = []
    say("  Tiles Home: left installed. Remove it from the TV's app list, or with Dev Manager.")
    say("  On-screen consents: left as you set them.")
    if not a.dry_run:
        save_state(st)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="run", choices=["run", "maintain", "status", "undo"])
    p.add_argument("--tv", help="TV IP (default: search the network)")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask; take the default at each prompt")
    p.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    p.add_argument("--only", type=lambda s: s.split(","), default=[], help="comma list of steps to run")
    p.add_argument("--skip", type=lambda s: s.split(","), default=[], help="comma list of steps to skip")
    p.add_argument("--pihole", help="Pi-hole v6 address, e.g. http://192.168.1.2")
    p.add_argument("--pihole-password", help="or set PIHOLE_PASSWORD; asked if missing")
    p.add_argument("--adguard", help="AdGuard Home address, e.g. http://192.168.1.2:3000")
    p.add_argument("--adguard-user", default="admin")
    p.add_argument("--adguard-password", help="or set ADGUARD_PASSWORD; asked if missing")
    p.add_argument("--tiers", type=lambda s: s.split(","), default=["base"],
                   help="blocklist tiers: base (default), strict, doh; strict/doh apply to the TV only")
    p.add_argument("--dns-ip", help="IP your devices use for DNS, if different from the Pi-hole/AdGuard URL host")
    p.add_argument("--tv-mac", help="TV MAC for router rules (found automatically if possible)")
    p.add_argument("--dev-key", default="~/.ssh/lgtv_webos", help="Developer Mode SSH key (fetched if missing)")
    p.add_argument("--ares-device", help="install with LG's ares-install to this device name instead of SSH")
    p.add_argument("--root", action="store_true",
                   help="the TV is rooted (Homebrew Channel): install the on-TV part over root SSH")
    p.add_argument("--root-port", type=int, default=22, help="Homebrew Channel SSH port (default 22)")
    p.add_argument("--root-key", help="SSH key for root@TV (default: ssh's own, or the password)")
    p.add_argument("--root-tiers", type=lambda s: s.split(","), default=["base", "doh"],
                   help="tiers for the TV's own hosts list (default base,doh; add strict for the most, "
                        "which also stops the Content Store and app updates)")
    p.add_argument("--block-updates", action="store_true",
                   help="rooted: also block LG firmware updates (keeps root; you miss LG's security fixes)")
    p.add_argument("--home-code", type=int, help="rooted: your remote's Home key code (root/find-home-key.js)")
    p.add_argument("--press-enter", action="store_true", help="newer firmware: confirm the hidden alert")
    p.add_argument("--insecure-port", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args()
    for s in a.only + a.skip:
        if s not in STEPS:
            p.error(f"unknown step {s!r}; steps are {', '.join(STEPS)}")
    for t in a.tiers + a.root_tiers:
        if t not in ("base", "strict", "doh"):
            p.error(f"unknown tier {t!r}")
    sys.exit({"run": cmd_run, "maintain": cmd_maintain, "status": cmd_status, "undo": cmd_undo}[a.cmd](a))


if __name__ == "__main__":
    main()
