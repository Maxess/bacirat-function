(() => {
    'use strict';
    // Virtual clock: composing the frame for time T does not wait for wall time T.
    // FFmpeg receives lossless PNG pixels + PCM; H.264/AAC is the only film encode.
    const el = id => document.getElementById(id);
    const canvas = el('film'), paper = el('board-paper'), ink = el('board-ink');
    const paint = window.ArchiveFrame(canvas);
    const frameSamples = 4000; // 48000 Hz / 12 fps
    const opus = {codec:'opus',sampleRate:48000,numberOfChannels:2};
    const videoCodecs = {'video/vp8':'vp8','video/h264':'avc1.42E01F'};
    const tracks = new Map(), audio = new Map(), queue = [], sound = [];
    let scope, heading, teacher, archive, doc, nativeClock, laser = [], hadLaser = false, dirty = true, failure, fonts;
    const board = window.ClassroomBoard(el('capture-board'), {
        rasterOnly:true,canEdit:() => false,identity:() => null,authorName:() => '',notice:() => { failure = 'archived board unavailable'; },
    });
    const configure = codec => ({codec,optimizeForLatency:true,hardwareAcceleration:'prefer-software'});
    function fail() { failure = 'archived media decode failed'; }
    function clearVideo(item) {
        if (item.decoder && item.decoder.state !== 'closed') item.decoder.close();
        item.decoder = null;
        item.video?.close(); item.video = null;
    }
    function decodeVideo(item, at, codec, key, encoded) {
        if (item.muted || (!item.decoder && !key)) return;
        if (item.codec !== codec || !item.decoder) {
            clearVideo(item);
            if (!key || !videoCodecs[codec]) throw new Error('invalid video configuration');
            item.codec = codec;
            item.decoder = new VideoDecoder({
                output(frame) {
                    item.video?.close(); item.video = frame;
                    dirty = true;
                    item.decoded?.();
                },
                error() { fail(); item.decoded?.(); },
            });
            item.decoder.configure(configure(videoCodecs[codec]));
        }
        const chunk = new EncodedVideoChunk({type:key ? 'key' : 'delta',timestamp:at*1000,data:Uint8Array.fromBase64(encoded)});
        return new Promise((resolve,reject) => {
            // Classroom publications use real-time, non-reordered pictures. Fail closed if a
            // malformed archive never produces a frame; don't silently send a blank camera.
            const timer = setTimeout(() => reject(new Error('video decoder did not produce a frame')),1800);
            item.decoded = () => { clearTimeout(timer); resolve(); };
            try { item.decoder.decode(chunk); }
            catch (error) { clearTimeout(timer); reject(error); }
        });
    }
    function onSound(frame) {
        try {
            const n = frame.numberOfFrames;
            if (frame.sampleRate !== 48000 || n > 5760 || sound.length >= 12000) { fail(); return; }
            const samples = new Float32Array(n), plane = new Float32Array(n);
            for (let ch=0; ch<frame.numberOfChannels; ch++) {
                frame.copyTo(plane,{planeIndex:ch,format:'f32-planar'});
                for (let i=0;i<n;i++) samples[i] += plane[i]/frame.numberOfChannels;
            }
            sound.push([Math.round(frame.timestamp*0.048),samples]);
        } finally { frame.close(); }
    }
    function decodeAudio(id, at, ts, encoded) {
        let item = audio.get(id);
        if (!item) {
            const decoder = new AudioDecoder({output:onSound,error:fail});
            decoder.configure(opus);
            audio.set(id,item = {decoder,ts,pos:at*48});
        } else {
            item.pos += (ts-item.ts)|0;
            // Preserve continuous Opus samples through arrival jitter. Native
            // sources carry a shared sender-report clock: slowly correct drift
            // without a single jump for each small correction.
            const drift = at*48-item.pos;
            if (Math.abs(drift) > (nativeClock ? 5760 : 12000)) item.pos = at*48;
            else if (nativeClock && Math.abs(drift) > 480) item.pos += Math.sign(drift)*3;
            item.ts = ts;
        }
        item.decoder.decode(new EncodedAudioChunk({type:'key',timestamp:Math.round(item.pos/0.048),data:Uint8Array.fromBase64(encoded)}));
        return item.decoder;
    }
    async function load(updates) {
        const pending = new Set(), ended = [];
        for (const update of updates) {
            const [type,id,at,codec,ts,,bytes] = update;
            if (type === 'media' && codec === 'audio/opus') pending.add(decodeAudio(id,at,ts,bytes));
            else queue.push(update);
            if (type === 'end') ended.push(id);
        }
        await Promise.all(Array.from(pending, decoder => decoder.flush()));
        for (const id of ended) {
            const item = audio.get(id);
            if (item?.decoder.state === 'configured') item.decoder.close();
            audio.delete(id);
        }
        queue.sort((a,b) => a[2]-b[2]);
    }
    async function apply([type,id,at,...args]) {
        if (type === 'board') {
            const [next,layout,requestedAt] = args;
            archive = layout;
            if ('doc' in next) doc = next.doc;
            laser = (next.laser?.points || []).map(([x,y,left]) => [x,y,requestedAt+left]);
            await board.sync({...next,laser:null},scope);
            dirty = true;
        } else if (type === 'open') {
            const [source,identity,sid,pub,muted] = args;
            if (source !== 'microphone') tracks.set(id,{source,identity,sid,pub,muted});
        } else if (type === 'mute' || type === 'end') {
            const item = tracks.get(id);
            if (item) {
                clearVideo(item);
                item.muted = args[0];
                if (type === 'end') tracks.delete(id);
                dirty = true;
            }
        } else if (type === 'media') {
            const item = tracks.get(id);
            if (item) await decodeVideo(item,at,args[0],args[2],args[3]);
        } else throw new Error('unknown source event');
    }
    function mixed(index) {
        const start = index*frameSamples, stop = start+frameSamples;
        let floats;
        for (let i=sound.length-1; i>=0; i--) {
            const [at,samples] = sound[i];
            const end = at+samples.length;
            if (at < stop && end > start) {
                floats ??= new Float32Array(frameSamples);
                for (let pos=Math.max(at,start); pos<Math.min(end,stop); pos++) floats[pos-start] += samples[pos-at];
            }
            if (end <= stop) sound.splice(i,1);
        }
        if (!floats) return null;
        const buffer = new Uint8Array(frameSamples*2), pcm = new DataView(buffer.buffer);
        for (let i=0;i<floats.length;i++) pcm.setInt16(i*2,Math.round(Math.max(-1,Math.min(1,floats[i]))*32767),true);
        return buffer.toBase64();
    }
    function composite(at) {
        const cameras = [], owner = archive?.screenOwner;
        let screen, preferred = false, main;
        for (const item of tracks.values()) {
            if (!item.video || item.muted) continue;
            if (item.source === 'camera') {
                if (item.identity === teacher) main = item.video;
                cameras.push({role:item.identity.split('-',1)[0],video:item.video});
            } else if (item.source === 'screen_share' && owner?.identity === item.identity && owner?.sid === item.sid && !preferred) {
                screen = item.video;
                preferred = archive?.screen === item.pub;
            }
        }
        const rank = {teacher:0,admin:1,student:2};
        cameras.sort((a,b) => rank[a.role] - rank[b.role]);
        paint({paper,ink,doc,view:board.framing(),hasInk:board.position().ink>0,laser,cameras,screen,teacher:main,
            archiveBoard:archive?.board === true,preferScreen:preferred,heading,at});
    }
    async function ready() {
        if (!window.AudioDecoder || !window.VideoDecoder || typeof Uint8Array.fromBase64 !== 'function') return false;
        fonts ??= Promise.all(['ArchiveVazir','ClassroomPDFPersian'].flatMap(family => [400,700].map(async weight => {
            const loaded = await document.fonts.load(`${weight} 16px ${family}`, 'گچ‌پژکی');
            return loaded.some(face => face.family === family && face.status === 'loaded');
        })));
        return (await fonts).every(Boolean) && (await AudioDecoder.isConfigSupported(opus)).supported
            && (await VideoDecoder.isConfigSupported(configure('vp8'))).supported;
    }
    window.replay = {
        ready,
        async start(meta) {
            if (!await ready()) throw new Error('replay codecs or fonts unavailable');
            scope = {lesson:meta.lesson,epoch:meta.epoch}; heading = meta.title; teacher = meta.teacher; archive = meta.archive;
            nativeClock = meta.clock === 'rtcp-v1';
            board.reset();
        },
        async frame(index, updates) {
            const at = index*1000/12;
            if (updates.length) await load(updates);
            while (queue.length && queue[0][2] <= at) await apply(queue.shift());
            if (failure) throw new Error(failure);
            const lit = laser.some(([, , until]) => until > at);
            let png = null;
            if (dirty || lit || hadLaser) {
                composite(at);
                png = canvas.toDataURL('image/png').slice('data:image/png;base64,'.length);
                dirty = false;
            }
            hadLaser = lit;
            return {png,pcm:mixed(index)};
        },
    };
})();
