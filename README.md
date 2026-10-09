# supercollider-cli

Drive a headless [SuperCollider](https://supercollider.github.io/) server from the
command line, a browser, or a coding agent — no IDE, no GUI, no audio device
required for offline rendering.

The project grew out of one question: *can a coding agent write a SuperCollider
patch and actually hear it?* It answers that with three layers that can be used
independently:

| Layer | What it does | Entry point |
|---|---|---|
| **Offline render (NRT)** | `.scd` → OSC score → `scsynth -N` → WAV. No server, no sound card. Includes a bridge that runs [BBCut](https://github.com/nhthn/BBCut) cut procedures without a booted server. | `testing/*.scd` |
| **OSC control surface** | Talks scsynth's native OSC protocol directly (no sclang at runtime): play, set, free, inspect the node tree, send raw messages. | `sccli` |
| **Remote listening + web UI** | Captures scsynth's output from PipeWire and streams it to any number of browsers (low-latency PCM over WebSocket, MP3 fallback), with a control panel that drives the same OSC layer. | `scweb.py` |

Everything in Python is **stdlib only** — zero third-party dependencies.

> **Status: experimental.** Built and verified on one Linux host (PipeWire 1.0,
> SuperCollider 3.15-dev built from source). Expect to adjust paths for your
> machine. There are no automated tests yet; verification so far has been
> end-to-end against a live server.

## Requirements

- Linux with **PipeWire** (+ WirePlumber) for realtime playback
- **SuperCollider** (`scsynth`, `sclang`) — a headless build (`-DSC_QT=OFF`) is enough
- **`pipewire-jack`** — scsynth on Linux links only `libjack`; this lets it attach
  to the already-running PipeWire instead of a separate `jackd`
- **Python 3.10+** (stdlib only)
- **ffmpeg** — only for `scweb.py` (audio capture/encoding)
- **BBCut** — only for the BBCut render bridge

Default locations assume a source build under `~/dev/supercollider`. Override
them with environment variables:

| Variable | Default | Used by |
|---|---|---|
| `SCSYNTH` | `~/dev/supercollider/build/server/scsynth/scsynth` | `scripts/scsynth-up.sh`, NRT scripts |
| `SC_PLUGINS` | `~/dev/supercollider/build/server/plugins` | `scripts/scsynth-up.sh` |
| `SCLANG` | `~/dev/supercollider/build/lang/sclang` | `sccli defs compile` |
| `PIPEWIRE_JACK` | `/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack` | `scripts/scsynth-up.sh` |
| `SC_HOST` / `SC_PORT` | `127.0.0.1` / `57210` | `sccli`, `scweb.py` |
| `SC_CHANNELS` | `2` | `scripts/scsynth-up.sh` |
| `SC_LOG` | `/tmp/scsynth-rt.log` | `scripts/scsynth-up.sh` |
| `BBCUT_DIR` | `~/dev/BBCut` | BBCut render scripts |

## Quick start (realtime)

```bash
# 1. Boot scsynth through PipeWire's JACK and link its outputs to the active sink
scripts/scsynth-up.sh

# 2. Load SynthDefs (precompiled ones ship in defs/compiled/)
./sccli defs load
#    ...or recompile from source (requires sclang):
./sccli defs compile defs/tone.scd

# 3. Make sound
./sccli play drone freq=330 amp=0.12     # -> "#1000 drone freq=330 amp=0.12"
./sccli tree --controls                  # show the node tree with control values
./sccli set 1000 freq=550                # change a running synth
./sccli free 1000                        # or: ./sccli free-all
./sccli status                           # CPU, UGen/synth/group counts
```

### `sccli` commands

| Command | OSC | Notes |
|---|---|---|
| `status` | `/status` | |
| `tree [--group N] [--controls]` | `/g_queryTree` | Parsed into a nested tree |
| `defs compile <file.scd> [--dir D]` | — | Runs sclang; the `.scd` writes `.scsyndef` files into `~defsOutDir` |
| `defs load [--dir D]` | `/d_loadDir` | Waits for `/synced` |
| `play <def> [k=v ...] [--id N] [--target N] [--action N]` | `/s_new` | Picks a free node id by querying the server |
| `set <node> k=v ...` | `/n_set` | |
| `free <node>` / `free-all` | `/n_free` / `/g_freeAll` | |
| `send <address> [args ...]` | any | Raw escape hatch. Numbers are auto-typed; `#57` sends integer command 57 |

Global options: `--host`, `--port`.

## Web UI and remote listening

```bash
./scweb.py --http-port 8099
# open http://<host>:8099
```

- `ws://…/ws` — raw float32 stereo PCM (48 kHz) over WebSocket, played by an
  `AudioWorklet`. Roughly 100–250 ms end-to-end, good enough to hear slider moves.
- `http://…/audio` — MP3 fallback (several seconds of browser buffering).
- `GET /api/state`, `POST /api/play|set|free|freeall` — JSON control API.

One ffmpeg process captures the PipeWire monitor of **the sink scsynth is actually
linked to** (falling back to the default sink), and the server fans the stream out
to every connected client.

> **Security:** `scweb.py` has **no authentication** and binds to `0.0.0.0` by
> default. Anyone who can reach the port can listen and control the server. Run it
> on a trusted LAN or VPN (e.g. Tailscale), or pass `--host 127.0.0.1`. Never
> expose it to the public internet.

## Offline rendering (NRT)

No audio device or running server is needed:

```bash
sclang testing/smoke_nrt_260915_01.scd     # minimal patch -> /tmp/smoke_render.wav
sclang testing/bbcut_render_260915_01.scd  # BBCut break cutting -> OSC score -> WAV
```

The BBCut bridge works because BBCut only needs a server to *send* messages, not
to compute cuts. It redirects `Server.default.addr` to a `BundleNetAddr`, pulls
blocks from the cut procedure by hand, and writes the collected messages as a
`Score`. `scclasses/OfflineRender.sc` provides a guarded override of
`Server:serverRunning` so BBCut constructors accept an unbooted server — add
`scclasses/` to sclang's `includePaths` and only set
`OfflineRender.forceServer = true` inside scripts that never talk to a real server.
With a fixed `thisThread.randSeed`, renders are bit-identical.

## Pitfalls this project already paid for

- **OSC strings need a NUL terminator *and* 4-byte padding.** Padding alone is
  not a terminator: addresses and type-tag strings whose length is a multiple of
  4 (`/n_query`, `,isi`) silently break without it.
- **`/status.reply` and `/g_queryTree.reply` start with a flag argument.**
- **Node ids are chosen by the client.** Every CLI call is a new process, so
  `sccli` asks the server which ids are in use instead of keeping a counter.
- **JACK clients are not auto-connected.** `scsynth-up.sh` links ports with
  `pw-link`; without it there is no sound.
- **Don't capture "the default sink".** It can change under you while scsynth
  stays linked elsewhere — follow scsynth's links.
- **Streaming needs HTTP/1.1.** `BaseHTTPRequestHandler` defaults to HTTP/1.0,
  which has no chunked encoding.
- **NRT `Score` entries must be nested** (`[time, [cmd, args...]]`). A flat array
  renders silence with no error.
- **`SynthDef:send` silently does nothing without a booted server** — embed
  `.asBytes` in the score instead.
- **`Node:creationCmd` uses integer command codes** (e.g. 21 = `/g_new`), which
  must be converted when assembling a score by hand.

## Repository layout

```
sccli                  CLI entry point (OSC control surface)
sclib.py               stdlib OSC encoder/decoder + SCClient (shared by sccli and scweb)
scweb.py               HTTP/WebSocket server: audio fan-out + JSON control API
web/index.html         browser UI (AudioWorklet player + controls)
scripts/scsynth-up.sh  boot realtime scsynth via pipewire-jack and link outputs
defs/                  SynthDef sources (.scd) and compiled .scsyndef files
scclasses/             sclang class extensions (OfflineRender shim)
testing/               NRT render experiments and probes (.scd)
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the pieces fit together.

## License

[GPL-3.0](LICENSE), matching SuperCollider and BBCut.
