# Architecture

Three independent paths share one principle: **sclang is a compiler, not a
runtime.** sclang is used to produce artifacts (`.scsyndef` files, OSC score
files); everything that happens at run time talks to `scsynth` directly over OSC.

```
                    ┌──────────── offline (NRT) ────────────┐
 testing/*.scd ──▶ sclang ──▶ Score (.osc) ──▶ scsynth -N ──▶ WAV
                    │  (BBCut cut logic runs here; messages are
                    │   captured by a BundleNetAddr, not sent)
                    └────────────────────────────────────────┘

                    ┌──────────── realtime ─────────────────────────────────┐
 defs/*.scd ──▶ sclang ──▶ defs/compiled/*.scsyndef                         │
                                     │ /d_loadDir                            │
 sccli ─────┐                        ▼                                       │
            ├─▶ sclib.SCClient ──UDP/OSC──▶ scsynth ──JACK API──▶ PipeWire ──▶ sink
 scweb.py ──┘        ▲                       (pipewire-jack)        │        │
   ▲   │             └── replies to the client's ephemeral port     │monitor │
   │   └── ffmpeg ◀── capture ──────────────────────────────────────┘        │
   │         ├─▶ f32le PCM ──▶ PCMHub  ──▶ WebSocket /ws ──▶ AudioWorklet     │
   │         └─▶ MP3       ──▶ AudioHub ──▶ HTTP /audio  (fallback)           │
   └── JSON control API (POST /api/*) ◀── web/index.html                     │
                    └───────────────────────────────────────────────────────┘
```

## Components

**`sclib.py`** — The only OSC implementation. Encodes/decodes messages with
`struct` (int32, float32, string, blob), parses `/g_queryTree.reply` into a
nested dict, and wraps a UDP socket in `SCClient`. The socket binds an ephemeral
port because scsynth replies to the sender's address. `request(..., want=addr)`
returns as soon as the expected reply arrives and discards stale datagrams;
`sync()` blocks on `/sync` → `/synced` so mutations are ordered.

**`sccli`** — Thin argparse front end over `SCClient`. Each invocation is a new
process, so it never keeps state: free node ids are found by querying the tree.
`defs compile` writes a temporary wrapper `.scd` that sets `~defsOutDir` and
executes the source file under sclang.

**`scweb.py`** — `ThreadingHTTPServer` (HTTP/1.1, required for chunked
streaming) with three concerns:

- *Capture*: picks the PipeWire monitor of the sink scsynth is linked to
  (`pw-link -l`), falling back to the default sink.
- *Fan-out*: one ffmpeg per format; a pump thread copies chunks into a per-client
  `queue.Queue`. Slow clients drop chunks instead of blocking the pump.
- *Control*: `Control` owns the single `SCClient` behind a lock. A background
  thread refreshes a status/tree cache once per second, so `GET /api/state`
  never touches OSC and cannot starve `POST /api/set`.

WebSocket handshake and framing (RFC 6455, server→client only) are implemented
by hand to keep the dependency count at zero.

**`scripts/scsynth-up.sh`** — Starts scsynth with `LD_LIBRARY_PATH` pointed at
pipewire-jack (non-invasive: the system libjack is untouched), waits for
`server ready`, discovers the current default sink via `wpctl`, and links
`SuperCollider:out_N` to its playback ports with `pw-link`.

**`scclasses/OfflineRender.sc`** — Extends `Server:serverRunning` behind a class
flag so BBCut constructors run without a booted server. Off by default.

## Concurrency notes

- One UDP socket per `SCClient`; replies are not correlated by id, so concurrent
  requests on the same client would steal each other's replies. `scweb.py`
  serialises all OSC access through `Control.lock`. `sccli` is single-threaded.
- Multiple `sccli` processes against one server are safe for mutations, but
  `play` without `--id` has a check-then-act race on node ids (two processes can
  pick the same free id). Pass `--id` when scripting parallel calls.
- `scsynth-up.sh` checks `pgrep -x scsynth` before starting; it does not detect
  a scsynth bound to a different port.

## Non-goals

- Authentication or public exposure of `scweb.py`.
- Sub-50 ms remote monitoring (would need WebRTC).
- Replacing sclang for pattern/sequencing work.
