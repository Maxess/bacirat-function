(() => {
    'use strict';
    const lk = window.LivekitClient, E = lk.RoomEvent, S = lk.Track.Source;
    const tracks = new Map(), byId = new Map();
    let room, worker, flushWorker, lease, started, timer, finishedAt, seq = 0, pending = 0;
    let writing = Promise.resolve(), stopping, running = false, failure = false;
    const now = () => Math.max(0, Math.round(performance.now() - started));
    function save(events) {
        if (!events.length || failure) return;
        if (++pending > 10) { fault(); return; }
        writing = writing.then(() => window.saveArchiveEvents(events)).catch(fault).finally(() => pending--);
    }
    const eligible = (pub, p) => /^(student|teacher|admin)-[1-9]\d{0,17}$/.test(p.identity)
        && [S.Microphone, S.Camera, S.ScreenShare].includes(pub.source);
    function subscribe(pub, p) { if (eligible(pub, p)) pub.setSubscribed(true); }
    function attach(track, pub, p) {
        if (stopping || !eligible(pub, p) || tracks.get(pub.trackSid)?.track === track) return;
        detach(pub);
        const video = pub.source !== S.Microphone;
        if ((track.kind === 'video') !== video || !track.receiver) { fault(); return; }
        const item = {id:++seq, video, pub, track, muted:!!pub.isMuted, needKey:video, last:null, since:now()};
        tracks.set(pub.trackSid,item); byId.set(item.id,item);
        save([['open',item.id,item.since,pub.source,p.identity,p.sid,pub.trackSid,item.muted]]);
        try {
            track.receiver.transform = new RTCRtpScriptTransform(worker,{id:item.id,video,origin:performance.timeOrigin,
                mime:pub.mimeType || (video ? '' : 'audio/opus')});
        } catch (_) { fault(); }
    }
    function detach(pub) {
        const item = tracks.get(pub.trackSid);
        if (!item) return;
        tracks.delete(pub.trackSid); byId.delete(item.id);
        worker.postMessage({drop:item.id});
        save([['end',item.id,now()]]);
    }
    function mute(pub) {
        const item = tracks.get(pub.trackSid);
        if (!item || item.muted === !!pub.isMuted) return;
        item.muted = !!pub.isMuted;
        item.since = now();
        item.last = null;
        if (item.video && !item.muted) {
            item.needKey = true;
            worker.postMessage({key:item.id});
        }
        save([['mute',item.id,item.since,item.muted]]);
    }
    function packets({data}) {
        if (data === 'fault') { fault(); return; }
        if (data === 'flushed') { flushWorker?.(); return; }
        const events = [];
        for (const [id,received,codec,ts,key,raw] of data) {
            const item = byId.get(id), at = Math.max(0,received - started);
            if (!item || item.muted || at < item.since - 2) continue;
            const codecs = item.video ? ['video/vp8','video/h264'] : ['audio/opus'];
            if (!codecs.includes(codec)) { fault(); return; }
            if (item.needKey && !key) continue;
            // The transform can receive retransmitted keyframes or overlapping packets.
            if (item.last !== null && ((ts-item.last)|0) <= 0) continue;
            item.last = ts; item.needKey = false;
            events.push(['media',id,Math.round(Math.max(item.since,at)),codec,ts,key,new Uint8Array(raw).toBase64()]);
        }
        save(events);
    }
    function fault() { failure = true; queueMicrotask(() => finish().catch(() => {})); }
    function finish() {
        return stopping ??= (async () => {
            running = false; finishedAt = now(); clearInterval(timer);
            if (worker && !failure) {
                // Drain encoded frames already received before tearing down SDK subscriptions.
                await new Promise(resolve => { const timeout = setTimeout(() => { failure = true; resolve(); },1000);
                    flushWorker = () => { clearTimeout(timeout); resolve(); }; worker.postMessage('flush'); });
            }
            worker?.terminate();
            await writing;
            await room?.disconnect();
            return !failure;
        })();
    }
    const ready = () => typeof RTCRtpScriptTransform === 'function' && typeof Uint8Array.prototype.toBase64 === 'function';
    window.ingest = {
        ready,
        async start({url,token,leaseMs}) {
            if (!ready()) throw new Error('encoded receiver unavailable');
            const boot = performance.now(); lease = boot + leaseMs; started = Math.floor(boot);
            worker = new Worker('/encoded.js'); worker.onmessage = packets; worker.onerror = fault;
            room = new lk.Room({adaptiveStream:false,dynacast:false,webAudioMix:false,disconnectOnPageLeave:false});
            room.on(E.TrackPublished,subscribe).on(E.TrackSubscribed,attach)
                .on(E.TrackUnsubscribed,(_,pub) => { if (!stopping) detach(pub); })
                .on(E.TrackUnpublished,pub => { if (!stopping) detach(pub); })
                .on(E.TrackMuted,mute).on(E.TrackUnmuted,mute)
                .on(E.Disconnected,() => { if (running) finish().catch(fault); });
            // The capture lease is enforced in the browser too, even if the Python loop stalls.
            timer = setInterval(() => { if (performance.now() >= lease) finish().catch(fault); },150);
            try { await room.connect(url,token,{autoSubscribe:false,websocketTimeout:4500,peerConnectionTimeout:4500}); }
            catch (_) { await finish(); return false; }
            if (stopping || room.state !== 'connected' || performance.now() >= lease) { await finish(); return false; }
            for (const p of room.remoteParticipants.values()) for (const pub of p.trackPublications.values()) subscribe(pub,p);
            running = true;
            return true;
        },
        renew(leaseMs) { if (running) lease = performance.now() + leaseMs; },
        state: () => ({running,failure,ms:Math.min(finishedAt ?? now(),Math.max(0,Math.round(lease-started)))}),
        finish,
    };
})();
