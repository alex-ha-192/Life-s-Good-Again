#!/usr/bin/env python3
"""
lgtv_privacy.py - pair with an LG webOS TV over your LAN, audit and switch off
ads / promotions / ACR ("Live Plus"), and help set up the Tiles Home launcher.

Stdlib only (Python 3.8+). Nothing is changed on the TV unless you run
`apply` or `restore`, and `apply --dry-run` shows exactly what would be sent.

Quick start:
    python3 lgtv_privacy.py pair  --host 192.168.1.50   # accept the prompt on the TV
    python3 lgtv_privacy.py info
    python3 lgtv_privacy.py audit
    python3 lgtv_privacy.py apply --dry-run
    python3 lgtv_privacy.py apply                       # writes a backup first
    python3 lgtv_privacy.py restore                     # put the old values back
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("LGTV_PRIVACY_HOME", Path.home() / ".config" / "lgtv-privacy"))
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_DIR = CONFIG_DIR / "backups"
LAUNCHER_ID = "io.github.lgprivacykit.tiles"

# --------------------------------------------------------------------------
# What we change. Each entry: (profile, category, key, desired value, label)
# Keys differ between firmwares; anything the TV doesn't report is skipped.
# Sources: webOS homebrew community notes (see GUIDE.html for links).
# --------------------------------------------------------------------------
SETTINGS = [
    ("privacy", "option",  "livePlus",              "off", "Live Plus (ACR: recognises what you watch, incl. HDMI)"),
    ("privacy", "other",   "contentRecommendation", "off", "Content recommendations based on viewing"),
    ("ads",     "general", "homePromotion",         "off", "Home screen promotion / ad banner"),
    ("ads",     "option",  "livePromotion",         "off", "Live TV promotions"),
    ("ads",     "option",  "webOSPromotionVideo",   "off", "webOS promo video"),
    ("ads",     "option",  "promotionStreamer",     "off", "Promotion streamer overlay"),
    ("ads",     "general", "sportsAlarm",           "off", "Sports alerts pop-ups"),
    ("home",    "general", "homeAutoLaunch",        "off", "Don't pop LG Home up at power-on"),
    ("home",    "general", "noSignalScreenSaver",   "off", "No-signal 'gallery' screensaver (downloads images)"),
]
PROFILES = ["privacy", "ads", "home"]

MANUAL_STEPS = """\
Things the TV only lets you change on-screen (do these once with the remote).
Menu names are for webOS 23; wording varies slightly by region.

  1. Settings > All Settings > Support > Privacy & Terms > User Agreements
       Untick everything optional: Viewing Information, Voice Information,
       Interest-Based Advertising, Content Recommendation, Live Plus, etc.
       (Keep only the core 'Terms of Use' if you still want the app store.)
       LG can re-tick these after a network change or update: the scheduled
       guard re-checks the settings it can read and warns you on the TV.
  2. Settings > All Settings > General > System > Additional Settings > Advertisement
       Turn ON 'Limit Ad Tracking' (US) and OFF 'Personalised Advertising'.
       Use 'Reset Advertising ID' once.
  3. Settings > All Settings > General > System > Additional Settings > Home Settings
       Turn OFF 'Home Promotion', 'Content Recommendation', 'Home Auto Launch'.
  4. Settings > All Settings > General > AI Service (or AI Settings)
       Turn OFF 'AI Recommendation', 'Smart Tips', voice data collection and
       any 'Always Ready' / always-listening option.
  5. Settings > All Settings > General > Devices > TV Management
       OFF: 'Sharing Device Info' / 'Device Connection Settings' you don't use.
       Keep 'TV On With Mobile' ON (the kit's timer needs it).
  6. Settings > All Settings > Support > Software Update
       Your call: Auto Update ON keeps LG's security fixes coming (recommended).
  7. Magic Remote microphone: if you never use voice, Settings > Accessibility /
       AI Settings > Voice: turn off voice recognition. The DNS list also blocks
       LG's voice-data endpoints.
"""


def log(msg):
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Minimal websocket client (RFC 6455, client side, text frames only)
# --------------------------------------------------------------------------
class WebSocket:
    def __init__(self, url, timeout=10):
        assert url.startswith(("ws://", "wss://")), url
        secure = url.startswith("wss://")
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        port = int(port or (443 if secure else 80))
        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            # LG TVs use a self-signed certificate; we pin nothing but we also
            # never send anything sensitive except the pairing key.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = self._read_until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise ConnectionError(f"websocket upgrade failed: {status!r}")
        expect = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if expect not in head:
            raise ConnectionError("bad Sec-WebSocket-Accept from TV")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("TV closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read_until(self, marker):
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("TV closed the connection")
            self.buf += chunk
        i = self.buf.index(marker) + len(marker)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def _send_frame(self, opcode, data):
        header = bytes([0x80 | opcode])
        n = len(data)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(header + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode())

    def recv(self, timeout=None):
        self.sock.settimeout(timeout)
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, fin = b1 & 0x0F, b1 & 0x80
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if b2 & 0x80:
                mask = self._read(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                data = self._read(n)
            if opcode == 0x9:  # ping
                self._send_frame(0xA, data)
                continue
            if opcode == 0x8:
                raise ConnectionError("TV closed the websocket")
            if opcode in (0x0, 0x1, 0x2):
                message += data
                if fin:
                    return message.decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# SSAP client
# --------------------------------------------------------------------------
class TV:
    def __init__(self, host, client_key=None, secure=True):
        self.host = host
        self.client_key = client_key
        self.secure = secure
        self.ws = None
        self.n = 0

    def connect(self, pairing_timeout=60):
        urls = [f"wss://{self.host}:3001/", f"ws://{self.host}:3000/"] if self.secure else [f"ws://{self.host}:3000/"]
        last = None
        for url in urls:
            try:
                self.ws = WebSocket(url)
                break
            except (OSError, ConnectionError) as e:
                last = e
        if self.ws is None:
            raise SystemExit(
                f"Could not reach {self.host} on port 3001/3000 ({last}).\n"
                "Check the IP, that the TV is on, and that "
                "Settings > General > Devices > External Devices > 'TV On With Mobile' / "
                "'LG Connect Apps' is enabled."
            )
        manifest = json.loads((HERE / "tools" / "manifest.json").read_text(encoding="utf-8"))
        payload = {"forcePairing": False, "pairingType": "PROMPT", "manifest": manifest}
        if self.client_key:
            payload["client-key"] = self.client_key
        self.ws.send(json.dumps({"type": "register", "id": "register_0", "payload": payload}))
        deadline = time.time() + pairing_timeout
        prompted = False
        while time.time() < deadline:
            msg = json.loads(self.ws.recv(timeout=pairing_timeout))
            if msg.get("type") == "response" and msg.get("payload", {}).get("pairingType") == "PROMPT":
                if not prompted:
                    log(">> Accept the connection request on the TV screen (you have 60 s)...")
                    prompted = True
                continue
            if msg.get("type") == "registered":
                self.client_key = msg["payload"]["client-key"]
                return self.client_key
            if msg.get("type") == "error":
                raise SystemExit(f"TV refused pairing: {msg.get('error')}")
        raise SystemExit("Timed out waiting for pairing.")

    def request(self, uri, payload=None, timeout=10):
        self.n += 1
        rid = f"req_{self.n}"
        msg = {"type": "request", "id": rid, "uri": uri if uri.startswith("ssap://") else "ssap://" + uri}
        if payload is not None:
            msg["payload"] = payload
        self.ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = json.loads(self.ws.recv(timeout=timeout))
            if resp.get("id") != rid:
                continue
            if resp.get("type") == "error":
                return {"returnValue": False, "errorText": resp.get("error"), **(resp.get("payload") or {})}
            return resp.get("payload", {})
        raise TimeoutError(uri)

    def luna(self, uri, params, press_enter=False):
        """Call an internal luna:// API through the alert-button mechanism.

        This is the long-standing trick used by bscpylgtv / Home Assistant
        users: create a system alert whose button, close and fail actions all
        point at the luna URI, then close it. The TV never returns the luna
        result, so callers must verify by reading the setting back.
        """
        luna_uri = "luna://" + uri
        payload = {
            "message": " ",
            "buttons": [{"label": "", "onClick": luna_uri, "params": params}],
            "onclose": {"uri": luna_uri, "params": params},
            "onfail": {"uri": luna_uri, "params": params},
        }
        r = self.request("system.notifications/createAlert", payload)
        alert_id = r.get("alertId")
        if not alert_id:
            raise RuntimeError(f"createAlert failed: {r}")
        if press_enter:
            time.sleep(0.6)
            self.button("ENTER")
            time.sleep(0.4)
        self.request("system.notifications/closeAlert", {"alertId": alert_id})

    def button(self, name):
        r = self.request("com.webos.service.networkinput/getPointerInputSocket")
        path = r.get("socketPath")
        if not path:
            raise RuntimeError(f"no pointer socket: {r}")
        ws = WebSocket(path)
        ws.send(f"type:button\nname:{name}\n\n")
        time.sleep(0.2)
        ws.close()

    def get_settings(self, category, keys):
        r = self.request("settings/getSystemSettings", {"category": category, "keys": keys})
        if not r.get("returnValue", True) and "settings" not in r:
            return None, r.get("errorText") or r.get("errorCode")
        return r.get("settings", {}), None

    def close(self):
        if self.ws:
            self.ws.close()


# --------------------------------------------------------------------------
# config helpers
# --------------------------------------------------------------------------
def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def open_tv(args):
    cfg = load_config()
    host = args.host or cfg.get("host")
    if not host:
        raise SystemExit("No TV known yet. Run:  lgtv_privacy.py pair --host <tv-ip>")
    key = cfg.get("keys", {}).get(host)
    if not key:
        raise SystemExit(f"Not paired with {host}. Run:  lgtv_privacy.py pair --host {host}")
    tv = TV(host, key, secure=not args.insecure_port)
    new_key = tv.connect()
    if new_key != key:
        cfg.setdefault("keys", {})[host] = new_key
        save_config(cfg)
    return tv


def selected(profiles):
    return [s for s in SETTINGS if s[0] in profiles]


def read_current(tv, entries):
    """Return {(cat,key): value or None-if-unsupported}."""
    by_cat = {}
    for _, cat, key, _, _ in entries:
        by_cat.setdefault(cat, []).append(key)
    out = {}
    for cat, keys in by_cat.items():
        vals, err = tv.get_settings(cat, keys)
        if vals is None:
            # Some firmwares reject a batch if one key is unknown; try one by one.
            vals = {}
            for k in keys:
                v, _ = tv.get_settings(cat, [k])
                if v and k in v:
                    vals[k] = v[k]
        for k in keys:
            out[(cat, k)] = vals.get(k) if vals else None
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_pair(args):
    cfg = load_config()
    tv = TV(args.host, cfg.get("keys", {}).get(args.host), secure=not args.insecure_port)
    key = tv.connect()
    cfg.setdefault("keys", {})[args.host] = key
    cfg["host"] = args.host
    save_config(cfg)
    tv.request("system.notifications/createToast", {"message": "Paired with lgtv_privacy"})
    tv.close()
    print(f"Paired with {args.host}. Key stored in {CONFIG_FILE}")


def cmd_info(args):
    tv = open_tv(args)
    sysinfo = tv.request("system/getSystemInfo")
    sw = tv.request("com.webos.service.update/getCurrentSWInformation")
    tv.close()
    model = sysinfo.get("modelName", "?")
    fw = sw.get("major_ver", "?") + "." + sw.get("minor_ver", "?") if sw.get("major_ver") else sw.get("firmware_revision", "?")
    webos = sw.get("product_name", "?")
    print(f"Model:        {model}")
    print(f"webOS:        {webos}")
    print(f"Firmware:     {fw}")
    print(f"Country:      {sw.get('country', '?')} / device id hidden")
    print()
    print("Rooting check (read-only advice):")
    print(f"  Look up your exact model + firmware at https://cani.rootmy.tv/")
    print("  If it says a method works, DON'T update the TV until you've decided.")
    if args.json:
        print(json.dumps({"system": sysinfo, "software": sw}, indent=2))


def cmd_audit(args):
    tv = open_tv(args)
    entries = selected(args.profile or PROFILES)
    cur = read_current(tv, entries)
    tv.close()
    print(f"{'setting':42} {'now':>8}  {'wanted':>6}")
    print("-" * 62)
    for prof, cat, key, want, label in entries:
        v = cur.get((cat, key))
        mark = "n/a" if v is None else ("ok" if v == want else "CHANGE")
        print(f"{label[:42]:42} {str(v) if v is not None else '-':>8}  {want:>6}  {mark}")
    print("\n'n/a' = this firmware doesn't expose that key (nothing to do).")
    print("Run `apply --dry-run` to preview changes, then `apply`.\n")
    print(MANUAL_STEPS)


def cmd_apply(args):
    tv = open_tv(args)
    entries = selected(args.profile or PROFILES)
    cur = read_current(tv, entries)
    todo = [(c, k, w, l) for _, c, k, w, l in entries if cur.get((c, k)) is not None and cur[(c, k)] != w]
    if not todo:
        print("Nothing to change; everything readable is already off.")
        tv.close()
        return
    print("Will set:")
    for c, k, w, l in todo:
        print(f"  {c}.{k}: {cur[(c, k)]} -> {w}   ({l})")
    if args.dry_run:
        print("\n(dry run, nothing sent)")
        tv.close()
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{tv.host}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(json.dumps([{"category": c, "key": k, "value": cur[(c, k)]} for c, k, _, _ in todo], indent=2))
    print(f"\nBackup written: {backup}")
    for c, k, w, _ in todo:
        tv.luna("com.webos.settingsservice/setSystemSettings", {"category": c, "settings": {k: w}},
                press_enter=args.press_enter)
        time.sleep(0.5)
    after = read_current(tv, [(None, c, k, w, l) for c, k, w, l in todo])
    tv.close()
    failed = [(c, k) for c, k, w, _ in todo if after.get((c, k)) != w]
    print()
    for c, k, w, _ in todo:
        print(f"  {c}.{k}: now {after.get((c, k))} {'OK' if (c, k) not in failed else 'NOT CHANGED'}")
    if failed:
        print("\nSome values didn't stick. Try again with --press-enter (newer firmware may\n"
              "want the hidden alert confirmed), or set them by hand, see `manual`.")
    else:
        print("\nDone. Now do the on-screen steps:  lgtv_privacy.py manual")


def cmd_restore(args):
    files = sorted(BACKUP_DIR.glob("*.json"))
    if args.file:
        path = Path(args.file)
    elif files:
        path = files[-1]
    else:
        raise SystemExit("No backups found.")
    items = json.loads(path.read_text())
    print(f"Restoring from {path}:")
    for it in items:
        print(f"  {it['category']}.{it['key']} -> {it['value']}")
    if args.dry_run:
        print("(dry run)")
        return
    tv = open_tv(args)
    for it in items:
        tv.luna("com.webos.settingsservice/setSystemSettings",
                {"category": it["category"], "settings": {it["key"]: it["value"]}}, press_enter=args.press_enter)
        time.sleep(0.5)
    tv.close()
    print("Sent. Run `audit` to check.")


def cmd_guard(args):
    """Re-check every readable privacy setting and quietly put back any the TV turned on again.
    Meant for the scheduled timer; prints one line per change. Exit 0 ok, 3 something drifted and
    couldn't be fixed, 75 TV not reachable."""
    try:
        tv = open_tv(args)
    except SystemExit as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] guard: TV not reachable ({str(e).splitlines()[0]})")
        return 75
    entries = selected(PROFILES)
    cur = read_current(tv, entries)
    drift = [(c, k, w, l) for _, c, k, w, l in entries if cur.get((c, k)) is not None and cur[(c, k)] != w]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if not drift:
        print(f"[{stamp}] guard: all {sum(v is not None for v in cur.values())} readable settings still off.")
        tv.close()
        return 0
    for c, k, w, _ in drift:
        tv.luna("com.webos.settingsservice/setSystemSettings", {"category": c, "settings": {k: w}},
                press_enter=getattr(args, "press_enter", False))
        time.sleep(0.5)
    after = read_current(tv, [(None, c, k, w, l) for c, k, w, l in drift])
    fixed = [l for c, k, w, l in drift if after.get((c, k)) == w]
    stuck = [l for c, k, w, l in drift if after.get((c, k)) != w]
    for l in fixed:
        print(f"[{stamp}] guard: turned back off: {l}")
    for l in stuck:
        print(f"[{stamp}] guard: COULD NOT turn off: {l}")
    try:
        msg = f"Privacy kit: {len(fixed)} setting(s) LG turned back on were switched off again"
        if stuck:
            msg = f"Privacy kit: {len(stuck)} setting(s) are back on. Check Settings > Privacy"
        tv.request("system.notifications/createToast", {"message": msg})
    except Exception:  # noqa: BLE001
        pass
    tv.close()
    return 3 if stuck else 0


def cmd_manual(args):
    print(MANUAL_STEPS)


def cmd_apps(args):
    tv = open_tv(args)
    r = tv.request("com.webos.applicationManager/listLaunchPoints")
    tv.close()
    for lp in sorted(r.get("launchPoints", []), key=lambda x: x.get("title", "")):
        print(f"{lp.get('id', ''):45} {lp.get('title', '')}")


def cmd_export_tiles(args):
    """Write launcher/apps.json (+ icons) from the apps installed on the TV."""
    tv = open_tv(args)
    r = tv.request("com.webos.applicationManager/listLaunchPoints")
    tv.close()
    out_dir = HERE / "launcher"
    icon_dir = out_dir / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    apps = []
    skip = {LAUNCHER_ID}
    for lp in r.get("launchPoints", []):
        aid = lp.get("id")
        if not aid or aid in skip:
            continue
        entry = {"id": aid, "title": lp.get("title", aid), "color": lp.get("bgColor") or None}
        icon = lp.get("largeIcon") or lp.get("icon")
        if icon and not args.no_icons and icon.startswith("http"):
            name = hashlib.sha1(aid.encode()).hexdigest()[:12] + ".png"
            try:
                # Icons are served by the TV itself on the LAN.
                with urllib.request.urlopen(icon, timeout=5) as resp:
                    (icon_dir / name).write_bytes(resp.read())
                entry["icon"] = "icons/" + name
            except Exception as e:  # noqa: BLE001
                log(f"  (icon for {aid} skipped: {e})")
        apps.append(entry)
    apps.sort(key=lambda a: a["title"].lower())
    (out_dir / "apps.json").write_text(json.dumps(apps, indent=1, ensure_ascii=False))
    print(f"Wrote {len(apps)} apps to {out_dir / 'apps.json'}. Rebuild the .ipk to include them:")
    print("  python3 tools/build_ipk.py")


def cmd_launch(args):
    tv = open_tv(args)
    r = tv.request("system.launcher/launch", {"id": args.app or LAUNCHER_ID})
    tv.close()
    print(r)


def cmd_renew(args):
    """Kept for compatibility: forwards to lgtv_devmode.py (the maintained helper)."""
    import lgtv_devmode
    argv = []
    if args.host:
        argv += ["--host", args.host]
    if args.insecure_port:
        argv += ["--insecure-port"]
    argv += ["renew", "--force"]
    log("(renew now lives in lgtv_devmode.py; running: lgtv_devmode.py " + " ".join(argv) + ")")
    sys.argv = ["lgtv_devmode.py"] + argv
    lgtv_devmode.main()


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=argparse.SUPPRESS, help="TV IP (remembered after pairing)")
    common.add_argument("--insecure-port", action="store_true", default=argparse.SUPPRESS,
                        help="use ws://:3000 only (old firmware / testing)")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *a, **k: _add(*a, parents=[common], **k)

    s = sub.add_parser("pair", help="pair with the TV (accept prompt on screen)")
    s.set_defaults(fn=cmd_pair)
    s = sub.add_parser("info", help="model / firmware / rooting pointer")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_info)
    for name, fn, hlp in (("audit", cmd_audit, "read current privacy/ad settings (read-only)"),
                          ("apply", cmd_apply, "turn off ads/ACR/promotions (writes backup)")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--profile", action="append", choices=PROFILES,
                       help="limit to a group; repeatable (default: all)")
        if name == "apply":
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--press-enter", action="store_true",
                           help="also press OK on the hidden alert (some newer firmware needs it)")
        s.set_defaults(fn=fn)
    s = sub.add_parser("restore", help="restore values from the latest (or given) backup")
    s.add_argument("--file")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--press-enter", action="store_true")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("guard", help="re-apply any privacy setting the TV switched back on (for timers)")
    s.add_argument("--press-enter", action="store_true")
    s.set_defaults(fn=cmd_guard)
    s = sub.add_parser("manual", help="print the on-screen steps the API can't do")
    s.set_defaults(fn=cmd_manual)
    s = sub.add_parser("apps", help="list installed apps and their IDs")
    s.set_defaults(fn=cmd_apps)
    s = sub.add_parser("export-tiles", help="snapshot installed apps into the launcher")
    s.add_argument("--no-icons", action="store_true")
    s.set_defaults(fn=cmd_export_tiles)
    s = sub.add_parser("launch", help="open Tiles Home (or --app ID) on the TV")
    s.add_argument("--app")
    s.set_defaults(fn=cmd_launch)
    s = sub.add_parser("renew", help="extend Developer Mode now (same as lgtv_devmode.py renew --force)")
    s.set_defaults(fn=cmd_renew)

    args = p.parse_args()
    args.host = getattr(args, "host", None)
    args.insecure_port = getattr(args, "insecure_port", False)
    if args.cmd == "pair" and not args.host:
        p.error("pair needs --host <tv-ip>")
    rc = args.fn(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
