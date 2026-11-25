#!/usr/bin/env python3
"""Build every blocklist format from blocklist/src/*.txt.

    python3 tools/build_blocklists.py
    python3 tools/build_blocklists.py --client 192.168.1.50   # also AdGuard rules for the TV only

Writes, for each tier (base, strict, doh):
  lg-<tier>.hosts            hosts format (Pi-hole adlist, router hosts file). Exact names only.
  lg-<tier>.domains.txt      plain domain list (Pi-hole adlist, NextDNS, Blocky, Technitium)
  lg-<tier>.adguard.txt      AdGuard Home / AdGuard DNS rules (wildcards + regex)
  lg-<tier>.pihole-regex.txt Pi-hole regex deny filters (wildcards + regex)
  lg-<tier>.dnsmasq.conf     dnsmasq / OpenWrt / Pi-hole custom (blocks subdomains too)
  lg-<tier>.unbound.conf     Unbound / pfSense / OPNsense (local-zone, subdomains too)
"""
import argparse
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "blocklist"
# ISO 3166-1 alpha-2, for LG's per-country <cc>-ad-lgsmartad-com.aws-prd.net style hosts.
CC = ("ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs "
      "bt bv bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee "
      "eg eh er es et fi fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm "
      "hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc "
      "li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na "
      "nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw "
      "sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to "
      "tr tt tv tw tz ua ug um us uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw").split()
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")


def parse(path):
    exact, wild, regex = [], [], []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("##"):
            continue
        if line == "@regional":
            for cc in CC:
                exact += [f"{cc}-ad-lgsmartad-com.aws-prd.net", f"{cc}-info-lgsmartad-com.aws-prd.net",
                          f"{cc}-rdx2-lgtvsdp-com.aws-prd.net"]
        elif line.startswith("regex: "):
            r = line[7:]
            re.compile(r)
            regex.append(r)
        elif line.startswith("*."):
            d = line[2:].lower()
            assert DOMAIN.match(d), d
            wild.append(d)
        else:
            d = line.lower()
            assert DOMAIN.match(d), (path, d)
            exact.append(d)
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    wild = dedup(wild)
    # exact names already covered by a wildcard stay in hosts/plain lists (they can't wildcard)
    return dedup(exact), wild, dedup(regex)


def header(path):
    return [l for l in path.read_text().splitlines() if l.startswith("# ")]


def esc(d):
    return re.escape(d).replace("\\-", "-")


def build(tier, client=None):
    src = ROOT / "src" / f"{tier}.txt"
    exact, wild, regex = parse(src)
    head = header(src) + [f"# Generated {time.strftime('%Y-%m-%d')} by tools/build_blocklists.py - edit src/{tier}.txt"]
    plain = list(dict.fromkeys(exact + wild))
    rx = [re.compile(r) for r in regex]
    covered = lambda d: any(d == w or d.endswith("." + w) for w in wild) or any(r.search(d) for r in rx)  # noqa: E731
    in_wild = lambda d: any(d == w or d.endswith("." + w) for w in wild)  # noqa: E731
    out = {}
    out["hosts"] = head + [""] + [f"0.0.0.0 {d}" for d in plain]
    out["domains.txt"] = head + [""] + plain
    ag = [f"||{d}^" for d in wild] + [f"||{d}^" for d in exact if not covered(d)] + [f"/{r}/" for r in regex]
    out["adguard.txt"] = ["! " + l[2:] for l in head] + ag
    # Regex filters only for what a hosts list can't express; exact names go in via the adlist.
    px = [f"(^|\\.){esc(d)}$" for d in wild] + regex
    out["pihole-regex.txt"] = head + ["# Pi-hole: add lg-%s.hosts as an adlist, then each line below under" % tier,
                                      "# Domains > Regex filter > Deny (or `pihole --regex '<line>'`). Catches hosts LG adds later."] + px
    dm = list(dict.fromkeys(wild + [d for d in exact if not in_wild(d)]))
    out["dnsmasq.conf"] = head + [f"address=/{d}/0.0.0.0" for d in dm] + [f"address=/{d}/::" for d in dm]
    out["unbound.conf"] = head + ["server:"] + [f'  local-zone: "{d}." always_null' for d in dm]
    for ext, lines in out.items():
        (ROOT / f"lg-{tier}.{ext}").write_text("\n".join(lines) + "\n")
    if client:
        cl = [f"{r}$client='{client}'" for r in ag]
        (ROOT / f"lg-{tier}.adguard-tv-only.txt").write_text(
            "\n".join(["! " + l[2:] for l in head] + [f"! Applies only to the TV at {client}"] + cl) + "\n")
    print(f"lg-{tier}: {len(plain)} names, {len(wild)} wildcards, {len(regex)} regex")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", help="TV IP or AdGuard client name, for TV-only AdGuard rules")
    a = p.parse_args()
    for tier in ("base", "strict", "doh"):
        build(tier, a.client)


if __name__ == "__main__":
    main()
