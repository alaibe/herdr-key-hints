#!/bin/sh
# The binary lives in the state directory: the install directory moves with each update.
set -eu

state="${HERDR_PLUGIN_STATE_DIR:?run this through Herdr: herdr plugin action invoke hold-on --plugin alaibe.key-hints}"
root="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
label="${HERDR_PLUGIN_ID:-alaibe.key-hints}.watcher"
binary="$state/key-hints-watcher"
agent="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

build() {
    source="$root/watcher/main.swift"
    sum="$(shasum -a 256 "$source" | cut -d ' ' -f 1)"
    if [ -x "$binary" ] && [ "$(cat "$binary.sha256" 2>/dev/null)" = "$sum" ]; then
        return
    fi
    if ! command -v swiftc >/dev/null 2>&1; then
        echo "key-hints: swiftc not found. Install Xcode's command line tools (xcode-select --install), then run hold-on again." >&2
        exit 1
    fi
    mkdir -p "$state"
    swiftc -O -swift-version 5 -o "$binary.new" "$source"
    mv "$binary.new" "$binary"
    printf '%s\n' "$sum" >"$binary.sha256"
    echo "key-hints: built $binary"
}

stop() {
    launchctl bootout "$domain/$label" 2>/dev/null || true
    # bootout returns before the job is gone, and bootstrapping it again before then fails.
    tries=0
    while launchctl print "$domain/$label" >/dev/null 2>&1 && [ "$tries" -lt 20 ]; do
        sleep 0.1
        tries=$((tries + 1))
    done
}

start() {
    mkdir -p "$(dirname "$agent")"
    sh "$root/run" launch-agent "$label" "$binary" >"$agent.new" || {
        rm -f "$agent.new"
        exit 1
    }
    mv "$agent.new" "$agent"
    stop
    launchctl bootstrap "$domain" "$agent"
    echo "key-hints: watching for a held ⌘ or ⌃ ($agent)"
}

case "${1:-}" in
    on)
        build
        start
        ;;
    off)
        stop
        rm -f "$agent"
        echo "key-hints: stopped watching for a held ⌘ or ⌃"
        ;;
    refresh)
        [ -f "$agent" ] || exit 0
        build
        start
        ;;
    *)
        echo "usage: setup.sh on | off | refresh" >&2
        exit 2
        ;;
esac
