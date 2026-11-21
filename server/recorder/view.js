(() => {
    'use strict';
    // This read-only view is used by the VPS's managed browser, not by an open teacher tab.
    // Only these canvases and LiveKit tracks ever reach the encoder.
    const canvas = document.getElementById('film'), paper = document.getElementById('board-paper'), ink = document.getElementById('board-ink');
    const ctx = canvas.getContext('2d', {alpha:false}), lk = window.LivekitClient;
    const board = window.ClassroomBoard(document.getElementById('capture-board'), {
        autoReceive: true, canEdit: () => false, identity: () => null, authorName: () => '', notice: () => {},
    });
    const tracks = new Map();
    let room, audio, mix, stream, recorder, frameTimer, boardTimer, stopping, ended, roomScope, leaseUntil = 0;
    let running = false, failure = false, pending = 0, writes = Promise.resolve(), hasBoard = false;
    const width = canvas.width, height = canvas.height;

    function attach(track, pub, participant) {
        if (!/^(student|teacher|admin)-[1-9]\d{0,17}$/.test(participant.identity) || tracks.has(pub.trackSid)) return;
        const source = pub.source;
        if (source === lk.Track.Source.Microphone && track.kind === 'audio') {
            const input = audio.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
            const gain = audio.createGain();
            input.connect(gain).connect(mix);
            tracks.set(pub.trackSid, {input,gain,pub});
        } else if ([lk.Track.Source.Camera, lk.Track.Source.ScreenShare].includes(source) && track.kind === 'video') {
            const video = document.createElement('video');
            video.muted = true; video.playsInline = true; video.autoplay = true;
            track.attach(video);
            video.play().catch(() => {});
            tracks.set(pub.trackSid, {track,video,pub,screen:source === lk.Track.Source.ScreenShare});
        }
    }
    function detach(key) {
        const item = tracks.get(key);
        if (!item) return;
        if (item.video) item.track.detach(item.video);
        else { item.input.disconnect(); item.gain.disconnect(); }
        tracks.delete(key);
    }
    function fit(item, sw, sh, x, y, w, h) {
        if (!sw || !sh) return null;
        const scale = Math.min(w/sw,h/sh), ww = sw*scale, hh = sh*scale;
        const rect = [x+(w-ww)/2,y+(h-hh)/2,ww,hh];
        ctx.drawImage(item,...rect);
        return rect;
    }
    function paint() {
        ctx.fillStyle = '#162035'; ctx.fillRect(0,0,width,height);
        const cameras = [], screens = [];
        for (const item of tracks.values()) {
            if (item.gain) item.gain.gain.value = item.pub.isMuted ? 0 : 1;
            if (item.pub.isMuted || !item.video?.videoWidth || item.video.readyState < 2) continue;
            (item.screen ? screens : cameras).push(item.video);
        }
        const gap = 4, row = cameras.length ? Math.min(150, Math.ceil(Math.sqrt(cameras.length/8))*88) : 0;
        let main = height-row;
        if (!hasBoard && !screens.length && cameras.length) main = 0;
        const half = screens.length ? (hasBoard ? width/2 : 0) : width;
        if (half && main) {
            const rect = fit(paper,paper.width,paper.height,gap,gap,half-2*gap,main-2*gap);
            if (rect) ctx.drawImage(ink,...rect);
        }
        if (screens.length && main) {
            screens.forEach((video,i) => {
                const w = (width-half)/screens.length;
                fit(video,video.videoWidth,video.videoHeight,half+i*w+gap,gap,w-2*gap,main-2*gap);
            });
        }
        if (cameras.length) {
            const columns = main ? Math.min(10,cameras.length) : Math.min(10,Math.ceil(Math.sqrt(cameras.length*width/height)));
            const rows = Math.ceil(cameras.length/columns), w = width/columns, h = (height-main)/rows;
            cameras.forEach((video,i) => fit(video,video.videoWidth,video.videoHeight,
                (i%columns)*w+gap,main+Math.floor(i/columns)*h+gap,w-2*gap,h-2*gap));
        }
        ctx.fillStyle = '#ff8e9b'; ctx.beginPath(); ctx.arc(14,14,4,0,Math.PI*2); ctx.fill();
    }
    async function updateBoard() {
        let more = false;
        try {
            const pos = board.position();
            const res = await fetch('/board', {method:'POST', cache:'no-store', redirect:'error', signal:AbortSignal.timeout(2400),
                headers:{'Content-Type':'application/json'}, body:JSON.stringify({...pos,compact:true})});
            if (!res.ok || !running) return;
            const {board: view} = await res.json();
            if (!view || !running) return;
            board.sync(view,roomScope);
            hasBoard = !!view.doc || board.position().ink > 0 || (!('doc' in view) && hasBoard);
            more = view.more === true;
        } catch (_) { /* A stale fetch while closing does not keep the recorder alive. */ }
        finally { if (running) boardTimer = setTimeout(updateBoard, more ? 90 : 850); }
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
            await room?.disconnect();
            for (const key of tracks.keys()) detach(key);
            await audio?.close();
            return !failure;
        })();
        return stopping;
    }
    function fault() { failure = true; queueMicrotask(() => finish().catch(() => {})); }

    window.recorder = {
        supported: () => MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus') && !!canvas.captureStream && !!lk,
        async start({url,token,lesson,epoch,leaseMs}) {
            leaseUntil = performance.now() + leaseMs;
            audio = new AudioContext({sampleRate:48000});
            await audio.resume();
            const bus = audio.createMediaStreamDestination();
            mix = audio.createDynamicsCompressor(); mix.connect(bus);
            roomScope = {lesson,epoch};
            board.reset();
            room = new lk.Room({adaptiveStream:false, dynacast:false, disconnectOnPageLeave:false});
            room.on(lk.RoomEvent.TrackSubscribed,attach).on(lk.RoomEvent.TrackUnsubscribed,(_,pub) => detach(pub.trackSid));
            room.on(lk.RoomEvent.Disconnected, () => { if (running) finish().catch(fault); });
            try { await room.connect(url,token,{autoSubscribe:true,websocketTimeout:4500,peerConnectionTimeout:4500}); }
            catch (_) { await finish(); return false; } // Including the teacher closing during the handshake.
            if (room.state !== 'connected' || performance.now() >= leaseUntil) { await finish(); return false; }
            paint();
            const video = canvas.captureStream(10);
            stream = new MediaStream([...video.getVideoTracks(),...bus.stream.getAudioTracks()]);
            recorder = new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8,opus',videoBitsPerSecond:520000,audioBitsPerSecond:48000});
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
            },100);
            updateBoard();
            return true;
        },
        state: () => ({running,failure}),
        renew(leaseMs) { if (running) leaseUntil = performance.now() + leaseMs; },
        finish,
    };
})();
