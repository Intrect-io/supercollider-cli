// Created: 2026-09-15
// Purpose: Dump an OSC score file for manual scsynth -N diagnosis.
// Dependencies: sclang build tree
// Test Status: diagnostic

(
var score, tone;

tone = SynthDef(\smokeTone, { | out = 0, freq = 440, amp = 0.2 |
    var env, sig;
    env = EnvGen.kr(Env.perc(0.01, 2.8), doneAction: 2);
    sig = SinOsc.ar(freq, 0, amp) + SinOsc.ar(freq * 1.005, 0, amp);
    Out.ar(out, sig * env);
}); // SynthDef.new already calls build internally

"tone built, bytes=%".format(tone.asBytes.size).postln;

score = Score.new;
// NESTED message form: [time, [cmd, args...]] -- see smoke patch header note
score.add([0.0, ['/d_recv', tone.asBytes]]);
score.add([0.1, ['/s_new', \smokeTone, 1001, 0, 0, 'freq', 440.0]]);

score.writeOSCFile("/tmp/test_osc.osc", 0, 4.0);
"OSC_WRITTEN size=%".format(File("/tmp/test_osc.osc").length).postln;
0.exit;
)
