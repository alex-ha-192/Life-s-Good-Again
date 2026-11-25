# MikroTik RouterOS 7. Paste in Terminal after replacing TV_IP / PIHOLE_IP.
# Give the TV a static DHCP lease first so TV_IP never changes.

/ip dhcp-server network set [find] dns-server=PIHOLE_IP

/ip firewall address-list
add list=public-dns address=8.8.8.8
add list=public-dns address=8.8.4.4
add list=public-dns address=1.1.1.1
add list=public-dns address=1.0.0.1
add list=public-dns address=9.9.9.9
add list=public-dns address=149.112.112.112
add list=public-dns address=208.67.222.222
add list=public-dns address=208.67.220.220
add list=public-dns address=94.140.14.14
add list=public-dns address=94.140.15.15

/ip firewall filter
add chain=forward src-address=TV_IP out-interface-list=WAN protocol=udp dst-port=53,853 action=reject comment="TV: no DNS bypass (udp)"
add chain=forward src-address=TV_IP out-interface-list=WAN protocol=tcp dst-port=53,853 action=reject reject-with=tcp-reset comment="TV: no DNS bypass (tcp)"
add chain=forward src-address=TV_IP dst-address-list=public-dns action=reject comment="TV: no public resolvers / DoH"
# Move these three rules above any 'accept established/forward' catch-all in your list.

# IPv6: simplest is no IPv6 for the TV. If your TV's network menu has an IPv6 switch, turn it off there.
# Otherwise stop advertising the router as IPv6 DNS:
/ipv6 nd set [find] advertise-dns=no

# --- Undo ---
# /ip firewall filter remove [find comment~"^TV: "]
# /ip firewall address-list remove [find list=public-dns]
# /ipv6 nd set [find] advertise-dns=yes
