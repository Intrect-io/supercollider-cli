// Created: 2026-09-15
// Purpose: Resolve the two unknowns for the BBCut offline (NRT) bridge:
//          (1) can Server.default be made to report serverRunning without booting?
//          (2) can a BBCutBuffer be built with no server at all?
// Dependencies: sclang build tree, BBCut classes
// Test Status: diagnostic

(
var sf, buf, sw, cap;

"--- 1. server status ---".postln;
sw = Server.default.statusWatcher;
"statusWatcher class: %".format(sw.class).postln;
"serverRunning: %".format(Server.default.serverRunning).postln;

// is serverRunning settable on the watcher?
try {
    sw.serverRunning = true;
    "SETTABLE: yes -> now %".format(Server.default.serverRunning).postln;
} {
    "SETTABLE: no".postln;
};

"--- 2. offline BBCutBuffer ---".postln;
try {
    sf = SoundFile.openRead((("BBCUT_DIR".getenv ?? { "~/dev/BBCut".standardizePath }) +/+ "sounds/break.aiff"));
    "soundfile: frames=% ch=% sr=%".format(sf.numFrames, sf.numChannels, sf.sampleRate).postln;
    buf = BBCutBuffer.newCopyArgs(Server.default, 0);
    buf.numFrames = sf.numFrames;
    buf.numChannels = sf.numChannels;
    buf.sampleRate = sf.sampleRate;
    sf.close;
    buf.beatlength_(4);
    buf.events_(nil);
    "buffer: length=% bps=% events=%".format(buf.length, buf.bps, buf.eventstarts.size).postln;
    "eventstarts(first 8): %".format(buf.eventstarts.copyToEnd(0).copyRange(0, 7) ? buf.eventstarts[0..7]).postln;
} { |err|
    "OFFLINE BUFFER FAILED: %".format(err.errorString).postln;
};

"--- 3. capture addr ---".postln;
try {
    cap = BundleNetAddr.copyFrom(Server.default.addr, []);
    Server.default.addr = cap;
    Server.default.sendMsg(\test, 1, 2);
    "captured: %".format(cap.bundle).postln;
} { |err|
    "CAPTURE FAILED: %".format(err.errorString).postln;
};

0.exit;
)
