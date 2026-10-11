#!/bin/bash
# Install the working tree into a local Kodi and (re)load it.
#
# Usage: tools/dev-install.sh [--flatpak]
#
#   (default)   the native Kodi under ~/.kodi
#   --flatpak   the flatpak Kodi (tv.kodi.Kodi) under ~/.var/app/tv.kodi.Kodi/data
#
# The JSON-RPC port, its credentials and the EventServer port are read from the
# target's own userdata/guisettings.xml; the credentials are parsed inside this
# script and passed to curl through a variable, never printed. A same-version
# service is not restarted by Kodi's UpdateLocalAddons: the enable below is a
# disable/enable bounce for that reason.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
KODI_HOME="$HOME/.kodi"
TARGET="native"

case "${1:-}" in
    --flatpak) KODI_HOME="$HOME/.var/app/tv.kodi.Kodi/data"; TARGET="flatpak" ;;
    "") ;;
    *) echo "usage: $0 [--flatpak]" >&2; exit 2 ;;
esac

ADDON_ID="service.kofin.companion"
DEST="$KODI_HOME/addons/$ADDON_ID"
GUISETTINGS="$KODI_HOME/userdata/guisettings.xml"

read -r RPC_PORT ES_PORT RPC_USER RPC_PASS < <(python3 - "$GUISETTINGS" <<'PY'
import sys, xml.etree.ElementTree as E
try:
    root = E.parse(sys.argv[1]).getroot()
except Exception:
    root = None
def get(key, default):
    if root is None:
        return default
    node = root.find(f"./setting[@id='{key}']")
    return default if node is None or not (node.text or "").strip() else node.text.strip()
print(get("services.webserverport", "8080"), get("services.esport", "9777"),
      get("services.webserverusername", "kodi"), get("services.webserverpassword", "kodi"))
PY
)
KODI_RPC="http://localhost:${RPC_PORT}/jsonrpc"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
python3 "$SRC/tools/build.py" --stage "$STAGE"
mkdir -p "$DEST"
rsync -a --delete "$STAGE/" "$DEST/"

rpc() {
    curl -s -m "${2:-5}" -u "$RPC_USER:$RPC_PASS" -X POST -H 'Content-Type: application/json' \
        -d "$1" "$KODI_RPC"
}

builtin() {
    python3 - "$1" "$ES_PORT" <<'PY'
import socket, struct, sys
payload = b"\x02" + sys.argv[1].encode("utf-8") + b"\x00"
header = (b"XBMC" + struct.pack(">BB", 2, 0) + struct.pack(">H", 0x0A)
          + struct.pack(">II", 1, 1) + struct.pack(">H", len(payload))
          + struct.pack(">I", 0) + b"\x00" * 10)
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(header + payload, ("127.0.0.1", int(sys.argv[2])))
PY
}

if ! rpc '{"jsonrpc":"2.0","id":1,"method":"JSONRPC.Ping"}' 2 | grep -q '"pong"'; then
    echo "installed to $DEST ($TARGET Kodi not reachable on :$RPC_PORT; skipped reload/enable)"
    exit 0
fi

# curl succeeds on a JSON-RPC error reply, so the reply is read: a refused
# disable leaves the old service running, a refused enable leaves none.
set_enabled() {
    reply="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Addons.SetAddonEnabled\",\"params\":{\"addonid\":\"$ADDON_ID\",\"enabled\":$1}}")" || reply=""
    case "$reply" in
        *'"result":"OK"'*) ;;
        *)
            echo "installed to $DEST, but Kodi refused enabled=$1 for $ADDON_ID: ${reply:-no reply}" >&2
            exit 1
            ;;
    esac
}

builtin 'UpdateLocalAddons()'
sleep 2
set_enabled false
sleep 2
set_enabled true
echo "installed and enabled $ADDON_ID ($TARGET Kodi on :$RPC_PORT)"
