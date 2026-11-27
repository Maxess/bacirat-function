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
    function placeSound(item, at, ts, samples) {
        let pos = at*48, slew = 0;
        if (item.ts !== null) {
            const predicted = item.pos + ((ts-item.ts)|0);
            const drift = pos-predicted;
            if (Math.abs(drift) <= (nativeClock ? 5760 : 12000)) {
                pos = predicted;
                if (nativeClock && samples.length >= 120 && Math.abs(drift) > 480) {
                    slew = Math.sign(drift)*Math.max(1,Math.round(samples.length/320));
                }
            }
        }
        item.ts = ts;
        item.pos = pos+slew;
        if (slew) {
            // Slew the duration, not the start: shifting consecutive packets
            // leaves holes or sums them at a boundary, both audible as clicks.
            // Keep the decoded endpoints so this small correction joins smoothly.
            const adjusted = new Float32Array(samples.length+slew);
            const last = adjusted.length-1, scale = (samples.length-1)/last;
            for (let i=0;i<last;i++) {
                const offset = i*scale, left = Math.floor(offset);
                adjusted[i] = samples[left] + (samples[left+1]-samples[left])*(offset-left);
            }
            adjusted[last] = samples[samples.length-1];
            samples = adjusted;
        }
        sound.push([pos,samples]);
    }
    function onSound(item, frame) {
        try {
            const n = frame.numberOfFrames, timing = item.timing[item.decoded++];
            // Raw Opus yields one ordered PCM block per packet. Chromium may
            // synthesize contiguous decoder timestamps, even across an RTP gap.
            if (!timing || frame.sampleRate !== 48000 || !n || n > 5760 || sound.length >= 12000) { fail(); return; }
            const samples = new Float32Array(n), plane = new Float32Array(n);
            for (let ch=0; ch<frame.numberOfChannels; ch++) {
                frame.copyTo(plane,{planeIndex:ch,format:'f32-planar'});
                for (let i=0;i<n;i++) samples[i] += plane[i]/frame.numberOfChannels;
            }
            placeSound(item,...timing,samples);
        } catch (_) { fail(); }
        finally {
            frame.close();
            if (failure || item.decoded === item.timing.length) item.settle?.();
        }
    }
    async function waitAudio(item) {
        // Do not flush per video frame: that resets Opus prediction and
        // makes the resulting sound depend on how the reader batched RTP.
        if (!failure && item.decoded < item.timing.length) await new Promise(resolve => {
            const timer = setTimeout(() => { fail(); item.settle?.(); }, 8000);
            item.settle = () => { clearTimeout(timer); item.settle = null; resolve(); };
        });
        if (failure || item.decoded !== item.timing.length) throw new Error('archived audio decode incomplete');
        item.timing.length = item.decoded = 0;
    }
    function decodeAudio(id, at, ts, encoded) {
        if (!Number.isSafeInteger(at) || !Number.isSafeInteger(ts)) throw new Error('invalid archived audio time');
        let item = audio.get(id);
        if (!item) {
            item = {ts:null,pos:0,timing:[],decoded:0,settle:null};
            item.decoder = new AudioDecoder({output:frame => onSound(item,frame),error:() => { fail(); item.settle?.(); }});
            item.decoder.configure(opus);
            audio.set(id,item);
        }
        item.timing.push([at,ts]);
        item.decoder.decode(new EncodedAudioChunk({type:'key',timestamp:at*1000,data:Uint8Array.fromBase64(encoded)}));
        return item;
    }
    async function load(updates) {
        const pending = new Set(), ended = [];
        for (const update of updates) {
            const [type,id,at,codec,ts,,bytes] = update;
            if (type === 'media' && codec === 'audio/opus') pending.add(decodeAudio(id,at,ts,bytes));
            else queue.push(update);
            if (type === 'end') ended.push(id);
        }
        await Promise.all(Array.from(pending, waitAudio));
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
