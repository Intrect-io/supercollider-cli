// Created: 2026-09-15
// Purpose: SynthDef source for sccli. `sccli defs compile defs/tone.scd` runs
//          this through sclang and writes .scsyndef files the server can load.
// Dependencies: sclang
// Test Status: verified 2026-09-15
//
// ~defsOutDir is set by the sccli wrapper; running this file directly writes to
// /tmp/scdefs instead.

(
var dir;

dir = ~defsOutDir ?? { "/tmp/scdefs" };

// Simple decaying sine -- the "hello world" of the control layer.
SynthDef(\tone, { |freq = 440, amp = 0.2, dur = 2, pan = 0 |
    var env, sig;
    env = EnvGen.kr(Env.perc(0.01, dur), doneAction: 2);
    sig = SinOsc.ar(freq, 0, amp) * env;
    Out.ar(0, Pan2.ar(sig, pan));
}).writeDefFile(dir);

// Sustained tone: no doneAction, so it stays until freed -- needed to exercise
// `sccli set` and `sccli free`.
SynthDef(\drone, { |freq = 220, amp = 0.1, pan = 0 |
    Out.ar(0, Pan2.ar(SinOsc.ar(freq, 0, amp), pan));
}).writeDefFile(dir);

("wrote tone, drone -> " ++ dir).postln;
)
