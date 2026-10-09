// Created: 2026-09-15
// Purpose: Send a test tone to an ALREADY-RUNNING realtime scsynth over OSC,
//          to verify the headless realtime path end to end.
// Dependencies: realtime scsynth on 127.0.0.1:57210 (started via pipewire-jack)
// Test Status: diagnostic

// Explicit /d_recv + /s_new rather than Synth(...): a Server object created here
// is not the default, so Synth()/SynthDef.add() would target the wrong server
// (or none). Sending the messages directly leaves no ambiguity.

(
var s, tone;

s = Server(\pwtest, NetAddr("127.0.0.1", 57210));
s.options.numOutputBusChannels = 2;

tone = SynthDef(\pwtone, { |freq = 440, amp = 0.25|
    var env;
    env = EnvGen.kr(Env.perc(0.01, 2.5), doneAction: 2);
    Out.ar(0, SinOsc.ar(freq, 0, amp) * env ! 2);
});

s.sendMsg(\d_recv, tone.asBytes);

SystemClock.sched(0.7, { s.sendMsg(\s_new, \pwtone, 2001, 0, 0, \freq, 440.0); "TONE_440".postln; });
SystemClock.sched(1.2, { s.sendMsg(\s_new, \pwtone, 2002, 0, 0, \freq, 660.0); "TONE_660".postln; });
SystemClock.sched(4.5, { "DONE".postln; 0.exit; });
)
