Stop the TV going around your Pi-hole / AdGuard Home
=====================================================
A DNS blocklist only works if the TV actually asks your filter. These snippets
make the router refuse the other routes: public DNS on port 53, DNS-over-TLS
and DNS-over-QUIC on 853, the big public resolvers' IPs (which also covers
DNS-over-HTTPS to them), and IPv6 DNS from router adverts.

Replace before use:
  TV_IP      your TV's fixed IPv4 address          e.g. 192.168.1.50
  TV_MAC     your TV's MAC (TV: Settings > Support > TV Information, or router's client list)
  PIHOLE_IP  your Pi-hole / AdGuard Home address   e.g. 192.168.1.2

Rules are scoped to the TV only, so nothing else on your network changes.
Read the file for your router, paste the lines yourself, then run
  python3 tools/dns_check.py --dns PIHOLE_IP
from a computer (or temporarily give that computer the same rules) to confirm.

  openwrt.sh         OpenWrt 22.03+ (fw4 / nftables), via SSH: uci commands
  mikrotik.rsc       MikroTik RouterOS 7
  pfsense-opnsense.txt  click-path for pfSense / OPNsense
  generic-router.txt    ordinary home routers with no firewall rules

Undo: each file ends with the lines that remove what it added.
