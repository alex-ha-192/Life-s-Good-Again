#!/usr/bin/env python3
"""Tiny fake LG TV (SSAP over ws://127.0.0.1:3000) for testing lgtv_privacy.py
without touching a real TV.

    python3 tools/mock_tv.py &
    LGTV_PRIVACY_HOME=/tmp/lgtest python3 lgtv_privacy.py --insecure-port pair --host 127.0.0.1

Dev Mode renewal test (fake LG site on :3080, token "mocktoken0123456789"):
    export LGTV_PRIVACY_HOME=/tmp/lgtest LGTV_DEVMODE_API=http://127.0.0.1:3080
    python3 lgtv_devmode.py --insecure-port set-token mocktoken0123456789
    python3 lgtv_devmode.py --insecure-port renew
"""
import base64
import hashlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

SETTINGS = {
    "option": {"livePlus": "on", "livePromotion": "on", "webOSPromotionVideo": "on"},
    "general": {"homePromotion": "on", "sportsAlarm": "on", "homeAutoLaunch": "on", "noSignalScreenSaver": "on"},
    "other": {"contentRecommendation": "on"},
}
ALERTS = {}
# Developer Mode session: seconds left, ticking. "extend" resets it to ~1000 h.
DEVMODE = {"token": "mocktoken0123456789", "expires": time.time() + 640 * 3600, "launches": []}
FOREGROUND = {"appId": "com.webos.app.home"}


def recv_frame(conn, buf):
    def need(n):
        nonlocal buf
        while len(buf) < n:
            d = conn.recv(65536)
            if not d:
                raise ConnectionError
            buf += d
        out, buf = buf[:n], buf[n:]
        return out
    b1, b2 = need(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", need(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", need(8))[0]
    mask = need(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(need(n)))
    return b1 & 0x0F, data, buf


def send(conn, obj):
    data = json.dumps(obj).encode()
    n = len(data)
    hdr = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n) if n < 65536
                           else bytes([127]) + struct.pack("!Q", n))
    conn.sendall(hdr + data)


def apply_luna(uri, params):
    if uri == "luna://com.webos.settingsservice/setSystemSettings":
        cat = SETTINGS.setdefault(params["category"], {})
        for k, v in params["settings"].items():
            if k in cat:
                cat[k] = v


def handle(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += conn.recv(4096)
    head, buf = buf.split(b"\r\n\r\n", 1)
    key = [l.split(b": ", 1)[1] for l in head.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
    acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
    try:
        while True:
            op, data, buf = recv_frame(conn, buf)
            if op == 8:
                return
            if data.startswith(b"type:button"):
                continue
            msg = json.loads(data)
            if msg["type"] == "register":
                if "client-key" not in msg["payload"]:
                    send(conn, {"type": "response", "id": msg["id"], "payload": {"pairingType": "PROMPT"}})
                send(conn, {"type": "registered", "id": msg["id"], "payload": {"client-key": "mockkey123"}})
                continue
            uri, p, rid = msg["uri"][len("ssap://"):], msg.get("payload", {}), msg["id"]
            out = {"returnValue": True}
            if uri == "settings/getSystemSettings":
                cat = SETTINGS.get(p["category"], {})
                out["settings"] = {k: cat[k] for k in p["keys"] if k in cat}
            elif uri == "system.notifications/createAlert":
                aid = f"alert{len(ALERTS)}"
                ALERTS[aid] = p
                out["alertId"] = aid
            elif uri == "system.notifications/closeAlert":
                a = ALERTS.pop(p["alertId"])
                apply_luna(a["onclose"]["uri"], a["onclose"]["params"])
            elif uri == "system/getSystemInfo":
                out.update({"modelName": "43UR78006LK", "features": {}})
            elif uri == "com.webos.service.update/getCurrentSWInformation":
                out.update({"product_name": "webOSTV 8.0", "major_ver": "03", "minor_ver": "21.40", "country": "GB"})
            elif uri == "com.webos.applicationManager/getForegroundAppInfo":
                out["appId"] = FOREGROUND["appId"]
            elif uri == "system.launcher/launch":
                DEVMODE["launches"].append(p)
                if p.get("id") == "com.palmdts.devmode" and (p.get("params") or {}).get("extend"):
                    DEVMODE["expires"] = time.time() + 999 * 3600 + 59 * 60
                FOREGROUND["appId"] = p.get("id", "")
                print("launch", json.dumps(p), flush=True)
            elif uri == "system.launcher/close":
                FOREGROUND["appId"] = "com.webos.app.home"
            elif uri == "system.notifications/createToast":
                print("toast", p.get("message"), flush=True)
            elif uri == "com.webos.applicationManager/listLaunchPoints":
                out["launchPoints"] = [{"id": "netflix", "title": "Netflix"}, {"id": "youtube.leanback.v4", "title": "YouTube"},
                                       {"id": "com.palmdts.devmode", "title": "Developer Mode"}]
            send(conn, {"type": "response", "id": rid, "payload": out})
    except (ConnectionError, OSError):
        pass
    finally:
        conn.close()


class LGSite(BaseHTTPRequestHandler):
    """Fake developer.lge.com /secure/{Check,Reset}DevModeSession.dev on :3080."""

    def do_GET(self):
        u = urlparse(self.path)
        tok = parse_qs(u.query).get("sessionToken", [""])[0]
        if tok != DEVMODE["token"]:
            body = {"result": "fail", "errorCode": "401", "errorMsg": "invalid token"}
        elif u.path.endswith("CheckDevModeSession.dev"):
            left = max(0, int(DEVMODE["expires"] - time.time()))
            body = {"result": "success", "errorCode": "200",
                    "errorMsg": f"{left // 3600}:{left % 3600 // 60:02d}:{left % 60:02d}"}
        else:  # Reset: like the real one per reports, says success but doesn't move the TV timer
            body = {"result": "success", "errorCode": "200", "errorMsg": "GNL"}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    threading.Thread(target=HTTPServer(("127.0.0.1", 3080), LGSite).serve_forever, daemon=True).start()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 3000))
    s.listen()
    print("mock TV on ws://127.0.0.1:3000", flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
