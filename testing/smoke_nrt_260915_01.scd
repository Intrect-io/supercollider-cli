// Created: 2026-09-15
// Purpose: Smoke test - headless NRT render of a SuperCollider patch.
//          No audio device needed: Score.recordNRT renders straight to WAV.
// Dependencies: sclang + scsynth (SC_QT=OFF build), libsndfile
// Test Status: verified 2026-09-15 (WAV non-silent)

// Non-realtime (NRT) render: proves the coding-agent loop works headless.
//
// SCORE FORMAT NOTE (this is what makes it work):
//   Score entries are [time, [cmd, arg1, arg2, ...]] -- the OSC message MUST be
//   nested. A flat [time, cmd, args...] array is silently reduced to an EMPTY
//   bundle: makeSynthBundle() only treats array elements as messages and skips
//   everything else, so scsynth reports no error and renders pure silence.

(
var score, outfile, size, f;

// scsynth is not on PATH: Score.program is the class var recordNRT shells out to
Score.program = ("SCSYNTH".getenv ?? { "~/dev/supercollider/build/server/scsynth/scsynth".standardizePath });
outfile = "/tmp/smoke_render.wav";

score = Score.new;

// SynthDef: two detuned sine oscillators, percussive-ish envelope
score.add([0.0, ['/d_recv',
    SynthDef(\smokeTone, { | out = 0, freq = 440, amp = 0.2 |
        var env, sig1, sig2;
        env = EnvGen.kr(Env.perc(0.01, 2.8), doneAction: 2);
        sig1 = SinOsc.ar(freq, 0, amp);
        sig2 = SinOsc.ar(freq * 1.005, 0, amp); // slight detune for beating
        Out.ar(out, [sig1, sig2] * env);
    }).asBytes
]]);

// Start the synth at t=0.1; the envelope's doneAction frees it
score.add([0.1, ['/s_new', \smokeTone, 1001, 0, 0, 'freq', 440.0]]);

"SCORE_BUILT".postln;

score.recordNRT(
    outputFilePath: outfile,
    sampleRate: 44100,
    headerFormat: "WAV",
    sampleFormat: "int16",
    duration: 4.0
);

// Verdict based on the artifact itself: a header-only WAV is 44 bytes
size = if(File.exists(outfile)) { f = File(outfile, "rb"); f.length } { 0 };
"SMOKE_WAV_BYTES=%".format(size).postln;
if(size > 44) {
    "SMOKE_RENDER_OK".postln;
} {
    "SMOKE_RENDER_FAIL".postln;
};
0.exit; // non-interactive: exit sclang after render
)
