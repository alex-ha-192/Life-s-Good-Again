#!/bin/sh
# LG TV Privacy Kit - boot hook. ROOTED TVs ONLY (Homebrew Channel with root).
# install.sh links this as /var/lib/webosbrew/init.d/60-lgkit, so Homebrew Channel runs it at
# every boot, after Life's Good Without Spying's own hook (50-...). It finishes in about a second
# (Homebrew's failsafe watches boot scripts) and:
#   1. hosts: adds the kit's extra names after LGWS's blocklist (or, without LGWS, bind-mounts
#      /etc/hosts with the kit's whole list). Nothing is written to the system partition.
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
# With Life's Good Without Spying (LGWS, the default): LGWS owns /etc/hosts. Once its blocklist is
# up, we append the kit's extra names (regional ad hosts, DoH services, strict tier) after it.
# LGWS keeps lines other tools add after its block, so they survive its own re-applies.
# Without LGWS (--no-lgws): bind-mount /etc/hosts with the kit's whole list, as before.
LGWS_MARK="# lifesgoodwithoutspying - blocked LG ad/ACR/telemetry endpoints"
KIT_MARK="# --- LG TV Privacy Kit extras"
append_extras() {
    grep -qF "$KIT_MARK" /etc/hosts && { echo "hosts: kit extras already there"; return; }
    { echo "$KIT_MARK ($(grep -c '^0' "$D/hosts.extra") names, $D/hosts.extra) ---"; cat "$D/hosts.extra";
      echo "# --- end LG TV Privacy Kit ---"; } >>/etc/hosts && echo "hosts: added $(grep -c '^0' "$D/hosts.extra") kit extras after LGWS"
}
valid() { ! grep -v -E '^(#.*|[[:space:]]*|0\.0\.0\.0[[:space:]]+[A-Za-z0-9._-]+)$' "$1" | grep -q .; }
if [ "${LGWS:-0}" = 1 ]; then
    if [ -s "$D/hosts.extra" ] && valid "$D/hosts.extra"; then
        (   # in the background: LGWS applies at boot too, and may take a minute or two
            i=0
            while [ $i -lt 100 ]; do
                grep -qF "$LGWS_MARK" /etc/hosts 2>/dev/null && { sleep 3; append_extras; exit 0; }
                i=$((i + 1)); sleep 3
            done
            echo "hosts: LGWS blocklist never came up; kit extras not added (is its protection on?)"
        ) >>"$D/boot.log" 2>&1 &
        echo "hosts: waiting for LGWS, then adding kit extras"
    fi
elif [ "${HOSTS:-1}" = 1 ] && [ -s "$D/hosts.block" ]; then
    if ! valid "$D/hosts.block"; then
        echo "hosts: unexpected line in hosts.block, not applying"
    elif grep -q " /etc/hosts " /proc/mounts; then
        echo "hosts: /etc/hosts already bind-mounted by something else, leaving it"
    else
        M=/tmp/lgkit.hosts
        { cat /etc/hosts; echo; echo "# --- LG TV Privacy Kit ($D/hosts.block) ---"; cat "$D/hosts.block"; } >"$M"
        chmod 644 "$M"
        if mount --bind "$M" /etc/hosts; then
            echo "hosts: $(grep -c '^0\.0\.0\.0' "$D/hosts.block") names blocked"
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
