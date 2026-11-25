# OpenWrt 22.03+ (firewall4). Paste over SSH, after replacing TV_MAC / PIHOLE_IP.
# Everything matches the TV by MAC, so it covers its IPv4 and IPv6 addresses.

# 1. Hand the TV (and everyone) the Pi-hole as DNS over DHCPv4
uci add_list dhcp.lan.dhcp_option='6,PIHOLE_IP'

# 2. TV: no DNS to anything on the internet (53 plain, 853 DoT/DoQ). Pi-hole is on the LAN, so it's unaffected.
uci add firewall rule
uci set firewall.@rule[-1].name='TV-no-DNS-bypass'
uci set firewall.@rule[-1].src='lan'
uci set firewall.@rule[-1].src_mac='TV_MAC'
uci set firewall.@rule[-1].dest='wan'
uci set firewall.@rule[-1].dest_port='53 853'
uci set firewall.@rule[-1].proto='tcp udp'
uci set firewall.@rule[-1].family='any'
uci set firewall.@rule[-1].target='REJECT'

# 3. TV: no traffic at all to the big public resolvers (blocks DNS-over-HTTPS to them too)
uci add firewall rule
uci set firewall.@rule[-1].name='TV-no-public-resolvers'
uci set firewall.@rule[-1].src='lan'
uci set firewall.@rule[-1].src_mac='TV_MAC'
uci set firewall.@rule[-1].dest='wan'
uci set firewall.@rule[-1].proto='all'
uci set firewall.@rule[-1].family='any'
for ip in 8.8.8.8 8.8.4.4 1.1.1.1 1.0.0.1 9.9.9.9 149.112.112.112 208.67.222.222 208.67.220.220 \
          94.140.14.14 94.140.15.15 2001:4860:4860::8888 2001:4860:4860::8844 2606:4700:4700::1111 \
          2606:4700:4700::1001 2620:fe::fe 2620:fe::9; do
  uci add_list firewall.@rule[-1].dest_ip="$ip"
done
uci set firewall.@rule[-1].target='REJECT'

# 4. IPv6: the router advertises itself as DNS. Make the router's own resolver forward to the
#    Pi-hole, so IPv6 clients asking the router are filtered too.
uci add_list dhcp.@dnsmasq[0].server='PIHOLE_IP'
uci set dhcp.@dnsmasq[0].noresolv='1'

uci commit && service firewall restart && service dnsmasq restart && service odhcpd restart

# --- Undo ---
# uci del_list dhcp.lan.dhcp_option='6,PIHOLE_IP'
# uci del_list dhcp.@dnsmasq[0].server='PIHOLE_IP'; uci set dhcp.@dnsmasq[0].noresolv='0'
# then in LuCI > Network > Firewall > Traffic Rules delete TV-no-DNS-bypass and TV-no-public-resolvers
# uci commit && service firewall restart && service dnsmasq restart
