#!/usr/bin/env python3
"""Check that your DNS block works and that devices can't go around it.

    python3 tools/dns_check.py --dns 192.168.1.2          # your Pi-hole / AdGuard Home IP

Read-only: it only sends DNS queries and opens (then closes) a few TCP
connections. Run it from a computer on the same network as the TV. If your
router rules apply only to the TV's IP, a "bypass possible" result here may be
fine for the TV; run it from a machine covered by the same rules to be sure.

Checks:
  1. Blocking   - a sample of LG hosts from blocklist/lg-base.hosts resolve to 0.0.0.0 / NXDOMAIN
                  via your DNS server, and a normal site still resolves.
  2. Port 53    - asking 8.8.8.8 / 1.1.1.1 / 9.9.9.9 directly for an LG host: a real answer means
                  the router lets devices skip your Pi-hole (fix: redirect or block outbound 53).
  3. DoT (853)  - can this network open DNS-over-TLS to public resolvers? (fix: block TCP/UDP 853)
  4. IPv6       - does this machine have working IPv6? If so, the TV may get an IPv6 DNS server
                  from your router's adverts unless you point those at the Pi-hole too.
"""
import argparse
import os
import random
import socket
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = {"Google": "8.8.8.8", "Cloudflare": "1.1.1.1", "Quad9": "9.9.9.9"}
PROBE = "ad.lgsmartad.com"
CONTROL = "example.com"


def query(server, name, timeout=3.0, qtype=1):
    """Minimal DNS A query over UDP. Returns ('ok', [ips]) / ('nx', []) / ('err', reason)."""
    tid = random.randint(0, 0xFFFF)
    q = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    q += struct.pack("!HH", qtype, 1)
    fam = socket.AF_INET6 if ":" in server else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(4096)
    except OSError as e:
        return "err", str(e) or type(e).__name__
    finally:
        s.close()
    rid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", data[:12])
    if rid != tid:
        return "err", "mismatched reply"
    rcode = flags & 0xF
    if rcode == 3:
        return "nx", []
    if rcode:
        return "err", f"rcode {rcode}"
    i = 12
    for _ in range(qd):  # skip question
        while data[i]:
            i += data[i] + 1
        i += 5
    ips = []
    for _ in range(an):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i]:
                i += data[i] + 1
            i += 1
        rtype, _, _, rdlen = struct.unpack("!HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(data[i:i + 4]))
        i += rdlen
    return "ok", ips


def blocked(res):
    kind, ips = res
    return kind == "nx" or (kind == "ok" and (not ips or all(ip in ("0.0.0.0", "127.0.0.1") for ip in ips)))


def tcp_open(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sample_hosts(n):
    names = []
    for f in ("lg-base.hosts",):
        for line in (ROOT / "blocklist" / f).read_text().splitlines():
            if line.startswith("0.0.0.0 "):
                names.append(line.split()[1])
    regional = [d for d in names if d.endswith("aws-prd.net")]
    core = [d for d in names if not d.endswith("aws-prd.net")]
    random.shuffle(regional)
    return core[: n] + regional[: max(5, n // 5)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dns", required=True, help="IP of your Pi-hole / AdGuard Home / router DNS")
    p.add_argument("--sample", type=int, default=40, help="how many listed hosts to test (default 40)")
    a = p.parse_args()
    problems = 0

    print(f"1. Blocking via {a.dns}")
    ctl = query(a.dns, CONTROL)
    if ctl[0] == "err":
        print(f"   ! no answer from {a.dns} ({ctl[1]}). Is that the right IP, and is port 53 open?")
        return 1
    print(f"   {'ok' if not blocked(ctl) else '!!'} {CONTROL} resolves normally: {ctl[1]}")
    hosts = sample_hosts(a.sample)
    leak = [h for h in hosts if not blocked(query(a.dns, h))]
    print(f"   {len(hosts) - len(leak)}/{len(hosts)} LG hosts blocked")
    for h in leak[:10]:
        print(f"   !! not blocked: {h}")
    if leak:
        problems += 1
        print("   -> add/refresh the lists (Pi-hole: `pihole -g`), and make sure the TV uses this server.")

    print("2. Plain DNS to public resolvers (port 53)")
    for name, ip in PUBLIC.items():
        r = query(ip, PROBE)
        c = query(ip, CONTROL)
        if r[0] == "err":
            print(f"   ok {name} {ip}: no answer (outbound 53 blocked)")
        elif blocked(r) and blocked(c):
            print(f"   ok {name} {ip}: refuses everything (outbound DNS is intercepted)")
        elif blocked(r):
            print(f"   ok {name} {ip}: answered, but blocked - your router redirects 53 to your filter")
        else:
            problems += 1
            print(f"   !! {name} {ip}: real answer {r[1]} - a device could skip your Pi-hole")

    print("3. DNS-over-TLS (port 853)")
    for name, ip in PUBLIC.items():
        if tcp_open(ip, 853):
            problems += 1
            print(f"   !! {name} {ip}:853 reachable - block TCP+UDP 853 outbound")
        else:
            print(f"   ok {name} {ip}:853 blocked")

    print("4. IPv6")
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.connect(("2001:4860:4860::8888", 53))
        addr = s.getsockname()[0]
        s.close()
        if addr.startswith(("fe80", "::1")):
            raise OSError
        r6 = query("2001:4860:4860::8888", PROBE)
        print(f"   .. this network has IPv6 ({addr}).")
        if r6[0] == "ok" and not blocked(r6):
            problems += 1
            print("   !! public DNS over IPv6 answers - apply the same 53/853 rules to IPv6, and have your")
            print("      router advertise only the Pi-hole as IPv6 DNS (RDNSS / DHCPv6), or turn IPv6 off for the TV.")
        else:
            print("   ok public DNS over IPv6 is blocked or redirected")
    except OSError:
        print("   ok no working IPv6 here (nothing to leak through)")

    print()
    print("All good." if not problems else f"{problems} thing(s) to fix - see GUIDE.html, 'Stop the TV going around it'.")
    print("Note: DNS can't stop traffic to hard-coded IP addresses; see the guide's firewall option.")
    return 0 if not problems else 3


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
