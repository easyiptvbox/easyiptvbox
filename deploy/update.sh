#!/bin/bash
# Fetch the latest main from GitHub and apply it. Run as root (by the
# iptv-update.timer, or "Update now" in the web app). Rolls back if the player
# fails to start after the update.
set -uo pipefail

SRC="${IPTV_SRC:-/opt/easyiptvbox}"
URL="${IPTV_REPO_URL:-https://github.com/easyiptvbox/easyiptvbox.git}"
BRANCH="${IPTV_BRANCH:-main}"
STATE=/var/lib/iptv-update
FLAG=/run/iptv-app-changed
mkdir -p "$STATE"

status() { # status <result> <message>
    printf 'time=%s\nresult=%s\ncommit=%s\nmessage=%s\n' \
        "$(date -Is)" "$1" "$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null)" "$2" > "$STATE/status"
    echo "$1: $2"
}

# Whole body in a function: git may replace this file while it runs.
main() {
exec 9>"$STATE/lock"
flock -n 9 || { echo "update already running"; return 0; }

if [ ! -d "$SRC/.git" ]; then
    git clone --branch "$BRANCH" "$URL" "$SRC" || { status error "clone failed (offline?)"; return 0; }
fi
git -C "$SRC" remote set-url origin "$URL"

if ! git -C "$SRC" fetch --quiet origin "$BRANCH"; then
    status error "could not reach GitHub"; return 0
fi
OLD="$(git -C "$SRC" rev-parse HEAD)"
NEW="$(git -C "$SRC" rev-parse "origin/$BRANCH")"
if [ "$OLD" = "$NEW" ] && [ "${1:-}" != "--force" ]; then
    status ok "already up to date"; return 0
fi

git -C "$SRC" checkout -q -B "$BRANCH" "origin/$BRANCH" || { status error "checkout failed"; return 0; }
if ! "$SRC/deploy/apply.sh"; then
    git -C "$SRC" checkout -q -B "$BRANCH" "$OLD"; "$SRC/deploy/apply.sh" || true
    status error "apply failed, rolled back to ${OLD:0:7}"; return 1
fi

if [ -e "$FLAG" ]; then
    systemctl restart iptv.service
    sleep 30
    if ! systemctl is-active --quiet iptv.service; then
        git -C "$SRC" checkout -q -B "$BRANCH" "$OLD"
        "$SRC/deploy/apply.sh" || true
        systemctl restart iptv.service
        status error "player failed after update, rolled back to ${OLD:0:7}"; return 1
    fi
fi
status ok "updated ${OLD:0:7} -> ${NEW:0:7}"
}
main "$@"
exit $?
