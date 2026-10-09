// Created: 2026-09-15
// Purpose: Probe whether Server.internal (in-process scsynth) boots with zero
//          audio channels -- i.e. headless, no JACK/PipeWire device needed.
// Dependencies: sclang build tree
// Test Status: diagnostic

(
var s, done = false;

s = Server.internal;
s.options.numOutputBusChannels = 0;
s.options.numInputBusChannels = 0;
s.options.zeroConf = false;

s.waitForBoot {
    done = true;
    "INTERNAL_BOOT_OK".postln;
    s.quit;
    0.exit;
};

// hard timeout so the probe can never hang the agent loop
SystemClock.sched(20.0, {
    if(done.not) {
        "INTERNAL_BOOT_TIMEOUT".postln;
        0.exit;
    };
});
)
