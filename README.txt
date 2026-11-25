LG TV Privacy Kit + Tiles Home  (v2, 2026-11-25)
================================================
Open GUIDE.html for the full write-up and step-by-step setup.
v2 fixes Developer Mode renewal and really tightens the DNS block; see "What changed in v2".

Quick start (Python 3.8+, no extra packages):
  python3 lgtv_privacy.py pair --host <tv-ip>
  python3 lgtv_privacy.py audit
  python3 lgtv_privacy.py apply --dry-run
  python3 lgtv_privacy.py apply
  python3 lgtv_privacy.py restore        # undo

Keep Developer Mode alive:
  python3 lgtv_devmode.py renew --force  # extend now
  python3 lgtv_devmode.py schedule       # prints the timer entry for this machine

Launcher: install dist/io.github.lgprivacykit.tiles_1.0.0_all.ipk with webOS Dev Manager
or `ares-install -d tv <ipk>` (Developer Mode required).

DNS: blocklist/lg-base.* (6 formats), optional lg-strict.* and lg-doh.* for the TV only.
Stop bypass: router/ (OpenWrt, MikroTik, pfSense/OPNsense, plain routers).
Check:  python3 tools/dns_check.py --dns <pihole-ip>
Try the CLI safely first:  python3 tools/mock_tv.py &  then add --insecure-port --host 127.0.0.1

ONE STEP (stock TV):  python3 setup.py --dry-run   then   python3 setup.py --pihole http://<pihole-ip>
                      (or --adguard http://<ip>:3000).  `setup.py status` / `setup.py undo`.
