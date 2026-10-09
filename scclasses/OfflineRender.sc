// Created: 2026-09-15
// Purpose: Let BBCut constructors run with NO booted server, so a patch can be
//          rendered offline (NRT) instead of played in realtime.
// Note: loaded via sclang_conf.yaml includePaths. This intentionally overwrites
//       Server:serverRunning -- only enable OfflineRender.forceServer inside a
//       script that never talks to a real server.

OfflineRender {
    classvar <>forceServer = false;
}

+ Server {
    serverRunning {
        if(OfflineRender.forceServer) { ^true };
        ^this.statusWatcher.serverRunning
    }
}
