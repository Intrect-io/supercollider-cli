#!/usr/bin/env python3
# Created: 2026-09-15
# Purpose: Serve the scsynth output as a browser-playable audio stream and expose
#          an OSC control API, so one web page can both listen and drive it.
# Dependencies: none (stdlib only); ffmpeg for capture/encode; sclib for OSC
# Test Status: verified 2026-09-15 (2 concurrent /audio clients, control API mutates state)
#
# Design notes:
#   * ONE ffmpeg captures the sink monitor and writes MP3 to stdout. The server
#     fans those chunks out to every client, so N browsers can listen at once --
#     `ffmpeg -listen 1` cannot do that (it serves a single client and exits).
#   * Control is HTTP POST, not WebSocket. Commands only happen when a human
#     clicks something, so a socket would buy nothing and cost a dependency.
#   * No auth. Bind to a tailnet/LAN address and do not expose it publicly.

import base64
import hashlib
import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sclib import DEFAULT_HOST, DEFAULT_PORT, SCClient  # noqa: E402

REPO = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(REPO, "web")
DEFS_DIR = os.path.join(REPO, "defs", "compiled")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_frame(opcode, payload):
    """One WebSocket frame (RFC 6455), server->client (unmasked)."""
    first = 0x80 | opcode
    n = len(payload)
    if n < 126:
        header = bytes((first, n))
    elif n < 65536:
        header = bytes((first, 126)) + struct.pack(">H", n)
    else:
        header = bytes((first, 127)) + struct.pack(">Q", n)
    return header + payload


# --------------------------------------------------------------------------
# audio capture + fan-out
# --------------------------------------------------------------------------

def default_sink_monitor():
    """Resolve '<default sink node name>.monitor' from PipeWire.

    The sink name is NOT stable -- it changes when a USB interface appears, and the
    configured default can differ from the sink that is actually active.
    """
    try:
        status = subprocess.run(["wpctl", "status"], capture_output=True, text=True,
                                timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    block = re.search(r"Sinks:(.*?)Sources:", status, re.S)
    if not block:
        return None
    default = re.search(r"\*\s+(\d+)\.", block.group(1))
    if not default:
        return None
    sink_id = default.group(1)
    try:
        info = subprocess.run(["wpctl", "inspect", sink_id], capture_output=True,
                              text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'node\.name = "([^"]+)"', info)
    return f"{m.group(1)}.monitor" if m else None


def capture_source():
    """Pick the source that actually carries scsynth's output.

    The default sink is the WRONG answer in general: it can change under us (a
    display gets plugged in, WirePlumber re-evaluates) while scsynth stays linked
    to wherever it was wired up. Capturing the default sink then yields silence.
    So follow scsynth's own output links first, and only fall back to the default.
    """
    try:
        links = subprocess.run(["pw-link", "-l"], capture_output=True, text=True,
                               timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        links = ""
    flat = " ".join(links.split())
    m = re.search(r"SuperCollider:out_\d+ \|-> (\S+?):playback", flat)
    if m:
        return f"{m.group(1)}.monitor", "linked to scsynth"
    fallback = default_sink_monitor()
    return (fallback, "default sink (no scsynth link found)") if fallback else (None, "")


class AudioHub:
    """One ffmpeg, many listeners."""

    CHUNK = 4096
    SEED = 24          # chunks replayed to a late joiner so its decoder can sync

    def __init__(self, source, bitrate="128k"):
        self.source = source
        self.bitrate = bitrate
        self.clients = set()
        self.lock = threading.Lock()
        self.recent = deque(maxlen=self.SEED)
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "pulse", "-i", self.source,
             "-c:a", "libmp3lame", "-b:a", self.bitrate, "-f", "mp3", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            chunk = self.proc.stdout.read(self.CHUNK)
            if not chunk:
                break
            with self.lock:
                self.recent.append(chunk)
                targets = list(self.clients)
            for q in targets:
                try:
                    q.put_nowait(chunk)
                except queue.Full:  # cxt-ignore: exception_hiding -- narrow, intentional
                    pass          # a stalled client must not stall the others

    def subscribe(self):
        q = queue.Queue(maxsize=256)
        with self.lock:
            for chunk in self.recent:      # seed so playback starts immediately
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    break
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def stop(self):
        if self.proc:
            self.proc.terminate()


class PCMHub(AudioHub):
    """Raw float32 (stereo, 48 kHz) capture, many listeners.

    Same fan-out as AudioHub but the payload is uncompressed PCM: a browser can
    play it with a tiny worklet buffer (~100-250 ms end to end) rather than the
    seconds of buffering a compressed live stream inherently needs. This is the
    real-time path; AudioHub/MP3 stays as a fallback.
    """

    CHUNK = 16384      # 2048 stereo frames @48k = ~43 ms per frame
    SEED = 3           # ~128 ms of imm
    media = "application/octet-stream"

    def start(self):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "pulse", "-i", self.source,
             "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._pump, daemon=True).start()


# --------------------------------------------------------------------------
# OSC
# --------------------------------------------------------------------------

class Control:
    """Serialises access to the single UDP client (replies share one socket).

    State is refreshed by a background thread into a cache: /api/state returns
    the cache instantly. If the handler queried the OSC client on every poll, the
    0.x s socket waits would pile up on the lock (the browser polls every second,
    each poll would need two round-trips), starving /api/set for seconds -- which
    showed up as "slider changes the value but the pitch never moves".
    """

    def __init__(self, host, port):
        self.client = SCClient(host, port)
        self.lock = threading.Lock()
        self.cache = {"status": None, "tree": None, "updated": 0.0}

    def start(self):
        def loop():
            while True:
                try:
                    with self.lock:
                        self.cache["status"] = self.client.status()
                        self.cache["tree"] = self.client.query_tree(0, 1)
                        self.cache["updated"] = time.time()
                        self.cache.pop("last_error", None)
                except Exception as exc:               # scsynth may be down briefly
                    self.cache["last_error"] = str(exc)  # surface, don't hide
                time.sleep(1.0)

        threading.Thread(target=loop, daemon=True).start()

    def state(self):
        return self.cache


def available_defs():
    if not os.path.isdir(DEFS_DIR):
        return []
    return sorted(f[:-len(".scsyndef")] for f in os.listdir(DEFS_DIR)
                  if f.endswith(".scsyndef"))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 is REQUIRED: HTTP/1.0 does not support chunked transfer encoding, and
    # a live audio stream has no Content-Length. Under 1.0 the browser reads the
    # chunk framing as audio data and reports "no supported source was found".
    protocol_version = "HTTP/1.1"
    server_version = "scweb"
    hub = None          # AudioHub  (MP3 fallback stream)
    pcm = None          # PCMHub    (real-time stream)
    control = None

    def log_message(self, fmt, *args):
        pass          # keep the console for real events

    def _route(self):
        """Path without the query string -- the UI appends ?<ts> to bust caches."""
        return urlparse(self.path).path

    def _ws(self):
        """Upgrade to WebSocket (RFC 6455) and stream PCM as binary frames."""
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        q = self.pcm.subscribe()
        try:
            while True:
                try:
                    chunk = q.get(timeout=1.0)
                except queue.Empty:
                    if self.pcm.proc.poll() is not None:
                        break           # producer died: end the frame stream
                    continue
                self.wfile.write(ws_frame(0x2, chunk))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):  # cxt-ignore: exception_hiding -- narrow, intentional
            pass
        finally:
            self.pcm.unsubscribe(q)

    # ---- helpers ----
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _static(self, path, ctype):
        full = os.path.join(WEB_DIR, path)
        if not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- routes ----
    def do_GET(self):
        r = self._route()
        if r in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if r == "/ws":
            return self._ws()
        if r == "/audio":
            return self._audio()
        if r == "/api/state":
            st = self.control.state()
            return self._json({"status": st["status"], "tree": st["tree"],
                               "defs": available_defs()})
        self.send_error(404)

    def do_POST(self):
        r = self._route()
        body = self._read_json()
        c = self.control.client
        try:
            if r == "/api/play":
                params = {k: v for k, v in (body.get("params") or {}).items()}
                with self.control.lock:
                    nid = c.play(body["def"], params)
                return self._json({"id": nid})
            if r == "/api/set":
                with self.control.lock:
                    c.set_controls(int(body["id"]), body.get("params") or {})
                return self._json({"ok": True})
            if r == "/api/free":
                with self.control.lock:
                    c.free(int(body["id"]))
                return self._json({"ok": True})
            if r == "/api/freeall":
                with self.control.lock:
                    c.free_all()
                return self._json({"ok": True})
        except KeyError as exc:
            return self._json({"error": f"missing field {exc}"}, 400)
        except Exception as exc:                       # noqa: BLE001 -- surface to the UI
            return self._json({"error": str(exc)}, 500)
        self.send_error(404)

    def _audio(self):
        q = self.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                try:
                    chunk = q.get(timeout=1.0)
                except queue.Empty:
                    if self.hub.proc.poll() is not None:
                        break           # producer died: end the stream cleanly
                    continue
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")               # terminal chunk
        except (BrokenPipeError, ConnectionResetError):  # cxt-ignore: exception_hiding -- narrow, intentional
            pass                                       # client navigated away
        finally:
            self.hub.unsubscribe(q)


def main():
    import argparse
    p = argparse.ArgumentParser(description="web UI + audio stream for a running scsynth")
    p.add_argument("--host", default="0.0.0.0", help="bind address (default all interfaces)")
    p.add_argument("--http-port", type=int, default=8099)
    p.add_argument("--sc-host", default=DEFAULT_HOST)
    p.add_argument("--sc-port", type=int, default=DEFAULT_PORT)
    p.add_argument("--source", default=None,
                   help="pulse source to capture (default: the active sink's monitor)")
    p.add_argument("--bitrate", default="128k")
    a = p.parse_args()

    source = a.source
    origin = "given by --source"
    if not source:
        source, origin = capture_source()
    if not source:
        print("could not determine a capture source; pass --source", file=sys.stderr)
        return 1

    hub = AudioHub(source, a.bitrate)
    hub.start()
    pcm = PCMHub(source)
    pcm.start()
    time.sleep(0.3)
    if pcm.proc.poll() is not None or hub.proc.poll() is not None:
        print(f"ffmpeg failed to capture {source}", file=sys.stderr)
        return 1

    Handler.hub = hub
    Handler.pcm = pcm
    Handler.control = Control(a.sc_host, a.sc_port)
    Handler.control.start()   # background state cache -- /api/state never blocks on OSC

    print(f"capturing {source}  [{origin}]")
    print(f"  realtime -> ws://{a.host}:{a.http_port}/ws   (raw f32 PCM)")
    print(f"  fallback -> http://{a.host}:{a.http_port}/audio (MP3)")
    print(f"  control  -> http://{a.host}:{a.http_port}/api/state")
    print(f"  scsynth  -> udp/{a.sc_port}")
    srv = ThreadingHTTPServer((a.host, a.http_port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:  # cxt-ignore: exception_hiding -- narrow, intentional
        pass
    finally:
        hub.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())