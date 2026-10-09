// Created: 2026-09-15
// Purpose: Render a BBCut break-cut patch to an OSC score file, fully offline.
//          No server, no audio device: BBCut's cut algorithm is driven by hand
//          and every OSC message it would send is collected into a Score.
// Dependencies: sclang build tree, BBCut classes, OfflineRender shim
// Test Status: pending (render + verify with scsynth -N)

// HOW THIS WORKS
//   BBCut is a realtime system: a clock calls provideMaterial() each beat and
//   the resulting events are sent to the server. None of that needs a server --
//   only the *sending* does. So:
//     1. build the BBCutBuffer client-side (Buffer's numFrames/sampleRate are
//        settable, so no /b_allocRead round-trip is required)
//     2. point Server.default.addr at a BundleNetAddr, which collects messages
//        instead of sending them
//     3. pull blocks from the cut procedure and render them by hand
//     4. BBCutBlock:cacheMsgs() tags every message with an absolute beat, so the
//        score keeps exact timing
//     5. write the score out and let scsynth -N render it to WAV

(
var breakPath, beatlength, totalBeats;
var sf, buf, capture, cg, proc, score, outOsc, defs;
var cache, t, tempo, blocks, maxBlocks;

breakPath = (("BBCUT_DIR".getenv ?? { "~/dev/BBCut".standardizePath }) +/+ "sounds/break.aiff");
beatlength = 4;      // cut the break into 4 beats
totalBeats = 24;     // render 6 bars
outOsc = "/tmp/bbcut_break.osc";

Score.program = ("SCSYNTH".getenv ?? { "~/dev/supercollider/build/server/scsynth/scsynth".standardizePath });
OfflineRender.forceServer = true;   // BBCut constructors require a "running" server
thisThread.randSeed = 20260915;     // BBCutProc11 is stochastic; fix the seed for a repeatable render

// ---- 1. the break, built with no server at all ----
sf = SoundFile.openRead(breakPath);
buf = BBCutBuffer.newCopyArgs(Server.default, 0);   // bufnum 0
buf.numFrames = sf.numFrames;
buf.numChannels = sf.numChannels;
buf.sampleRate = sf.sampleRate;
sf.close;
buf.beatlength_(beatlength);
buf.events_(nil);
tempo = buf.bps;   // natural tempo -> playback rate 1.0
"BUFFER len=% len(s)=% bps=% events=%".format(buf.length, buf.length.round(0.001), tempo.round(0.0001), buf.eventstarts.size).postln;

// ---- 2. capture instead of send ----
capture = BundleNetAddr.copyFrom(Server.default.addr, []);
Server.default.addr = capture;

// ---- 3. the cut synths' SynthDefs ----
// They must be written into the score by hand: SynthDef:send iterates
// Server.allBootedServers, which is empty here, so .add would silently send
// nothing. CutBuf1/CutMixer register these via StartUp at sclang startup, i.e.
// long before the capture addr existed, so their /d_recv never went anywhere.
defs = [
    SynthDef(\cb1playbuf1, { |bufnum = 0, outbus = 0, rate = 1, startPos = 0, trig = 1 |
        Out.ar(outbus, PlayBuf.ar(1, bufnum, BufRateScale.kr(bufnum) * rate, InTrig.kr(trig), startPos, 1));
    }),
    SynthDef(\cb1playbuf2, { |bufnum = 0, outbus = 0, rate = 1, startPos = 0, trig = 1 |
        Out.ar(outbus, PlayBuf.ar(2, bufnum, BufRateScale.kr(bufnum) * rate, InTrig.kr(trig), startPos, 1));
    }),
    SynthDef(\cutmixer1, { |outbus = 0, inbus = 0, amp = 1.0, pan = 0.0 |
        Out.ar(outbus, Pan2.ar(amp * In.ar(inbus, 1), pan));
    }),
    SynthDef(\cutmixer2, { |outbus = 0, inbus = 0, amp = 1.0, pan = 0.0 |
        Out.ar(outbus, amp * In.ar(inbus, 2));
    })
];

// ---- 4. cut group + cut procedure ----
cg = CutGroup(CutBuf1(buf));   // CutGroup auto-adds a CutMixer -> bus 0
proc = BBCutProc11.new;
"GROUP bus=% channels=% cutsynths=%".format(cg.index, cg.numChannels, cg.cutsynths.size).postln;
"SETUP captured=% msgs".format(capture.bundle.size).postln;

// ---- 5. drive the blocks by hand, exactly what the realtime clock does ----
cache = LinkedList.new;
t = 0.0;
blocks = 0;
maxBlocks = 4000;
while { (t < totalBeats) and: { blocks < maxBlocks } } {
    var block, len;
    block = proc.next;
    block.startbeat = t;
    block.scaleDurations(1.0 / tempo);   // cut durations: beats -> seconds
    block.update;
    cg.renderBlock(block, (tempo: tempo));
    cache = block.cacheMsgs(cache, Server.default);
    len = block.length;
    if(len < 0.001) { len = 0.25 };      // never advance by zero
    t = t + len;
    blocks = blocks + 1;
};
"DRIVE blocks=% events=% beats=%".format(blocks, cache.size, t.round(0.01)).postln;

// ---- 6. assemble the score ----
score = Score.new;
// the sample must exist before the playbuf synths are created
score.add([0.0, ['/b_allocRead', 0, breakPath, 0, -1]]);
// SynthDefs, straight in as /d_recv
defs.do { |d| score.add([0.0, ['/d_recv', d.asBytes]]) };
// setup messages captured from BBCut: /g_new, the playbuf + mixer /s_new, ...
// Node commands arrive with an INTEGER command code in the address slot
// (Node:creationCmd: 21 = /g_new, 63 = /p_new) because Server:sendMsg passes it
// straight through; the UDP primitive understands that, but array->OSC
// serialisation does not, so translate it back to an address here.
capture.bundle.do { |m|
    var msg = m.asArray;
    if(msg.size > 0) {
        if(msg[0] == 21) { msg = ['/g_new'] ++ msg.copyToEnd(1) };
        if(msg[0] == 63) { msg = ['/p_new'] ++ msg.copyToEnd(1) };
        if(msg[0].isKindOf(Symbol) or: { msg[0].isKindOf(String) }) {
            score.add([0.0, msg]);
        };
    };
};
// the cut events, at their absolute beat positions
cache.do { |entry|
    var when, msgs;
    when = entry[0] / tempo;             // absolute beat -> seconds
    msgs = entry[2][\msg];
    if(msgs.notNil) { msgs.do { |m| score.add([when, m.asArray]) } };
};

score.writeOSCFile(outOsc, 0, totalBeats / tempo);
"SCORE file=% bytes=%".format(outOsc, File(outOsc, "rb").length).postln;
0.exit;
)
