#!/usr/bin/env python3
# Created: 2026-09-15
# Purpose: OSC client for scsynth -- shared by sccli (CLI) and scweb (web UI).
# Dependencies: none (stdlib only)
# Test Status: verified 2026-09-15 against a realtime scsynth
#
# Protocol notes that cost real debugging time:
#   * OSC strings are NUL-terminated AND padded to 4 bytes. The terminator is not
#     implied by the padding -- an address whose length is already a multiple of 4
#     still needs one full NUL word. Omitting it makes the server read the address
#     merged with the type tags and answer "Command not found".
#   * /status.reply and /g_queryTree.reply both start with a flag argument.
#   * Node ids are chosen by the client; the server never assigns them.

import os
import socket
import struct
import time

DEFAULT_HOST = os.environ.get("SC_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SC_PORT", "57210"))


# --------------------------------------------------------------------------
# OSC encoding
# --------------------------------------------------------------------------

def _pad4(b):
    """Pad raw bytes to a 4-byte boundary (for blobs, which are not terminated)."""
    return b + b"\x00" * (-len(b) % 4)


def _osc_string(s):
    """OSC string: NUL-terminated, then padded to a 4-byte boundary."""
    b = s.encode("utf-8") + b"\x00"
    return b + b"\x00" * (-len(b) % 4)


def _encode_arg(v):
    """Return (typetag, packed_bytes) for one argument."""
    if isinstance(v, bool):
        raise TypeError("bool is ambiguous in OSC; pass 0/1")
    if isinstance(v, int):
        return "i", struct.pack(">i", v)
    if isinstance(v, float):
        return "f", struct.pack(">f", v)
    if isinstance(v, str):
        return "s", _osc_string(v)
    if isinstance(v, bytes):
        return "b", struct.pack(">i", len(v)) + _pad4(v)
    raise TypeError(f"unsupported OSC argument type: {type(v).__name__}")


def encode(address, args):
    if isinstance(address, int):
        # Integer command form: the "address" is a bare big-endian int32 command
        # number (see include/server/SC_OSC_Commands.h). Resolved via gCmdArray.
        head = struct.pack(">i", address)
    else:
        head = _osc_string(address)
    tags, body = ",", b""
    for a in args:
        t, packed = _encode_arg(a)
        tags += t
        body += packed
    # The type tag string is a string too: it needs its own NUL terminator. Using
    # plain padding here silently drops it whenever len(tags) % 4 == 0 (e.g. ",isi"),
    # which shifts every argument by one and the server reads a value as the node id.
    return head + _osc_string(tags) + body


# --------------------------------------------------------------------------
# OSC decoding (for scsynth replies)
# --------------------------------------------------------------------------

def _read_padded_string(buf, off):
    """OSC string: NUL-terminated, padded to 4 bytes; returns (str, next_off)."""
    end = buf.index(b"\x00", off)
    s = buf[off:end].decode("utf-8", "replace")
    nxt = end + 1
    nxt += (-nxt) % 4
    return s, nxt


def decode(data):
    """Decode one OSC message -> (address, [args]). Raises ValueError otherwise."""
    addr, off = _read_padded_string(data, 0)
    if not addr.startswith("/"):
        raise ValueError("not an OSC message")
    tagstr, off = _read_padded_string(data, off)
    if not tagstr.startswith(","):
        raise ValueError("bad type tag string")
    args = []
    for t in tagstr[1:]:
        if t == "i":
            args.append(struct.unpack_from(">i", data, off)[0]); off += 4
        elif t == "f":
            args.append(struct.unpack_from(">f", data, off)[0]); off += 4
        elif t == "d":
            args.append(struct.unpack_from(">d", data, off)[0]); off += 8
        elif t in ("s", "S"):
            s, off = _read_padded_string(data, off)
            args.append(s)
        elif t == "b":
            n = struct.unpack_from(">i", data, off)[0]; off += 4
            args.append(data[off:off + n]); off += n + (-n % 4)
        elif t in "TFNI":
            args.append({"T": True, "F": False, "N": None, "I": "impulse"}[t])
        else:
            raise ValueError(f"unsupported type tag {t!r}")
    return addr, args


def parse_tree(args, i, controls):
    """Parse /g_queryTree.reply args (recursive). Returns (node, next_index).

    Call with i=1: args[0] of the reply is the controls flag.
    """
    node_id, nchildren = args[i], args[i + 1]
    i += 2
    if nchildren < 0:                    # synth
        name = args[i]; i += 1
        ctl = {}
        if controls:
            n = args[i]; i += 1
            for _ in range(n):
                ctl[args[i]] = args[i + 1]; i += 2
        return {"type": "synth", "id": node_id, "def": name, "controls": ctl}, i
    children = []
    for _ in range(nchildren):
        child, i = parse_tree(args, i, controls)
        children.append(child)
    return {"type": "group", "id": node_id, "children": children}, i


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

class SCClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=2.0):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))          # ephemeral port: scsynth replies land here
        self.sock.settimeout(timeout)
        self._next_id = 1

    def send(self, address, *args):
        self.sock.sendto(encode(address, list(args)), self.addr)

    def request(self, address, *args, window=0.6, want=None):
        """Send a message and collect replies.

        `want` is the reply address we are actually waiting for: as soon as it
        arrives (typically <5 ms for a live server) we return, instead of paying
        the full `window` as a drain delay. The window is only a timeout fallback.
        """
        self.send(address, *args)
        replies, deadline = [], time.monotonic() + window
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.sock.settimeout(min(remaining, 0.05))
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            try:
                addr, args2 = decode(data)
            except ValueError:
                continue
            if want is not None and addr != want:
                continue                      # stale datagram from an earlier op
            replies.append((addr, args2))
            if want is not None:
                break
        return replies

    def sync(self, timeout=2.0):
        """Block until the server has processed everything sent so far."""
        sid = self._next_id
        self._next_id += 1
        self.send("/sync", sid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                return False
            try:
                addr, args = decode(data)
            except ValueError:
                continue
            if addr == "/synced" and args and args[0] == sid:
                return True
        return False

    # ---- queries -----------------------------------------------------------

    def status(self):
        for addr, args in self.request("/status", window=0.6, want="/status.reply"):
            # args[0] is the unused leading flag
            keys = ["numUGens", "numSynths", "numGroups", "numSynthDefs",
                    "avgCPU", "peakCPU", "sampleRate", "actualSampleRate"]
            return dict(zip(keys, args[1:]))
        return None

    def query_tree(self, group=0, controls=0):
        for addr, args in self.request("/g_queryTree", group, controls,
                                       window=0.6, want="/g_queryTree.reply"):
            try:
                node, _ = parse_tree(args, 1, bool(args[0]))
                return node
            except (IndexError, ValueError, struct.error):
                return None
        return None

    def used_node_ids(self):
        used = set()

        def walk(n):
            used.add(n["id"])
            for child in n.get("children", []):
                walk(child)

        tree = self.query_tree(0, 0)
        if tree:
            walk(tree)
        return used

    def free_node_id(self, base=1000):
        """Pick an unused node id.

        Each CLI invocation is a fresh process, so a local counter would reuse ids
        across calls and collide with live nodes. Ask the server instead.
        """
        used = self.used_node_ids()
        node_id = base
        while node_id in used:
            node_id += 1
        return node_id

    # ---- mutations ---------------------------------------------------------

    def play(self, defname, params=(), node_id=None, target=0, action=0):
        """Create a synth. Returns the node id used."""
        if node_id is None:
            node_id = self.free_node_id()
        flat = []
        for k, v in dict(params).items():
            flat.extend([k, v])
        self.send("/s_new", defname, node_id, action, target, *flat)
        self.sync()
        return node_id

    def set_controls(self, node_id, params):
        flat = []
        for k, v in dict(params).items():
            flat.extend([k, v])
        self.send("/n_set", node_id, *flat)
        self.sync()

    def free(self, node_id):
        self.send("/n_free", node_id)
        self.sync()

    def free_all(self):
        self.send("/g_freeAll", 0)
        self.sync()