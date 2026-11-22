(() => {
    'use strict';
    // The VPS compositor is a read-only subscriber. It never opens a local microphone or camera.
    const byId = id => document.getElementById(id);
    const canvas = byId('film'), paper = byId('board-paper'), ink = byId('board-ink'), sinks = byId('media-sinks');
    const frame = window.ArchiveFrame(canvas), lk = window.LivekitClient, fps = 12;
    const board = window.ClassroomBoard(byId('capture-board'), {
        rasterOnly: true, canEdit: () => false, identity: () => null, authorName: () => '', notice: () => {},
    });
    const tracks = new Map(), cameras = new Map();
    const supported = () => MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus') && !!canvas.captureStream && !!lk && !!window.AudioContext;
    let fonts;
    function loadFonts() {
        // Canvas pixels are final: do not start painting while a fallback font is pending.
        return fonts ??= Promise.all(['ArchiveVazir','ClassroomPDFPersian'].flatMap(family => [400,700].map(async weight => {
            const loaded = await document.fonts.load(`${weight} 16px ${family}`, 'گچ‌پژکی');
            if (!loaded.some(face => face.family === family && face.status === 'loaded')) throw new Error('bundled Persian font unavailable');
        })));
    }
    let heading, assignedTeacher, archive;
    let room, audio, mix, stream, recorder, frameTimer, boardTimer, stopping, ended, roomScope, leaseUntil = 0;
    let running = false, failure = false, pending = 0, writes = Promise.resolve(), doc = null;

    function gainFor(item) {
        const muted = !!item.pub.isMuted;
        if (item.muted !== muted) {
            item.gain.gain.value = muted ? 0 : 1;
            item.muted = muted;
        }
    }
    function attach(track, pub, participant) {
        const id = participant.identity, key = pub.trackSid;
        if (stopping || !/^(student|teacher|admin)-[1-9]\d{0,17}$/.test(id) || tracks.has(key)) return;
        const {Camera, ScreenShare, Microphone} = lk.Track.Source;
        const source = pub.source, microphone = source === Microphone && track.kind === 'audio';
        if (!microphone && !(track.kind === 'video' && [Camera,ScreenShare].includes(source))) return;
        const node = document.createElement(microphone ? 'audio' : 'video');
        node.autoplay = node.muted = true; node.playsInline = true; node.volume = 0;
        sinks.append(node);
        const item = {id,sid:participant.sid,source,track,pub,node};
        tracks.set(key,item);
        try {
            track.attach(node);
            // LiveKit's audio attach unsets `muted`; keep the sink silent outside of our recording bus.
            node.muted = true;
            if (microphone) {
                // A bare createMediaStreamSource on a remote WebRTC track can return only zeroes
                // in Chromium, even while RTP arrives. Play an attached (muted) element to start
                // the remote audio renderer, then mix its *track*, not HTML element playback.
                item.input = audio.createMediaStreamSource(node.srcObject);
                item.gain = audio.createGain();
                gainFor(item);
                item.input.connect(item.gain).connect(mix);
            } else if (source === Camera) cameras.set(id,item);
            node.play().catch(() => {
                if (microphone && tracks.get(key) === item && !stopping) fault();
            });
        } catch (_) { fault(); }
    }
    function detach(key) {
        const item = tracks.get(key);
        if (!item) return;
        tracks.delete(key);
        // Keep the slot while this person is in the room, including camera off/re-publish.
        if (cameras.get(item.id) === item) cameras.set(item.id,null);
        item.input?.disconnect(); item.gain?.disconnect();
        item.track.detach(item.node);
        item.node.pause(); item.node.srcObject = null; item.node.remove();
    }
    function forget(participant) {
        const {identity,sid} = participant;
        for (const [key,item] of tracks) if (item.id === identity && item.sid === sid) detach(key);
        if (!cameras.get(identity)) cameras.delete(identity);
    }
    function videoOf(item) {
        const media = item?.track.mediaStreamTrack;
        return media?.readyState === 'live' && !media.muted && !item.pub.isMuted && item.pub.isEnabled !== false
            && item.node.readyState >= 2 && item.node.videoWidth > 0 && item.node.videoHeight > 0 ? item.node : null;
    }
    function paint() {
        const owner = archive?.screenOwner;
        let screen = null, preferScreen = false;
        for (const item of tracks.values()) {
            if (item.gain) gainFor(item);
            if (item.source !== lk.Track.Source.ScreenShare || preferScreen) continue;
            // Older sites omit the lease metadata. On new sites, ignore revoked or replaced publishers
            // even while a previously subscribed track is still waiting to be removed by signaling.
            if (owner !== undefined && (owner?.identity !== item.id || owner.sid !== item.sid)) continue;
            const video = videoOf(item);
            if (!video) continue;
            screen = video;
            preferScreen = archive?.screen === item.pub.trackSid;
        }
        const teacher = videoOf(cameras.get(assignedTeacher));
        const gallery = Array.from(cameras, ([id,item]) => ({role:id.split('-',1)[0],video:videoOf(item)}));
        const rank = {teacher:0,admin:1,student:2};
        gallery.sort((a,b) => rank[a.role] - rank[b.role]);
        frame({paper,ink,doc,view:board.framing(),hasInk:board.position().ink > 0,laser:board.laser(),
            cameras:gallery,screen,teacher,archiveBoard:archive?.board === true,preferScreen,heading});
    }
    async function updateBoard() {
        let delay = 850;
        try {
            const pos = board.position(), requestedAt = performance.now();
            const res = await fetch('/board', {method:'POST', cache:'no-store', redirect:'error', signal:AbortSignal.timeout(2400),
                headers:{'Content-Type':'application/json'}, body:JSON.stringify({...pos,compact:true})});
            if (!res.ok || !running) return;
            const {board: view, archive: prefs} = await res.json();
            if (!view || !running) return;
            archive = prefs;
            board.sync(view,roomScope,requestedAt);
            if ('doc' in view) doc = view.doc;
            const freshLaser = (board.laser().at(-1)?.[2] ?? 0) - performance.now() > 1000;
            if (view.more) delay = 90;
            else if (view.moving || view.viewMoving || freshLaser) delay = 280;
        } catch (_) { /* A stale fetch while closing does not keep the recorder alive. */ }
        finally { if (running) boardTimer = setTimeout(updateBoard, delay); }
    }
    function bytes64(buffer) {
        const bytes = new Uint8Array(buffer);
        let text = '';
        for (let i=0;i<bytes.length;i+=8192) text += String.fromCharCode(...bytes.subarray(i,i+8192));
        return btoa(text);
    }
    function finish() {
        if (stopping) return stopping;
        running = false;
        clearInterval(frameTimer); clearTimeout(boardTimer);
        stopping = (async () => {
            if (recorder?.state !== 'inactive' && recorder) recorder.stop();
            await ended;
            await writes;
            stream?.getTracks().forEach(track => track.stop());
            for (const key of tracks.keys()) detach(key);
            cameras.clear();
            await room?.disconnect();
            await audio?.close();
            return !failure;
        })();
        return stopping;
    }
    function fault() { failure = true; queueMicrotask(() => finish().catch(() => {})); }

    window.recorder = {
        supported,
        async ready() {
            if (!supported()) return false;
            await loadFonts();
            return true;
        },
        async start({url,token,lesson,epoch,leaseMs,title,teacher,archive:prefs}) {
            leaseUntil = performance.now() + leaseMs;
            heading = title; assignedTeacher = teacher; archive = prefs;
            // Render Web Audio without relying on a sound device on the VPS.
            audio = new AudioContext({sampleRate:48000,sinkId:{type:'none'}});
            await Promise.all([audio.resume(), loadFonts()]);
            if (audio.state !== 'running') throw new Error('recording audio context unavailable');
            const bus = audio.createMediaStreamDestination(); bus.channelCount = 1;
            mix = audio.createDynamicsCompressor();
            mix.threshold.value = -18; mix.knee.value = 20; mix.ratio.value = 4;
            mix.attack.value = .006; mix.release.value = .24;
            mix.connect(bus);
            roomScope = {lesson,epoch};
            board.reset();
            room = new lk.Room({adaptiveStream:false, dynacast:false, webAudioMix:false, disconnectOnPageLeave:false});
            room.on(lk.RoomEvent.TrackSubscribed,attach).on(lk.RoomEvent.TrackUnsubscribed,(_,pub) => detach(pub.trackSid));
            room.on(lk.RoomEvent.ParticipantDisconnected,forget);
            const setMute = pub => { const item = tracks.get(pub.trackSid); if (item?.gain) gainFor(item); };
            room.on(lk.RoomEvent.TrackMuted,setMute).on(lk.RoomEvent.TrackUnmuted,setMute);
            room.on(lk.RoomEvent.Disconnected, () => { if (running) finish().catch(fault); });
            try { await room.connect(url,token,{autoSubscribe:true,websocketTimeout:4500,peerConnectionTimeout:4500}); }
            catch (_) { await finish(); return false; } // Including a close during the handshake.
            if (stopping || room.state !== 'connected' || performance.now() >= leaseUntil) { await finish(); return false; }
            paint();
            const video = canvas.captureStream(fps);
            stream = new MediaStream([...video.getVideoTracks(),...bus.stream.getAudioTracks()]);
            recorder = new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8,opus',videoBitsPerSecond:1250000,audioBitsPerSecond:64000});
            ended = new Promise(resolve => recorder.addEventListener('stop',resolve,{once:true}));
            recorder.onerror = fault;
            recorder.ondataavailable = ({data}) => {
                if (!data.size || failure) return;
                if (++pending > 8 || data.size > 1024*1024) { fault(); return; }
                writes = writes.then(async () => {
                    if (!failure) await window.saveRecordingChunk(bytes64(await data.arrayBuffer()));
                }).catch(fault).finally(() => pending--);
            };
            running = true;
            recorder.start(1000);
            frameTimer = setInterval(() => {
                if (performance.now() >= leaseUntil) { finish().catch(fault); return; }
                paint();
            },1000/fps);
            updateBoard();
            return true;
        },
        state: () => ({running,failure}),
        renew(leaseMs) { if (running) leaseUntil = performance.now() + leaseMs; },
        finish,
    };
})();
