#!/bin/sh
# LG TV Privacy Kit - boot hook. ROOTED TVs ONLY (Homebrew Channel with root).
# install.sh links this as /var/lib/webosbrew/init.d/50-lgkit, so Homebrew Channel runs it at
# every boot. It finishes in about a second (Homebrew's failsafe watches boot scripts) and:
#   1. hosts: bind-mounts /etc/hosts with the kit's blocklist added. Nothing is written to the
#      read-only system partition; a reboot without this hook gives you the stock file back.
#   2. firewall: stops the TV reaching public DNS resolvers directly (port 53 or DoH) and
#      DNS-over-TLS (853), so apps can't go around the hosts list. Only if iptables exists.
#   3. starts lgkit-agent.js (Home -> Tiles Home, power-on, settings guard) in the background.
# Each step checks first, so running it twice is harmless.  Undo: sh install.sh --uninstall
D=/var/lib/webosbrew/lgkit
[ -f "$D/boot.log" ] && [ "$(wc -c <"$D/boot.log")" -gt 131072 ] && mv "$D/boot.log" "$D/boot.log.1"
exec >>"$D/boot.log" 2>&1
echo "== $(date) lgkit boot"
[ -f "$D/lgkit.env" ] && . "$D/lgkit.env"

# --- 1. hosts --------------------------------------------------------------------------------
if [ "${HOSTS:-1}" = 1 ] && [ -s "$D/hosts.block" ]; then
    bad=$(grep -v -E '^(#.*|[[:space:]]*|0\.0\.0\.0[[:space:]]+[A-Za-z0-9._-]+)$' "$D/hosts.block" | head -n1)
    if [ -n "$bad" ]; then
        echo "hosts: unexpected line, not applying: $bad"
    elif grep -q " /etc/hosts " /proc/mounts; then
        echo "hosts: /etc/hosts already bind-mounted, leaving it"
    else
        M=/tmp/lgkit.hosts
        { cat /etc/hosts; echo; echo "# --- LG TV Privacy Kit ($D/hosts.block) ---"; cat "$D/hosts.block"; } >"$M"
        chmod 644 "$M"
        if mount --bind "$M" /etc/hosts; then
            echo "hosts: $(grep -c '^0\.0\.0\.0' "$D/hosts.block") names blocked"
            # make running resolvers re-read it
            killall -HUP dnsmasq 2>/dev/null
        else
            echo "hosts: bind mount failed"
        fi
    fi
fi

# --- 2. firewall -----------------------------------------------------------------------------
fw() { # $1 = iptables or ip6tables, rest = resolver IPs
    T=$1; shift
    command -v "$T" >/dev/null 2>&1 || { echo "firewall: no $T on this TV, skipping"; return; }
    $T -N LGKIT 2>/dev/null; $T -F LGKIT
    # keep whatever DNS server the TV is actually using, even if it's on the list
    in_use=$(sed -n 's/^nameserver[[:space:]]*//p' /etc/resolv.conf /var/run/resolv.conf /tmp/resolv.conf 2>/dev/null | tr '\n' ' ')
    for ip in "$@"; do
        case " $in_use " in *" $ip "*) echo "firewall: $ip is your DNS server, not blocking it"; continue ;; esac
        $T -A LGKIT -d "$ip" -j REJECT
    done
    $T -A LGKIT -p tcp --dport 853 -j REJECT
    $T -A LGKIT -p udp --dport 853 -j REJECT
    $T -C OUTPUT -j LGKIT 2>/dev/null || $T -I OUTPUT -j LGKIT
    echo "firewall: $T rules in place ($($T -S LGKIT | grep -c REJECT) rules)"
}
if [ "${FIREWALL:-1}" = 1 ]; then
    fw iptables 8.8.8.8 8.8.4.4 1.1.1.1 1.0.0.1 9.9.9.9 149.112.112.112 208.67.222.222 208.67.220.220 \
        94.140.14.14 94.140.15.15 45.90.28.0 45.90.30.0 76.76.2.0 76.76.10.0 185.228.168.9 185.228.169.9
    fw ip6tables 2001:4860:4860::8888 2001:4860:4860::8844 2606:4700:4700::1111 2606:4700:4700::1001 \
        2620:fe::fe 2620:fe::9 2a10:50c0::ad1:ff 2a10:50c0::ad2:ff
fi

# --- 3. agent --------------------------------------------------------------------------------
if [ -s "$D/lgkit-agent.js" ]; then
    if pgrep -f lgkit-agent.js >/dev/null 2>&1; then
        echo "agent: already running"
    else
        (
            # wait (in the background) for the app manager, then start the agent
            i=0
            while [ $i -lt 90 ]; do
                luna-send -n 1 -w 2000 luna://com.webos.applicationManager/running '{}' 2>/dev/null | grep -q '"returnValue": *true' && break
                i=$((i + 1)); sleep 2
            done
            sleep 6
            set -a; [ -f "$D/lgkit.env" ] && . "$D/lgkit.env"; set +a
            LGKIT_DIR="$D" nohup node "$D/lgkit-agent.js" >/dev/null 2>&1 &
        ) >/dev/null 2>&1 &
        echo "agent: starting once the TV is up"
    fi
fi
exit 0
