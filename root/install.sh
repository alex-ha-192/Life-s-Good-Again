#!/bin/sh
# LG TV Privacy Kit - installer for ROOTED TVs (Homebrew Channel with root). Runs ON THE TV, as root.
#
# Easiest: from a laptop, once:   python3 setup.py --root          (copies the kit over SSH and runs this)
# By hand, over SSH on the TV:    sh /tmp/kit/root/install.sh --from /tmp/kit [options]
#
# What it sets up (all reversible, nothing on the read-only system partition):
#   - Life's Good Without Spying (LGWS, a Homebrew Channel app by lewisdoesstuff) as the
#     telemetry layer: it blocks LG's ad/ACR/telemetry hosts and stops LG's ad, ACR, telemetry
#     and voice services. Installed from its GitHub release if missing (SHA-256 checked),
#     then configured and switched on through its own nospy-ctl.sh.
#   - the kit's extra names on top of LGWS's list (regional ad hosts, DoH services)
#   - a firewall so apps can't go around it (public resolvers, DoH by IP, DNS-over-TLS)
#   - Tiles Home, opened by the Home button, at power-on and after standby
#   - the privacy settings re-checked on the TV every 6 hours and after every power-on
#
# Options:
#   --from DIR          the unpacked kit (default: the folder above this script)
#   --tiers LIST        kit tiers, comma list of base,strict,doh (default: base,doh). strict also turns
#                       on LGWS's Content Store, Home sessions, ThinQ and LG Channels categories
#   --keep-store        keep the LG Content Store working (LGWS: SDP not blocked)
#   --keep-voice        keep LG's voice services (LGWS stops them by default; the remote's mic
#                       button stops working while they're stopped)
#   --block-updates     also block LG firmware and app updates (keeps root; you miss security fixes)
#   --no-lgws           don't use LGWS: put the kit's own whole list in /etc/hosts instead
#   --home-code N       your remote's Home key code (find it with: node find-home-key.js)
#   --no-firewall       skip the firewall rules
#   --no-boot-to-tiles  don't open Tiles Home at power-on (the Home button still does)
#   --no-redirect       don't take over LG Home at all
#   --guard-hours N     how often to re-check settings (default 6, 0 = only after power-on)
#   --skip-app          don't install/update the Tiles Home app
#   --status            show what's active
#   --uninstall         remove all of it, and switch LGWS off again if this kit switched it on
#                       (Tiles Home and the LGWS app stay installed; remove them from the app list)
set -u
D=/var/lib/webosbrew/lgkit
INITD=/var/lib/webosbrew/init.d
APP=io.github.lgprivacykit.tiles
SRC=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
TIERS=base,doh; BLOCK_UPDATES=0; HOME_CODE=""; FIREWALL=1; BOOT_TO_TILES=1; REDIRECT_HOME=1
GUARD_HOURS=6; SKIP_APP=0; MODE=install; LGWS=1; KEEP_STORE=0; KEEP_VOICE=0
LGWS_ID=ooo.lew.lifesgoodwithoutspying
LGWS_DIR=/media/developer/apps/usr/palm/applications/$LGWS_ID
LGWS_CTL=$LGWS_DIR/init/nospy-ctl.sh
LGWS_MANIFEST=https://github.com/lewisdoesstuff/lifesgoodwithoutspying/releases/latest/download/$LGWS_ID.manifest.json
KIT_MARK="# --- LG TV Privacy Kit extras"

die() { echo "! $*"; exit 1; }   # stdout, so it lands in order when run over ssh
say() { echo "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --from) SRC=$2; shift ;;
        --tiers) TIERS=$2; shift ;;
        --block-updates) BLOCK_UPDATES=1 ;;
        --keep-store) KEEP_STORE=1 ;;
        --keep-voice) KEEP_VOICE=1 ;;
        --no-lgws) LGWS=0 ;;
        --home-code) HOME_CODE=$2; shift ;;
        --no-firewall) FIREWALL=0 ;;
        --no-boot-to-tiles) BOOT_TO_TILES=0 ;;
        --no-redirect) REDIRECT_HOME=0 ;;
        --guard-hours) GUARD_HOURS=$2; shift ;;
        --skip-app) SKIP_APP=1 ;;
        --status) MODE=status ;;
        --uninstall) MODE=uninstall ;;
        -h|--help) sed -n '2,38p' "$0"; exit 0 ;;
        *) die "unknown option $1 (try --help)" ;;
    esac
    shift
done

[ "$(id -u)" = 0 ] || die "run this as root on the TV (Homebrew Channel SSH, user root)"
[ -d /var/lib/webosbrew ] || die "Homebrew Channel isn't set up with root on this TV (/var/lib/webosbrew missing)"

stop_agent() {
    for p in $(pgrep -f lgkit-agent.js 2>/dev/null) $(pgrep -f tiles-home-watch.js 2>/dev/null); do kill "$p" 2>/dev/null; done
}
unmount_hosts() {
    if grep -q " /etc/hosts " /proc/mounts; then
        umount /etc/hosts 2>/dev/null && say "  hosts: back to stock" || say "  ! couldn't unmount /etc/hosts (reboot will)"
    fi
}
flush_fw() {
    for T in iptables ip6tables; do
        command -v $T >/dev/null 2>&1 || continue
        while $T -D OUTPUT -j LGKIT 2>/dev/null; do :; done
        $T -F LGKIT 2>/dev/null; $T -X LGKIT 2>/dev/null
    done
}
remove_extras() { # take the kit's block back out of /etc/hosts, in place (it may be a bind mount)
    grep -qF "$KIT_MARK" /etc/hosts 2>/dev/null || return 0
    awk -v m="$KIT_MARK" 'index($0, m) == 1 { skip = 1; next } skip && /^# --- end LG TV Privacy Kit ---/ { skip = 0; next } !skip' \
        /etc/hosts >/tmp/lgkit.hosts.new && cat /tmp/lgkit.hosts.new >/etc/hosts && rm -f /tmp/lgkit.hosts.new
    say "  hosts: kit extras removed"
}
lgws_wait() { # wait for an LGWS background job
    i=0
    while [ $i -lt 130 ]; do
        st=$(sh "$LGWS_CTL" job 2>/dev/null | sed -n 's/^job=//p' | head -n1)
        case "$st" in done|idle) return 0 ;; failed) return 1 ;; esac
        i=$((i + 1)); sleep 2
    done
    return 1
}
install_ipk_lgws() { # $1 = ipk path on the TV
    luna-send -n 1 -w 60000 luna://com.webos.appInstallService/dev/install \
        "{\"id\":\"$LGWS_ID\",\"ipkUrl\":\"$1\",\"subscribe\":true}" >/dev/null 2>&1
    i=0; while [ $i -lt 30 ] && [ ! -f "$LGWS_CTL" ]; do i=$((i + 1)); sleep 2; done
    [ -f "$LGWS_CTL" ] || { say "  ! the TV's installer didn't install LGWS"; return 1; }
    say "  LGWS: installed ($(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$LGWS_DIR/appinfo.json"))"
}
curl_why() { # explain a curl exit code
    case "$1" in
        6) echo "the TV couldn't look up github.com (DNS)" ;;
        7|28) echo "the TV couldn't connect to GitHub (no route out, or timed out)" ;;
        35|51|58|60|77|83) echo "the TV's TLS/certificate check failed (old CA bundle on the TV)" ;;
        22) echo "GitHub answered with an error" ;;
        *) echo "curl exit $1" ;;
    esac
}
install_lgws() {
    tmpipk=/media/developer/temp/lgws.ipk
    mkdir -p /media/developer/temp
    # 1. a copy the laptop downloaded and checked (setup.py puts it in the bundle)
    local_ipk=$SRC/dist/$LGWS_ID.ipk
    if [ -s "$local_ipk" ]; then
        want=$(cut -d' ' -f1 "$local_ipk.sha256" 2>/dev/null)
        got=$(sha256sum "$local_ipk" | cut -d' ' -f1)
        if [ -n "$want" ] && [ "$want" != "$got" ]; then
            say "  ! the LGWS copy from your computer got damaged on the way (checksum); trying GitHub"
        else
            say "  LGWS: installing the copy your computer downloaded and checked"
            cp "$local_ipk" "$tmpipk" && install_ipk_lgws "$tmpipk"; rc=$?; rm -f "$tmpipk"
            [ $rc = 0 ] && return 0
        fi
    fi
    # 2. straight from GitHub, if the TV can get there
    say "  LGWS: not installed; trying to fetch it from GitHub on the TV"
    command -v curl >/dev/null 2>&1 || { say "  ! no curl on this TV"; return 1; }
    m=$(curl -fsL --max-time 30 "$LGWS_MANIFEST"); rc=$?
    [ $rc = 0 ] || { say "  ! couldn't download the LGWS manifest: $(curl_why $rc)"; return 1; }
    url=$(printf '%s' "$m" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);console.log(j.ipkUrl)})')
    sum=$(printf '%s' "$m" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);console.log((j.ipkHash||{}).sha256||"")})')
    case "$url" in https://github.com/lewisdoesstuff/lifesgoodwithoutspying/*) ;; *) say "  ! unexpected download address: $url"; return 1 ;; esac
    curl -fsL --max-time 120 -o "$tmpipk" "$url"; rc=$?
    [ $rc = 0 ] || { say "  ! LGWS download failed: $(curl_why $rc)"; return 1; }
    got=$(sha256sum "$tmpipk" | cut -d' ' -f1)
    [ -n "$sum" ] && [ "$got" = "$sum" ] || { say "  ! checksum mismatch, not installing"; rm -f "$tmpipk"; return 1; }
    install_ipk_lgws "$tmpipk"; rc=$?; rm -f "$tmpipk"; return $rc
}
remove_old_v1() {
    # v1 of the kit shipped these optional hooks; the agent replaces them
    rm -f "$INITD/50-tiles-home" "$INITD/51-tiles-home-watch" /var/lib/webosbrew/tiles-home-watch.js \
          /var/lib/webosbrew/tiles-home.conf
}

if [ "$MODE" = status ]; then
    say "Init hook:  $(ls -l "$INITD/60-lgkit" 2>/dev/null || echo 'not installed')"
    if grep -q " /etc/hosts " /proc/mounts; then say "Hosts:      blocking $(grep -c '^0\.0\.0\.0' /etc/hosts) names"; else say "Hosts:      stock"; fi
    if command -v iptables >/dev/null 2>&1 && iptables -S LGKIT >/dev/null 2>&1; then
        say "Firewall:   $(iptables -S LGKIT | grep -c REJECT) IPv4 rules"; else say "Firewall:   off"; fi
    say "Agent:      $(pgrep -f lgkit-agent.js >/dev/null 2>&1 && echo running || echo 'not running')"
    say "Kit extras: $(grep -qF "$KIT_MARK" /etc/hosts && echo "in /etc/hosts ($(grep -c '^0' "$D/hosts.extra" 2>/dev/null) names)" || echo 'not in /etc/hosts')"
    if [ -f "$LGWS_CTL" ]; then
        say "LGWS:       $(sh "$LGWS_CTL" status 2>/dev/null | grep -E '^(autostart|job|hosts|blocklist|stubs)=' | tr '\n' ' ')"
    else
        say "LGWS:       not installed"
    fi
    [ -f "$D/status.json" ] && cat "$D/status.json" && echo
    [ -f "$D/agent.log" ] && { say "Last log lines:"; tail -n 8 "$D/agent.log"; }
    exit 0
fi

if [ "$MODE" = uninstall ]; then
    say "Removing the LG TV Privacy Kit from this TV"
    rm -f "$INITD/60-lgkit" "$INITD/50-lgkit"; remove_old_v1
    stop_agent; say "  agent: stopped"
    remove_extras
    if grep -q '^LGWS_BY_KIT=1' "$D/lgkit.env" 2>/dev/null && [ -f "$LGWS_CTL" ]; then
        sh "$LGWS_CTL" disable >/dev/null && lgws_wait && say "  LGWS: protection switched off (the kit had switched it on)"
    elif [ -f "$LGWS_CTL" ]; then
        say "  LGWS: left on (it was on before the kit). Switch it off in its app if you want."
    fi
    grep -q '^LGWS=1' "$D/lgkit.env" 2>/dev/null || unmount_hosts
    flush_fw; say "  firewall: removed"
    rm -rf "$D"
    say "Done. Tiles Home is still installed: remove it from the app list if you don't want it."
    say "The privacy settings stay as they are; turn any back on in the TV's settings."
    exit 0
fi

# ------------------------------------------------------------------ install
[ -f "$SRC/root/lgkit-agent.js" ] || die "kit not found at $SRC (use --from DIR)"
command -v node >/dev/null 2>&1 || die "no node on this TV; the agent needs it"
say "Installing the LG TV Privacy Kit (rooted) from $SRC"
mkdir -p "$D" "$INITD" || die "can't create $D"

# Life's Good Without Spying: the telemetry layer
LGWS_BY_KIT=$(sed -n 's/^LGWS_BY_KIT=//p' "$D/lgkit.env" 2>/dev/null); LGWS_BY_KIT=${LGWS_BY_KIT:-0}
if [ "$LGWS" = 1 ]; then
    if [ -f "$LGWS_CTL" ]; then
        say "  LGWS: already installed ($(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$LGWS_DIR/appinfo.json"))"
    else
        install_lgws || die "Life's Good Without Spying isn't on the TV and couldn't be installed. Fix: open Homebrew Channel on the TV, install \"Life's Good Without Spying\", then run this again. Or run again with --no-lgws to use the kit's own blocklist instead. Nothing has been changed yet."
    fi
    # an older kit's own hosts mount would stop LGWS mounting its list
    grep -q " /etc/hosts " /proc/mounts && ! grep -qF "# lifesgoodwithoutspying" /etc/hosts && unmount_hosts
    on() { sh "$LGWS_CTL" set "$1" "$2" >/dev/null || say "  ! LGWS didn't take $1=$2"; }
    strict=0; case ",$TIERS," in *,strict,*) strict=1 ;; esac
    for k in domains.smartad domains.acr domains.dashboard domains.telemetry ads.stop purge.boot; do on $k on; done
    on domains.sdp "$([ "$KEEP_STORE" = 1 ] && echo off || echo on)"
    on voice.stop "$([ "$KEEP_VOICE" = 1 ] && echo off || echo on)"
    for k in domains.lgrecommends domains.wiselg domains.thinq domains.lgchannels; do on $k "$([ $strict = 1 ] && echo on || echo off)"; done
    on domains.updates "$([ "$BLOCK_UPDATES" = 1 ] && echo on || echo off)"
    remove_extras   # LGWS rebuilds its list now; the kit adds its extras again afterwards
    if sh "$LGWS_CTL" status 2>/dev/null | grep -q '^autostart=on'; then
        sh "$LGWS_CTL" apply >/dev/null
    else
        sh "$LGWS_CTL" enable >/dev/null && LGWS_BY_KIT=1
    fi
    say "  LGWS: applying (up to a few minutes)…"
    lgws_wait && say "  LGWS: $(sh "$LGWS_CTL" status 2>/dev/null | grep -E '^(hosts|blocklist|stubs)=' | tr '\n' ' ')" \
        || say "  ! LGWS reported a problem: sh $LGWS_CTL joblog"
    fi

# blocklist
tmp=$D/hosts.block.new
: >"$tmp"
for t in $(echo "$TIERS" | tr ',' ' '); do
    f=$SRC/blocklist/lg-$t.hosts
    [ -s "$f" ] || die "no blocklist for tier $t ($f)"
    grep -E '^0\.0\.0\.0[[:space:]]+[A-Za-z0-9._-]+$' "$f" >>"$tmp"
done
[ "$BLOCK_UPDATES" = 1 ] && grep -E '^0\.0\.0\.0[[:space:]]' "$SRC/blocklist/lg-firmware-updates.hosts" >>"$tmp"
sort -u "$tmp" >"$D/hosts.block" && rm -f "$tmp"
if [ "$LGWS" = 1 ]; then
    # only what LGWS's own list doesn't already cover
    cat "$LGWS_DIR"/init/blocklist.d/*.txt 2>/dev/null | grep -v '^[[:space:]]*#' | awk 'NF{print $1}' | sort -u >/tmp/lgkit.lgws
    awk 'NR==FNR { have[$1] = 1; next } !($2 in have)' /tmp/lgkit.lgws "$D/hosts.block" >"$D/hosts.extra"
    rm -f /tmp/lgkit.lgws
    say "  kit extras: $(wc -l <"$D/hosts.extra") names on top of LGWS (tiers: $TIERS)"
else
    say "  blocklist: $(wc -l <"$D/hosts.block") names (tiers: $TIERS$([ "$BLOCK_UPDATES" = 1 ] && echo ', firmware updates'))"
fi

# agent, boot hook, settings
cp "$SRC/root/lgkit-agent.js" "$SRC/root/lgkit-boot.sh" "$SRC/root/find-home-key.js" "$SRC/root/install.sh" "$D/" || die "copy failed"
[ -s "$SRC/root/settings.json" ] && cp "$SRC/root/settings.json" "$D/"
chmod 755 "$D/lgkit-boot.sh" "$D/install.sh"
if [ -z "$HOME_CODE" ] && [ -f "$D/lgkit.env" ]; then HOME_CODE=$(sed -n 's/^HOME_CODE=//p' "$D/lgkit.env"); fi
cat >"$D/lgkit.env" <<EOF
# written by install.sh; edit and reboot (or re-run install.sh) to change
TILES_APP=$APP
LGWS=$LGWS
LGWS_BY_KIT=$LGWS_BY_KIT
HOSTS=$([ "$LGWS" = 1 ] && echo 0 || echo 1)
FIREWALL=$FIREWALL
BOOT_TO_TILES=$BOOT_TO_TILES
REDIRECT_HOME=$REDIRECT_HOME
GUARD_HOURS=$GUARD_HOURS
HOME_CODE=${HOME_CODE:-0}
EOF

remove_old_v1
rm -f "$INITD/50-lgkit"
ln -sf "$D/lgkit-boot.sh" "$INITD/60-lgkit"
say "  boot hook: $INITD/60-lgkit"

# Tiles Home app
if [ "$SKIP_APP" = 0 ]; then
    ipk=$(ls -t "$SRC"/dist/*.ipk 2>/dev/null | head -n1)
    if [ -n "$ipk" ]; then
        mkdir -p /media/developer/temp
        cp "$ipk" /media/developer/temp/lgkit-tiles.ipk
        r=$(luna-send -n 1 -w 30000 luna://com.webos.appInstallService/dev/install \
            '{"id":"com.ares.defaultName","ipkUrl":"/media/developer/temp/lgkit-tiles.ipk","subscribe":true}' 2>&1)
        case "$r" in
            *'"returnValue":true'*|*'"returnValue": true'*) say "  Tiles Home: installed from $(basename "$ipk")" ;;
            *) say "  ! Tiles Home install said: $r"; say "    Install the .ipk from Homebrew Channel or Dev Manager instead." ;;
        esac
    else
        say "  Tiles Home: no .ipk in $SRC/dist, skipping (build it with tools/build_ipk.py)"
    fi
fi

# apply now: restart cleanly so new settings take effect
stop_agent; flush_fw
[ "$LGWS" = 1 ] || unmount_hosts
LGKIT_FIRST_BOOT=1 sh "$D/lgkit-boot.sh"
sleep 1
tail -n 6 "$D/boot.log" | sed 's/^/  /'
say ""
say "Done. Press Home on the remote: Tiles Home should open."
[ "${HOME_CODE:-0}" = 0 ] && say "Tip: run 'node $D/find-home-key.js', press Home, then re-run with --home-code N so the Home button is caught even faster."
say "Status any time: sh $D/install.sh --status    Undo: sh $D/install.sh --uninstall"
