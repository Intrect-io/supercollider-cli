#!/usr/bin/env bash
# Created: 2026-09-15
# Purpose: Start a headless realtime scsynth via PipeWire's JACK and wire its
#          output ports to the current default PipeWire sink.
# Dependencies: pipewire-jack, wpctl/pw-link, scsynth built with SC_QT=OFF
# Test Status: verified 2026-09-15 (server ready + ports linked)
#
# The sink is discovered at run time -- its name is NOT stable (it changes when
# a USB interface is plugged in, and the *configured* default can differ from
# the sink that is actually active).

set -euo pipefail

SCSYNTH="${SCSYNTH:-$HOME/dev/supercollider/build/server/scsynth/scsynth}"
PLUGINS="${SC_PLUGINS:-$HOME/dev/supercollider/build/server/plugins}"
JACK_LIB="${PIPEWIRE_JACK:-/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack}"
PORT="${SC_PORT:-57210}"
CHANNELS="${SC_CHANNELS:-2}"
LOG="${SC_LOG:-/tmp/scsynth-rt.log}"

if [[ ! -x "$SCSYNTH" ]]; then
    echo "scsynth not found at $SCSYNTH" >&2
    exit 1
fi

if pgrep -x scsynth >/dev/null 2>&1; then
    echo "scsynth already running (pid $(pgrep -x scsynth | head -1))"
else
    echo "starting scsynth on udp/$PORT, $CHANNELS output channels..."
    : > "$LOG"
    LD_LIBRARY_PATH="$JACK_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        nohup "$SCSYNTH" -U "$PLUGINS" -u "$PORT" -o "$CHANNELS" -i 0 >>"$LOG" 2>&1 &

    for _ in $(seq 1 60); do
        grep -q "server ready" "$LOG" 2>/dev/null && break
        sleep 0.2
    done

    if ! grep -q "server ready" "$LOG"; then
        echo "scsynth failed to start. log:" >&2
        tail -20 "$LOG" >&2
        exit 1
    fi
    echo "  $(grep -m1 'JackDriver' "$LOG")"
    echo "  $(grep -m1 'server ready' "$LOG")"
fi

# --- discover the currently active default sink (marked '*' in wpctl status) ---
sink_id=$(wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' \
    | grep '\*' | grep -oE '[0-9]+\.' | tr -d '.' | head -1 || true)

if [[ -z "${sink_id:-}" ]]; then
    echo "no default sink found -- is PipeWire running?" >&2
    exit 1
fi

sink_name=$(wpctl inspect "$sink_id" 2>/dev/null \
    | grep -oP 'node\.name = "\K[^"]+' | head -1)

if [[ -z "${sink_name:-}" ]]; then
    echo "could not resolve node.name for sink id $sink_id" >&2
    exit 1
fi

echo "default sink: $sink_name"

# --- link scsynth outputs to that sink's playback ports (idempotent) ---
mapfile -t targets < <(pw-link -i 2>/dev/null | grep "^${sink_name}:playback" | sort)

if [[ ${#targets[@]} -eq 0 ]]; then
    echo "sink has no playback ports yet -- is the device present?" >&2
    exit 1
fi

linked=0
for i in $(seq 1 "$CHANNELS"); do
    [[ $i -gt ${#targets[@]} ]] && break
    src="SuperCollider:out_${i}"
    dst="${targets[$((i - 1))]}"
    pw-link -d "$src" "$dst" >/dev/null 2>&1 || true   # drop a stale link first
    if pw-link "$src" "$dst" >/dev/null 2>&1; then
        echo "  linked $src -> $dst"
        linked=$((linked + 1))
    else
        echo "  FAILED to link $src -> $dst" >&2
    fi
done

if [[ $linked -eq 0 ]]; then
    echo "no ports were linked -- there will be no sound" >&2
    exit 1
fi

echo "ready: scsynth on udp/$PORT, $linked channels linked to $sink_name"
