(() => {
    'use strict';
    window.ClassroomBoard = (root, options) => {
        const el = id => document.getElementById(id);
        const card = el('board'), stage = el('board-stage'), viewport = el('board-scroll'), bed = el('board-bed');
        const paper = el('board-paper'), ink = el('board-ink');
        const context = ink.getContext('2d');
        const background = paper.getContext('2d', {alpha:false});
        const zoom = el('board-zoom'), zoomOut = el('board-zoom-out'), zoomIn = el('board-zoom-in'), sharedZoom = el('board-zoom-shared');
        const zoomSteps = [.75, 1, 1.25, 1.5, 2, 2.5, 3];
        const percentage = new Intl.NumberFormat('fa-IR', {style:'percent'}), pageNumber = new Intl.NumberFormat('fa-IR');
        const fullView = {zoom:100,crop:[0,0,10000,10000]}; // Same normalized sheet coordinates as ink.
        let zoomLevel = 1, framing = fullView, viewFrame;
        let data = null, transport = null, cursor = 0, ops = [], stroke = null, pointer = null;
        let pending = [], sent = [], writing = null, writeTimer, generation = 0, targetRev = 0;
        const removed = new Set(), bounds = new WeakMap();
        const packetSize = 96, eraseSize = 32, batchSize = 6, queueLimit = 48, writeDelay = 175;
        const tip = el('eraser-tip');
        let tool = 'pen', busy = false, renderVersion = 0, documentVersion = 0, docsKey = '';
        const loadFile = el('board-load-file'), fileRetry = el('board-file-receive');
        let drag = null, pointerType = '', candidate = null, pinch = null, pinchFrame, touchBlocked = false, penUntil = 0, space = false;
        const contacts = new Map();
        const writers = el('board-writers'), labels = new Map();
        const laserTtl = 1900, laserLimit = 20, laserSampleMs = 48; // Wire limits match activity.php.
        const laserTrail = side => ({dot:el(`board-laser-${side}`), layer:el(`board-laser-${side}-trail`), samples:[], lines:[]});
        const localLaser = laserTrail('local'), remoteLaser = laserTrail('remote');
        let aimPending = null, laserFrame;
        let library, pdfSession, rendering, imageLoad, imageFile;
        const pdfChunk = 16 * 1024, pdfBatch = 16; // Multi-range cap matches content.php.
        const staff = root.dataset.moderator === 'yes';
        const url = value => new URL(value, document.baseURI).href;
        const widthLimit = 2048;
        const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
        let playback = null;

        const message = text => { if (text) options.notice(text); };
        function cancelRender() {
            renderVersion++;
            rendering?.cancel();
            rendering = null;
        }
        function clearPDF() {
            const session = pdfSession;
            pdfSession = null;
            clearTimeout(session?.timer);
            session?.controller.abort();
            session?.task?.destroy().catch(() => {});
        }
        function clearDocument() {
            cancelRender();
            documentVersion++;
            clearPDF();
            imageLoad?.abort(); imageLoad = null;
            imageFile?.close?.(); imageFile = null;
        }
        function pdfLib() {
            if (!library) library = Promise.all([
                import(url(root.dataset.pdf)),
                // Load on demand, before painting to a canvas (which will not repaint when a font arrives).
                Promise.allSettled([400,700].map(weight => document.fonts?.load(`${weight} 16px ClassroomPDFPersian`, 'گچپژکی'))),
            ]).then(([lib]) => {
                lib.GlobalWorkerOptions.workerSrc = url(root.dataset.pdfWorker);
                return lib;
            }).catch(e => { library = null; throw e; });
            return library;
        }
        function pdfOptions() {
            const prefix = url(root.dataset.pdfData);
            return {
                cMapUrl:`${prefix}cmaps/`, cMapPacked:true, standardFontDataUrl:`${prefix}standard_fonts/`,
                wasmUrl:`${prefix}wasm/`, iccUrl:`${prefix}iccs/`, useWorkerFetch:true, enableXfa:false,
                // Parse/decode in the PDF worker; paint in the document so its local fallback font is available.
                disableWorkerRendering:true, isEvalSupported:false, maxImageSize:24_000_000, canvasMaxAreaInBytes:24_000_000,
            };
        }
        function pdfFetch(source, signal, range) {
            return fetch(source, {mode:'same-origin', credentials:'same-origin', cache:'no-store',
                redirect:'error', priority:'low', signal, headers:range ? {Range:range} : undefined});
        }
        const discardBody = response => response.body?.cancel().catch(() => {});
        async function pdfBytes(source, length, signal, begin, end, allowWhole) {
            const ranged = !allowWhole || end - begin < length;
            const response = await pdfFetch(source,signal,ranged ? `bytes=${begin}-${end-1}` : null);
            const whole = response.status === 200 && allowWhole;
            const partial = response.status === 206 && ranged && response.headers.get('Content-Range') === `bytes ${begin}-${end-1}/${length}`;
            if ((!whole && !partial) || response.headers.get('Content-Type')?.split(';')[0] !== 'application/pdf') {
                discardBody(response);
                throw new Error('Unexpected PDF content response');
            }
            const bytes = new Uint8Array(await response.arrayBuffer());
            if (bytes.length !== (whole ? length : end-begin)) throw new Error('Unexpected PDF size');
            return bytes;
        }
        async function pdfParts(source, length, session, ranges) {
            const {signal} = session.controller;
            if (ranges.length > 1 && !session.singleRanges) {
                const res = await pdfFetch(source,signal,'bytes=' + ranges.map(([a,b]) => `${a}-${b-1}`).join(','));
                const boundary = /^multipart\/byteranges;\s*boundary="?([\w-]{1,70})"?$/i.exec(res.headers.get('Content-Type') || '')?.[1];
                if (res.status === 206 && boundary) {
                    const bytes = new Uint8Array(await res.arrayBuffer()), encoder = new TextEncoder();
                    let at = 0;
                    const expect = text => {
                        const header = encoder.encode(text);
                        if (!header.every((byte,i) => bytes[at+i] === byte)) throw new Error('Invalid PDF part');
                        at += header.length;
                    };
                    const parts = ranges.map(([a,b]) => {
                        expect(`--${boundary}\r\nContent-Type: application/pdf\r\nContent-Range: bytes ${a}-${b-1}/${length}\r\n\r\n`);
                        const end = at+b-a, part = bytes.slice(at,end);
                        at = end; expect('\r\n');
                        return part;
                    });
                    expect(`--${boundary}--\r\n`);
                    if (at !== bytes.length) throw new Error('Unexpected PDF parts size');
                    return parts;
                }
                discardBody(res);
                if (![200,206,416].includes(res.status)) throw new Error('PDF ranges unavailable');
                // A proxy may strip multipart Range. Never retain its unbounded response;
                // retry only the denied offsets as ordinary single ranges in this session.
                session.singleRanges = true;
            }
            return Promise.all(ranges.map(([a,b]) => pdfBytes(source,length,signal,a,b,false)));
        }
        async function openPDF(pdf, source, length, session) {
            const {signal} = session.controller;
            const end = length > 128*1024 ? pdfChunk : length;
            // Don't start a full GET just to discover range support. Seed the worker with the
            // first range; a host that ignores Range can still supply one complete response.
            const data = await pdfBytes(source,length,signal,0,end,true);
            if (signal.aborted) throw signal.reason;
            let input = {data};
            if (data.length < length) {
                input = {range:new class extends pdf.PDFDataRangeTransport {
                    requestDataRange(begin,end) {
                        if (pdfSession !== session || signal.aborted) return;
                        session.queue.push([begin,end]);
                        session.timer ??= setTimeout(() => this.flush(),8);
                    }
                    flush() {
                        session.timer = undefined;
                        if (signal.aborted) return;
                        // Page dictionaries can be scattered all over a scanned PDF. Group the
                        // worker's concurrent reads, without fetching the intervening images.
                        const queued = session.queue.splice(0).sort((a,b) => a[0]-b[0]);
                        for (let i=0;i<queued.length;i+=pdfBatch) {
                            const group = queued.slice(i,i+pdfBatch);
                            pdfParts(source,length,session,group).then(parts => {
                                if (!signal.aborted) group.forEach(([a],j) => this.onDataRange(a,parts[j]));
                            }).catch(() => { if (session === pdfSession) documentFailed(); });
                        }
                    }
                    abort() { session.controller.abort(); }
                }(length,data,true)};
            }
            session.task = pdf.getDocument({...input, rangeChunkSize:pdfChunk, disableAutoFetch:true, disableStream:true, ...pdfOptions()});
            return session.task.promise;
        }
        function documentURL(doc, scope) {
            const link = new URL(root.dataset.content, document.baseURI);
            for (const [key,val] of Object.entries({...scope, doc:doc.id})) link.searchParams.set(key,val);
            return link.href;
        }
        const clamp = (n, min=0, max=1) => Math.max(min, Math.min(max,n));
        const controlsView = () => staff && data?.navigate === true;
        const canNavigate = () => controlsView() && editable();
        const sameView = (a,b) => a.zoom === b.zoom && a.crop.every((n,i) => n === b.crop[i]);
        function checkedView(view) {
            const {zoom,crop} = view ?? {};
            return Number.isInteger(zoom) && zoom >= 75 && zoom <= 300 && Array.isArray(crop) && crop.length === 4
                && crop.every(n => Number.isInteger(n) && n >= 0 && n <= 10000)
                && crop[2]-crop[0] >= 3000 && crop[3]-crop[1] >= 3000
                && (zoom > 100 || crop.every((n,i) => n === fullView.crop[i])) ? view : fullView;
        }
        function fitView() {
            if (options.rasterOnly) return;
            const follow = controlsView() ? 'no' : 'yes';
            if (viewport.dataset.follow !== follow) {
                viewport.dataset.follow = follow;
                viewport.setAttribute('aria-label', follow === 'yes' ? 'نمای تخته، همگام با دبیر' : 'نمای تخته؛ بزرگ‌نمایی و پیمایش');
                bed.style.width = bed.style.height = stage.style.left = stage.style.top = '';
            }
            const w = viewport.clientWidth, h = viewport.clientHeight;
            if (!w || !h) return;
            const aspect = paper.width / paper.height;
            if (follow === 'yes') {
                const [l,t,r,b] = framing.crop.map(n => n/10000);
                const scale = Math.min(w/((r-l)*aspect),h/(b-t)) * Math.min(framing.zoom/100,1);
                bed.style.width = `${scale*(r-l)*aspect}px`;
                bed.style.height = `${scale*(b-t)}px`;
                stage.style.width = `${scale*aspect}px`;
                stage.style.left = `${-scale*aspect*l}px`;
                stage.style.top = `${-scale*t}px`;
            } else {
                const width = `${Math.max(1, Math.round(Math.min(w,h*aspect)*zoomLevel))}px`;
                if (stage.style.width !== width) stage.style.width = width;
                queueView();
            }
            dragState();
        }
        function canDrag() { return canNavigate() && (viewport.scrollWidth > viewport.clientWidth || viewport.scrollHeight > viewport.clientHeight); }
        function dragState() { viewport.dataset.drag = drag || pinch ? 'moving' : canDrag() && (tool === 'pan' || space) ? 'ready' : 'no'; }
        const at = event => [event.clientX,event.clientY];
        function center() {
            const box = viewport.getBoundingClientRect();
            return [box.left + viewport.clientWidth/2, box.top + viewport.clientHeight/2];
        }
        function relative(point) {
            const box = stage.getBoundingClientRect();
            return [clamp((point[0]-box.left)/Math.max(1,box.width)),clamp((point[1]-box.top)/Math.max(1,box.height))];
        }
        function focusAt(anchor, point = center()) {
            const box = stage.getBoundingClientRect();
            const dx = box.left + anchor[0]*box.width - point[0], dy = box.top + anchor[1]*box.height - point[1];
            if (Math.abs(dx) >= .5) viewport.scrollLeft += dx;
            if (Math.abs(dy) >= .5) viewport.scrollTop += dy;
        }
        function zoomLabels() {
            const teacher = controlsView();
            if (zoom) {
                zoom.hidden = zoomOut.hidden = zoomIn.hidden = !teacher;
                zoom.disabled = !canNavigate();
                zoomOut.disabled = zoom.disabled || zoomLevel <= zoomSteps[0];
                zoomIn.disabled = zoom.disabled || zoomLevel >= zoomSteps[zoomSteps.length-1];
                const value = percentage.format(zoomLevel);
                if (zoom.textContent !== value) zoom.textContent = value;
            }
            if (sharedZoom) {
                sharedZoom.hidden = teacher;
                const value = percentage.format(framing.zoom/100);
                if (sharedZoom.textContent !== value) sharedZoom.textContent = value;
            }
        }
        function setZoom(level, point = center(), anchor = relative(point)) {
            if (!canNavigate()) return;
            zoomLevel = Math.round(100*clamp(level, zoomSteps[0], zoomSteps[zoomSteps.length-1]))/100;
            fitView();
            focusAt(anchor,point);
            zoomLabels();
        }
        function stepZoom(direction) {
            const list = direction > 0 ? zoomSteps : [...zoomSteps].reverse();
            const next = list.find(n => direction*(n-zoomLevel) > .01);
            if (next !== undefined) setZoom(next);
        }
        function takeView() {
            if (!canNavigate() || document.hidden || !viewport.clientWidth || !viewport.clientHeight) return {};
            const zoom = Math.round(100*zoomLevel);
            let crop = fullView.crop;
            if (zoom > 100) {
                const a = viewport.getBoundingClientRect(), b = stage.getBoundingClientRect();
                if (!b.width || !b.height) return {};
                const normalized = (p, origin, size) => Math.round(10000*clamp((p-origin)/size));
                crop = [normalized(a.left,b.left,b.width), normalized(a.top,b.top,b.height),
                    normalized(a.left+viewport.clientWidth,b.left,b.width), normalized(a.top+viewport.clientHeight,b.top,b.height)];
            }
            const view = {zoom,crop};
            return checkedView(view) === view && !sameView(view,framing) ? {view} : {};
        }
        function queueView() {
            if (!controlsView() || viewFrame !== undefined || !options.aimSoon) return;
            viewFrame = requestAnimationFrame(() => {
                viewFrame = undefined;
                if (takeView().view) options.aimSoon();
            });
        }
        function fit(width, height) {
            if (!(width > 0 && height > 0 && Number.isFinite(width/height))) throw new Error('Invalid board size');
            if (pointer !== null || candidate) stopGestures();
            const anchor = controlsView() ? relative(center()) : null;
            const scale = Math.min(widthLimit / Math.max(width,height), Math.sqrt(3000000 / (width*height)));
            ink.width = paper.width = Math.max(1, Math.ceil(width * scale));
            ink.height = paper.height = Math.max(1, Math.ceil(height * scale));
            stage.style.aspectRatio = `${paper.width} / ${paper.height}`;
            fitView();
            if (anchor) focusAt(anchor);
            background.fillStyle = '#fff';
            background.fillRect(0,0,paper.width,paper.height);
            redraw();
            return scale;
        }
        function paint(op) {
            const p = op.points;
            if (!p.length) return;
            const w = ink.width, h = ink.height, d = Math.min(w,h);
            const x = n => w * n / 10000, y = n => h * n / 10000;
            const size = Math.max(1, d * op.size / 550);
            context.save();
            context.globalCompositeOperation = op.tool === 'erase' ? 'destination-out' : 'source-over';
            context.fillStyle = context.strokeStyle = op.color;
            context.lineWidth = size;
            context.lineJoin = context.lineCap = 'round';
            context.beginPath();
            if (p.length === 1) {
                context.arc(x(p[0][0]),y(p[0][1]),size/2,0,2*Math.PI);
                context.fill();
            } else {
                context.moveTo(x(p[0][0]),y(p[0][1]));
                for (let i = 1; i < p.length; i++) context.lineTo(x(p[i][0]),y(p[i][1]));
                context.stroke();
            }
            context.restore();
        }
        const samePoint = (a, b) => a[0] === b[0] && a[1] === b[1];
        const last = list => list[list.length - 1];
        const drafts = () => [...sent.flatMap(entry => entry.ops), ...pending];
        function collectRemoved(hidden, list) {
            for (const op of list) if (op.tool === 'erase-stroke') for (const id of op.targets) hidden.add(id);
            return hidden;
        }
        function paintVisible(op, hidden) {
            if (op.tool !== 'erase-stroke' && !hidden.has(op.id)) paint(op);
        }
        function redraw() {
            stopPlayback();
            const draft = drafts();
            const hidden = collectRemoved(new Set(removed), draft);
            context.clearRect(0,0,ink.width,ink.height);
            for (const op of ops) paintVisible(op, hidden);
            for (const op of draft) paintVisible(op, hidden);
        }
        function stopPlayback(finish = false) {
            if (!playback) return;
            const current = playback;
            playback = null;
            cancelAnimationFrame(current.frame);
            if (finish) current.draw(1);
        }
        function locate(node, point) {
            node.style.left = `${point[0]/100}%`;
            node.style.top = `${point[1]/100}%`;
        }
        function clearLabels() {
            for (const cue of labels.values()) clearTimeout(cue.timer);
            labels.clear(); writers.replaceChildren();
        }
        function writer(op, point) {
            if (!op.live || !op.author || op.author === options.identity() || !point || removed.has(op.id)) return;
            const name = options.authorName(op.author);
            if (!name) return;
            let cue = labels.get(op.author);
            if (!cue) {
                const label = document.createElement('bdi');
                label.className = 'ink-writer';
                writers.append(label);
                labels.set(op.author, cue = {label});
            }
            const {label} = cue;
            if (label.textContent !== name) label.textContent = name;
            locate(label,point);
            label.dataset.left = String(point[0] > 5450);
            label.dataset.below = String(point[1] < 1600);
            label.classList.remove('fading');
            cue.id = op.id;
            clearTimeout(cue.timer);
            cue.timer = setTimeout(() => {
                label.classList.add('fading');
                cue.timer = setTimeout(() => { label.remove(); labels.delete(op.author); }, reducedMotion?.matches ? 0 : 160);
            }, 370);
        }
        function lastWriters(rows) {
            const latest = new Map();
            for (const op of rows) if (op.tool === 'pen' && op.live && op.author) latest.set(op.author,op);
            for (const op of latest.values()) writer(op,last(op.points));
        }
        function presentInk(view, catchingUp) {
            stopPlayback(true);
            const rows = view.ops;
            // A quick character can arrive entirely in one HTTP response. Reveal just recent ink
            // along its actual path; old pages, erasures and catch-up never wait for an animation.
            if (pointer === null && !document.hidden && !reducedMotion?.matches && view.moving && !catchingUp && rows.length <= 8
                && rows.every(op => op.tool === 'pen' && op.id && !op.id.startsWith('old-'))) {
                const parts = [];
                let length = 0;
                for (const op of rows) {
                    if (removed.has(op.id)) continue;
                    if (op.points.length === 1) {
                        parts.push({op, from:length, to:length+4});
                        length += 4;
                    }
                    for (let i = 1; i < op.points.length; i++) {
                        const a = op.points[i-1], b = op.points[i];
                        if (samePoint(a,b)) continue;
                        const distance = Math.hypot((b[0]-a[0])*ink.width, (b[1]-a[1])*ink.height)/10000;
                        parts.push({op,a,b,from:length,to:length+distance});
                        length += distance;
                    }
                }
                if (parts.some(part => part.b)) {
                    const duration = Math.min(245, Math.max(145, 112 + .24*length));
                    const start = performance.now();
                    let index = 0, painted = 0;
                    const current = {draw(fraction) {
                        const target = length*fraction;
                        const cues = new Map();
                        while (index < parts.length && painted < target) {
                            const {op,a,b,from,to} = parts[index];
                            const end = Math.min(target,to);
                            if (b) {
                                const at = n => {
                                    const f = (n-from)/(to-from);
                                    return [a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1])];
                                };
                                const head = at(end);
                                paint({...op, points:[at(painted),head]});
                                if (op.live && op.author) cues.set(op.author,[op,head]);
                            } else if (painted === from) {
                                paint(op);
                                if (op.live && op.author) cues.set(op.author,[op,op.points[0]]);
                            }
                            painted = end;
                            if (painted === to) index++;
                        }
                        for (const [op,point] of cues.values()) writer(op,point);
                    }};
                    function tick(now) {
                        if (playback !== current) return;
                        const fraction = Math.max(0, Math.min(1, (now-start)/duration));
                        current.draw(fraction);
                        if (fraction < 1) current.frame = requestAnimationFrame(tick);
                        else playback = null;
                    }
                    playback = current;
                    current.frame = requestAnimationFrame(tick);
                    return;
                }
            }
            for (const op of rows) paintVisible(op, removed);
            if (!catchingUp && !document.hidden) lastWriters(rows);
        }
        function receive(incoming) {
            collectRemoved(removed, incoming);
            for (const op of incoming) {
                const tail = last(ops);
                if (op.tool === 'pen' && tail?.tool === 'pen' && tail.id === op.id && op.id && tail.color === op.color && tail.size === op.size
                    && samePoint(last(tail.points), op.points[0]) && tail.points.length + op.points.length - 1 <= packetSize) {
                    ops[ops.length - 1] = {...op, points:[...tail.points, ...op.points.slice(1)]};
                } else ops.push(op);
            }
        }
        function prune() {
            const before = sent.length;
            sent = sent.filter(entry => entry.until === null || entry.until > cursor);
            return before !== sent.length;
        }
        function clearTrail(trail) {
            trail.samples = [];
            if (trail.lines.length) trail.layer.replaceChildren();
            trail.lines = [];
            trail.head = null;
            trail.dot.hidden = true;
        }
        function drawTrail(trail, now) {
            if (!trail.samples.length) return false;
            while (trail.samples[0]?.[2] <= now) trail.samples.shift();
            if (!trail.samples.length) { clearTrail(trail); return false; }
            const {samples,lines,layer,dot} = trail;
            const head = last(samples);
            if (trail.head !== head) { trail.head = head; locate(dot,head); }
            dot.hidden = false;
            dot.style.opacity = String(Math.min(1,(head[2]-now)/500));
            // Each segment loses opacity with its own age, so the tail fades before the tip.
            for (let i = 1; i < samples.length; i++) {
                const a = samples[i-1], b = samples[i];
                let line = lines[i-1];
                if (!line) {
                    const node = document.createElementNS('http://www.w3.org/2000/svg','line');
                    layer.append(node);
                    lines.push(line = {node});
                }
                if (line.from !== a || line.to !== b) {
                    line.node.setAttribute('x1', a[0]); line.node.setAttribute('y1', a[1]);
                    line.node.setAttribute('x2', b[0]); line.node.setAttribute('y2', b[1]);
                    line.from = a; line.to = b;
                }
                line.node.style.opacity = String(Math.min(1,(a[2]-now)/1200));
            }
            while (lines.length >= samples.length) lines.pop().node.remove();
            return true;
        }
        function animateLaser() {
            if (laserFrame !== undefined || document.hidden) return;
            laserFrame = requestAnimationFrame(now => {
                laserFrame = undefined;
                const local = drawTrail(localLaser, now), remote = drawTrail(remoteLaser, now);
                if (local || remote) animateLaser();
            });
        }
        function clearPointers() {
            aimPending = null;
            cancelAnimationFrame(laserFrame); laserFrame = undefined;
            clearTrail(localLaser); clearTrail(remoteLaser);
        }
        function showRemoteLaser(aim, requestedAt) {
            if (!aim?.points?.length || aim.by === options.identity() || document.hidden) { clearTrail(remoteLaser); return; }
            const now = performance.now();
            // Conservatively spend the read's round-trip time from the cue, so delayed replies never replay it.
            remoteLaser.samples = aim.points.filter(([, , left]) => requestedAt+left > now)
                .map(([x,y,left]) => [x,y,requestedAt+left]);
            if (remoteLaser.samples.length) animateLaser();
            else clearTrail(remoteLaser);
        }
        function pointerEnd() {
            pointer = null; pointerType = ''; stroke = null; candidate = null;
            tip.hidden = true;
        }
        function abandonUnsentTouch() {
            // A touch can begin moving just before the second finger or the stylus is detected.
            // Discard only its unshared preview; never retract established handwriting.
            if (pointerType !== 'touch' || stroke.shared) return;
            const id = stroke.style?.id, erasures = stroke.erasures;
            if (!id && !erasures) return;
            const count = pending.length;
            pending = pending.filter(op => !(id && op.id === id) && !erasures?.has(op));
            if (pending.length !== count) redraw();
        }
        function stopGestures() {
            abandonUnsentTouch();
            cancelAnimationFrame(pinchFrame); pinchFrame = undefined;
            if (contacts.size) touchBlocked = true;
            pinch = drag = null; space = false;
            pointerEnd(); dragState();
        }
        function wipeInk() {
            generation++;
            stopPlayback(); stopGestures(); clearLabels(); clearPointers();
            clearTimeout(writeTimer); writeTimer = undefined;
            ops = []; pending = []; sent = [];
            removed.clear(); targetRev = 0; cursor = 0;
        }
        function canWrite() { return options.canEdit() && (staff || data?.write === true); }
        function editable() { return !!data && !busy && data.rev >= targetRev && canWrite(); }
        function queuedSlot() {
            if (pending.length + sent.reduce((count, entry) => count + entry.ops.length, 0) < queueLimit) return true;
            message('همگام‌سازی تخته عقب افتاده است؛ لحظه‌ای صبر کن.');
            pointerEnd();
            return false;
        }
        function scheduleWrite() {
            if (writing || writeTimer !== undefined || !pending.length || busy || !canWrite()) return;
            writeTimer = setTimeout(write, writeDelay);
        }
        function write() {
            clearTimeout(writeTimer); writeTimer = undefined;
            if (writing || !data || !pending.length || !canWrite()) return writing;
            const stamp = generation;
            const entry = {ops:pending.splice(0,batchSize), until:null};
            if (pointerType === 'touch' && entry.ops.some(op => (stroke.style && op.id === stroke.style.id)
                || stroke.erasures?.has(op))) stroke.shared = true;
            sent.push(entry);
            writing = options.send('board-ink',{board:data.rev,strokes:entry.ops}).then(reply => {
                if (stamp !== generation) return;
                entry.until = reply.ink;
                if (prune()) redraw();
            }).catch(error => {
                if (stamp !== generation) return;
                // An unknown or rejected prefix must not be linked to later points.
                pending = []; sent = sent.filter(row => row !== entry);
                pointerEnd(); redraw(); message(options.explain(error));
            }).finally(() => { writing = null; scheduleWrite(); });
            return writing;
        }
        function area(op) {
            if (!bounds.has(op)) {
                let x0 = 10000, y0 = 10000, x1 = 0, y1 = 0;
                for (const [x,y] of op.points) { x0 = Math.min(x0,x); y0 = Math.min(y0,y); x1 = Math.max(x1,x); y1 = Math.max(y1,y); }
                bounds.set(op, [x0,y0,x1,y1]);
            }
            return bounds.get(op);
        }
        function distance2(p, a, b, sx, sy) {
            const dx = (b[0]-a[0])*sx, dy = (b[1]-a[1])*sy;
            const x = (p[0]-a[0])*sx, y = (p[1]-a[1])*sy;
            const t = Math.max(0, Math.min(1, (x*dx+y*dy)/(dx*dx+dy*dy || 1)));
            return (x-t*dx)**2 + (y-t*dy)**2;
        }
        function crosses(a, b, c, d) {
            const sign = (p, q, r) => Math.sign((q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0]));
            return sign(a,b,c)*sign(a,b,d) <= 0 && sign(c,d,a)*sign(c,d,b) <= 0
                && [0,1].every(i => Math.max(Math.min(a[i],b[i]),Math.min(c[i],d[i])) <= Math.min(Math.max(a[i],b[i]),Math.max(c[i],d[i])));
        }
        function touches(op, a, b, r, radius) {
            const sx = r.width / 10000, sy = r.height / 10000;
            const reach = radius + Math.min(r.width,r.height)*op.size/1100;
            const [x0,y0,x1,y1] = area(op);
            if (Math.max(a[0],b[0])+reach/sx < x0 || Math.min(a[0],b[0])-reach/sx > x1
                || Math.max(a[1],b[1])+reach/sy < y0 || Math.min(a[1],b[1])-reach/sy > y1) return false;
            const hit = reach*reach, p = op.points;
            if (p.length === 1) return distance2(p[0],a,b,sx,sy) <= hit;
            for (let i = 1; i < p.length; i++) {
                const c = p[i-1], d = p[i];
                if (crosses(a,b,c,d) || distance2(a,c,d,sx,sy) <= hit || distance2(b,c,d,sx,sy) <= hit
                    || distance2(c,a,b,sx,sy) <= hit || distance2(d,a,b,sx,sy) <= hit) return true;
            }
            return false;
        }
        function blankPaper(doc) {
            // Keep image ink aligned while loading and never show pixels of the previous page.
            fit(doc?.width || 1600, doc?.height || 1000);
        }
        function documentFailed() {
            clearDocument();
            fileRetry.hidden = false;
            blankPaper(data.doc);
            message('نمایش فایل تخته کامل نشد. اتصال را بررسی کن و دوباره تلاش کن.');
        }
        async function show(view, scope) {
            cancelRender();
            const ticket = renderVersion;
            const docTicket = documentVersion;
            fileRetry.hidden = true;
            stage.classList.toggle('has-document', !!view.doc);
            if (!view.doc) return;
            const doc = view.doc;
            try {
                const source = documentURL(doc,scope);
                if (doc.mime === 'application/pdf') {
                    const pdf = await pdfLib();
                    if (docTicket !== documentVersion || ticket !== renderVersion) return;
                    if (!pdfSession) {
                        pdfSession = {controller:new AbortController(), queue:[]};
                        pdfSession.promise = openPDF(pdf,source,doc.bytes,pdfSession);
                    }
                    const session = pdfSession;
                    const file = await session.promise;
                    if (ticket !== renderVersion) return;
                    if (view.page > file.numPages) throw new Error('Invalid PDF page');
                    const page = await file.getPage(view.page);
                    if (ticket !== renderVersion) return;
                    const base = page.getViewport({scale:1});
                    const scale = fit(base.width,base.height);
                    const task = page.render({canvasContext:background,viewport:page.getViewport({scale}), annotationMode:pdf.AnnotationMode.DISABLE});
                    rendering = task;
                    await task.promise;
                    if (rendering === task) rendering = null;
                    if (ticket === renderVersion) page.cleanup();
                } else {
                    if (!imageFile) {
                        const ctrl = new AbortController(); imageLoad = ctrl;
                        const res = await fetch(source,{credentials:'same-origin',cache:'no-store',signal:ctrl.signal,priority:'low'});
                        if (!res.ok) throw new Error('Image unavailable');
                        const blob = await res.blob();
                        const img = await decodeImage(blob);
                        if (docTicket !== documentVersion || ticket !== renderVersion) { img.close?.(); return; }
                        imageFile = img;
                    }
                    fit(imageFile.width,imageFile.height);
                    background.drawImage(imageFile,0,0,paper.width,paper.height);
                }
            } catch (err) {
                if (ticket === renderVersion && err.name !== 'RenderingCancelledException') documentFailed();
            }
        }
        async function decodeImage(blob) {
            if ('createImageBitmap' in window) return await window.createImageBitmap(blob);
            const src = URL.createObjectURL(blob);
            try {
                return await new Promise((resolve,reject) => {
                    const img = new Image(); img.onload = () => resolve(img); img.onerror = reject; img.src = src;
                });
            } finally { URL.revokeObjectURL(src); }
        }
        function toolState() {
            const ready = editable();
            el('ink-tools').hidden = !staff && !canWrite();
            stage.classList.toggle('editable', ready);
            stage.classList.toggle('erasing',ready && tool === 'erase');
            if (!ready) tip.hidden = true;
            el('eraser').hidden = !staff && !canWrite();
            for (const id of ['pen','eraser','ink-color','ink-size']) el(id).disabled = !ready;
            viewport.dataset.ink = ready ? 'yes' : 'no';
            zoomLabels();
            dragState();
            if (!staff) return;
            const teacher = controlsView(), navigate = canNavigate();
            for (const id of ['board-laser','board-clear']) el(id).disabled = !ready;
            el('pan').hidden = el('board-pages').hidden = el('toggle-files').hidden = !teacher;
            if (!teacher) {
                el('board-files').hidden = true;
                el('toggle-files').setAttribute('aria-expanded','false');
            }
            for (const id of ['pan','toggle-files','board-file','board-doc']) el(id).disabled = !navigate;
            el('board-upload').querySelector('button').disabled = !navigate;
            const pages = data?.doc?.pages || 1;
            const n = data?.page || 1;
            el('page-number').max = String(pages);
            el('page-number').disabled = el('page-go').disabled = !navigate || pages === 1;
            el('page-prev').disabled = !navigate || n <= 1;
            el('page-next').disabled = !navigate || n >= pages;
        }
        function sync(next, scope, requestedAt = performance.now()) {
            if (!next) return;
            const rival = transport && (transport.lesson !== scope.lesson || transport.epoch !== scope.epoch || transport.sid !== scope.sid);
            if (!('doc' in next)) {
                // Compact deltas carry no document metadata; never borrow metadata from a different SID or page.
                if (rival || !data || data.rev !== next.rev) return;
                next = {...next,doc:data.doc,page:data.page,docs:data.docs,ops:next.ops || []};
            }
            const resetPage = next.reset || !data || next.rev !== data.rev || rival;
            const reloadDoc = rival || next.doc?.id !== data?.doc?.id;
            const changedPaper = !data || reloadDoc || next.page !== data.page;
            const catchingUp = data?.more || next.more;
            const revoked = !staff && data?.write === true && next.write !== true;
            const wasNavigating = controlsView();
            const shared = checkedView(next.view), viewChanged = !sameView(shared,framing);
            if (resetPage) wipeInk();
            else if (revoked) {
                // Keep confirmed ink; discard unsent strokes and make any old ACK harmless.
                generation++;
                clearTimeout(writeTimer); writeTimer = undefined;
                pending = []; sent = []; pointerEnd();
            }
            transport = scope;
            data = next;
            framing = shared;
            const restoringView = controlsView() && (changedPaper || !wasNavigating);
            if (restoringView) zoomLevel = framing.zoom/100;
            if (!controlsView() && (wasNavigating || viewChanged)) {
                // The coordinate transform must not change underneath a continuous pen path.
                stopGestures();
                if (tool === 'pan') setTool('pen');
            }
            cursor = next.cursor;
            const dropped = prune();
            receive(next.ops);
            if (removed.has(stroke?.style?.id)) pointerEnd();
            for (const [id,cue] of labels) if (removed.has(cue.id)) {
                clearTimeout(cue.timer); cue.label.remove(); labels.delete(id);
            }
            showRemoteLaser(next.rev < targetRev ? null : next.laser, requestedAt);
            if (reloadDoc) clearDocument();
            if (changedPaper) {
                blankPaper(next.doc);
                show(next,scope);
            } else if (resetPage || revoked || dropped || (next.ops.length && (pending.length || sent.length || next.ops.some(op => op.tool === 'erase-stroke')))) {
                redraw();
                if (!resetPage && !catchingUp && !document.hidden) lastWriters(next.ops);
            }
            else if (next.ops.length) presentInk(next, catchingUp);
            if (!changedPaper && (wasNavigating !== controlsView() || (!controlsView() && viewChanged))) fitView();
            if (restoringView) {
                const [l,t,r,b] = framing.crop;
                focusAt([(l+r)/20000,(t+b)/20000]);
            }
            const caption = next.doc ? pageNumber.format(next.page) : '';
            if (el('board-page-label').textContent !== caption) el('board-page-label').textContent = caption;
            el('board-footer').hidden = !caption;
            if (controlsView()) {
                const stamp = next.docs.map(d => d.id).join(',');
                const select = el('board-doc');
                if (stamp !== docsKey) {
                    docsKey = stamp;
                    const blank = document.createElement('option'); blank.value=''; blank.textContent='تختهٔ خالی';
                    select.replaceChildren(blank,...next.docs.map(d => {
                        const opt = document.createElement('option'); opt.value=d.id; opt.textContent=d.name; return opt;
                    }));
                }
                select.value = next.doc?.id || '';
                if (resetPage || document.activeElement !== el('page-number')) el('page-number').value = String(next.page);
            }
            toolState();
        }
        function reset() {
            data = transport = null; zoomLevel = 1; framing = fullView;
            cancelAnimationFrame(viewFrame); viewFrame = undefined;
            wipeInk(); clearDocument(); fileRetry.hidden = true;
            stage.classList.remove('has-document');
            fit(1600,1000);
            if (card.classList.contains('expanded')) setExpanded(false);
            el('board-page-label').textContent = ''; el('board-footer').hidden = true; toolState();
        }
        loadFile.addEventListener('click', () => {
            if (!data?.doc || !transport || !options.canEdit() || fileRetry.hidden) return;
            show(data,transport);
        });
        function setExpanded(expand) {
            const anchor = controlsView() ? relative(center()) : null;
            stopGestures();
            card.classList.toggle('expanded',expand);
            options.onExpand(expand);
            for (const node of root.querySelectorAll('.room-top, .staff-actions, .receive-tools, .screen-panel, .speaker-strip, .camera-strip, .room-side')) node.inert = expand;
            const btn = el('board-expand');
            btn.setAttribute('aria-pressed',String(expand));
            btn.setAttribute('aria-label', expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته');
            btn.title = expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته';
            fitView();
            if (anchor) focusAt(anchor);
            if (expand) btn.focus({preventScroll:true});
        }
        el('board-expand').addEventListener('click', () => setExpanded(!card.classList.contains('expanded')));
        card.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            const wakeControl = el('wake-control');
            if (wakeControl?.open) { wakeControl.open = false; wakeControl.querySelector('summary').focus(); }
            else setExpanded(false);
        });
        zoom?.addEventListener('click', () => setZoom(1));
        zoomOut?.addEventListener('click', () => stepZoom(-1));
        zoomIn?.addEventListener('click', () => stepZoom(1));
        if (!options.rasterOnly) {
            if ('ResizeObserver' in window) new ResizeObserver(fitView).observe(viewport);
            else window.addEventListener('resize', fitView);
            viewport.addEventListener('scroll', queueView, {passive:true});
        }
        fitView();

        if (staff) {
            const files = el('board-files'), toggle = el('toggle-files');
            toggle.addEventListener('click', () => {
                if (!canNavigate()) return;
                files.hidden = !files.hidden;
                toggle.setAttribute('aria-expanded', String(!files.hidden));
            });
        }
        function setTool(value) {
            if (value === 'pan' && !canNavigate()) return;
            pointerEnd();
            tool = value;
            el('pen').setAttribute('aria-pressed',String(value==='pen'));
            el('eraser').setAttribute('aria-pressed',String(value==='erase'));
            el('board-laser')?.setAttribute('aria-pressed',String(value==='laser'));
            el('pan')?.setAttribute('aria-pressed',String(value==='pan'));
            stage.classList.toggle('pan',value==='pan');
            toolState();
        }
        function xy(event, r) {
            const limit = n => Math.round(Math.max(0,Math.min(1,n))*10000);
            return [limit((event.clientX-r.left)/r.width),limit((event.clientY-r.top)/r.height)];
        }
        function aim(point, type) {
            tip.hidden = false;
            tip.classList.toggle('touch',type === 'touch');
            tip.style.left = `${point[0]/100}%`;
            tip.style.top = `${point[1]/100}%`;
        }
        function erase(a, b, rect, type) {
            const draft = drafts();
            const hidden = collectRemoved(new Set(removed), draft);
            let changed = false;
            for (const op of [...ops,...draft]) {
                if (op.tool !== 'pen' || !op.id || (!staff && (!data.owner || !op.id.startsWith(data.owner))) || hidden.has(op.id) || !touches(op,a,b,rect,type==='touch' ? 19 : 14)) continue;
                let tail = last(pending);
                if (tail?.tool !== 'erase-stroke' || tail.targets.length >= eraseSize || (stroke.erasures && !stroke.erasures.has(tail))) {
                    if (!queuedSlot()) break;
                    tail = {tool:'erase-stroke',targets:[]};
                    pending.push(tail);
                    stroke.erasures?.add(tail);
                }
                tail.targets.push(op.id);
                hidden.add(op.id);
                changed = true;
            }
            if (changed) { redraw(); scheduleWrite(); }
        }
        function laserPoint(point) {
            const now = performance.now(), samples = localLaser.samples;
            while (samples[0]?.[2] <= now) samples.shift();
            const end = [...point, now+laserTtl];
            // Retain the start of even a very short sweep; replace its newest point within each sample interval.
            if (samples.length >= 2 && end[2] - samples[samples.length-2][2] < laserSampleMs) samples[samples.length-1] = end;
            else { samples.push(end); if (samples.length > laserLimit) samples.shift(); }
            if (!aimPending) options.aimSoon();
            aimPending = {at:now, rev:data.rev};
            animateLaser();
        }
        function takeAim() {
            const item = aimPending;
            aimPending = null;
            const now = performance.now();
            if (!staff || !editable() || item?.rev !== data.rev || now-item.at > 850) return {};
            const points = localLaser.samples.filter(([, , expires]) => expires > now)
                .map(([x,y,expires]) => [x,y,Math.max(0,Math.floor(now + laserTtl - expires))]);
            return points.length ? {aim:points} : {};
        }
        function beginStroke(id, type, point, rect, mode = tool) {
            if (!editable() || pointer !== null || mode === 'pan' || (mode === 'pen' && !queuedSlot())) return;
            stopPlayback(true);
            pointer = id; pointerType = type;
            stroke = {last:point,mode};
            if (mode === 'erase') {
                if (type === 'touch') stroke.erasures = new Set();
                aim(point,type); erase(point,point,rect,type);
            } else if (mode === 'laser') {
                localLaser.samples = []; // A new gesture must never bridge to the end of the previous one.
                laserPoint(point);
            } else {
                const raw = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2,'0')).join('');
                const id = data.owner ? data.owner + raw.slice(16) : raw;
                stroke.style = {tool:'pen',id,color:el('ink-color').value,size:Number(el('ink-size').value)};
                const op = {...stroke.style,points:[point]};
                pending.push(op); paint(op); scheduleWrite();
            }
        }
        function penPoint(point) {
            const before = stroke.last, style = stroke.style;
            if (Math.hypot(point[0]-before[0],point[1]-before[1]) < 12) return;
            let tail = last(pending);
            if (tail?.id !== style.id || tail.points.length >= packetSize) {
                if (!queuedSlot()) return;
                tail = {...style,points:[before]};
                pending.push(tail);
            }
            tail.points.push(point); bounds.delete(tail);
            stroke.last = point;
            paint({...style,points:[before,point]}); scheduleWrite();
        }
        function inkPoint(point, rect) {
            if (!stroke) return;
            if (stroke.mode === 'erase') {
                const before = stroke.last; stroke.last = point;
                aim(point,pointerType);
                if (!samePoint(before,point)) erase(before,point,rect,pointerType);
            } else if (stroke.mode === 'laser') {
                if (!samePoint(stroke.last,point)) { stroke.last = point; laserPoint(point); }
            } else penPoint(point);
        }
        function beginDrag(event) {
            const [x,y] = at(event);
            drag = {id:event.pointerId,x:x+viewport.scrollLeft,y:y+viewport.scrollTop};
            dragState();
        }
        function moveDrag(event) {
            if (!canNavigate()) return;
            viewport.scrollLeft = drag.x-event.clientX;
            viewport.scrollTop = drag.y-event.clientY;
        }
        function pair(ids) {
            const [a,b] = ids.map(id => contacts.get(id));
            return {point:[(a[0]+b[0])/2,(a[1]+b[1])/2], distance:Math.max(12,Math.hypot(a[0]-b[0],a[1]-b[1]))};
        }
        function beginPinch() {
            cancelAnimationFrame(pinchFrame); pinchFrame = undefined;
            drag = null; touchBlocked = true;
            abandonUnsentTouch(); pointerEnd(); aimPending = null; clearTrail(localLaser);
            if (canNavigate()) {
                const ids = [...contacts.keys()].slice(0,2), sample = pair(ids);
                pinch = {ids, level:zoomLevel, distance:sample.distance, anchor:relative(sample.point), zooming:false};
            }
            dragState();
        }
        function movePinch() {
            if (pinchFrame !== undefined) return;
            pinchFrame = requestAnimationFrame(() => {
                pinchFrame = undefined;
                if (!pinch) return;
                const sample = pair(pinch.ids);
                const ratio = sample.distance / pinch.distance;
                pinch.zooming ||= Math.abs(ratio-1) > .035; // Two-finger panning shouldn't jitter the scale.
                setZoom(pinch.level * (pinch.zooming ? ratio : 1), sample.point, pinch.anchor);
            });
        }
        function drawingTarget(event) { return event.target === ink; }
        function grab(event) {
            const target = event.target?.setPointerCapture ? event.target : viewport;
            target.setPointerCapture?.(event.pointerId);
            event.preventDefault();
        }
        function start(event) {
            const type = event.pointerType, id = event.pointerId;
            if (type === 'touch') {
                if (pointerType === 'pen' || performance.now() < penUntil || (!editable() && !canNavigate())) return;
                if (!contacts.size) touchBlocked = false;
                contacts.set(id,at(event)); grab(event);
                if (!pinch && contacts.size >= 2 && pointerType !== 'pen') { beginPinch(); return; }
                if (touchBlocked || pointer !== null) return;
                if (tool === 'pan' || !drawingTarget(event)) { if (canDrag()) beginDrag(event); return; }
                const rect = ink.getBoundingClientRect();
                if (!rect.width || !rect.height) return;
                if (tool === 'laser') beginStroke(id,type,xy(event,rect),rect);
                else candidate = {id, at:performance.now(), start:at(event), first:xy(event,rect), trail:[]};
                return;
            }
            const buttonErase = type === 'pen' && (event.button === 5 || (event.buttons & 32) !== 0);
            if (event.button !== 0 && event.button !== 1 && !buttonErase) return;
            if (type === 'pen') {
                penUntil = performance.now() + 420;
                if (contacts.size && pointerType !== 'pen') stopGestures();
            }
            if (event.button === 1 || space || !editable() || tool === 'pan' || !drawingTarget(event)) {
                if (canDrag() && pointer === null) { grab(event); beginDrag(event); }
                else if (event.button === 1) event.preventDefault();
                return;
            }
            const rect = ink.getBoundingClientRect();
            if (pointer !== null || !rect.width || !rect.height) return;
            grab(event);
            beginStroke(id,type,xy(event,rect),rect,buttonErase ? 'erase' : tool);
        }
        function commitTouch(id, rect) {
            if (!candidate || candidate.id !== id) return;
            const {first,trail} = candidate;
            candidate = null;
            beginStroke(id,'touch',first,rect);
            for (const point of trail) inkPoint(point,rect);
        }
        function move(event) {
            const id = event.pointerId, type = event.pointerType;
            if (type === 'touch') {
                if (!contacts.has(id)) return;
                contacts.set(id,at(event));
                if (pinch) { if (pinch.ids.includes(id)) movePinch(); return; }
                if (touchBlocked || pointerType === 'pen') return;
            }
            if (drag?.id === id) { moveDrag(event); return; }
            if (!editable() || tool === 'pan') return;
            if (type === 'pen') penUntil = performance.now() + 420;
            if (pointer !== id && candidate?.id !== id && !(type !== 'touch' && tool === 'erase' && drawingTarget(event))) return;
            const rect = ink.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            if (candidate?.id === id) {
                const [x,y] = candidate.start;
                if (Math.hypot(event.clientX-x,event.clientY-y) < 6 && !candidate.trail.length) return;
                if (candidate.trail.length < 12) candidate.trail.push(xy(event,rect));
                if (performance.now() - candidate.at >= 120) commitTouch(id,rect);
            }
            if (pointer === id && stroke) {
                const samples = event.getCoalescedEvents?.();
                for (const sample of samples?.length ? samples : [event]) inkPoint(xy(sample,rect),rect);
            } else if (type !== 'touch' && tool === 'erase' && drawingTarget(event)) aim(xy(event,rect),type);
        }
        function done(event) {
            const id = event.pointerId;
            if (event.pointerType === 'pen') penUntil = performance.now() + 420;
            if (event.type === 'pointerup' && !touchBlocked && candidate?.id === id && editable()) {
                const rect = ink.getBoundingClientRect();
                if (rect.width && rect.height) commitTouch(id,rect);
            }
            if (pointer === id) {
                if (event.type === 'pointerup') move(event);
                pointerEnd();
            }
            if (candidate?.id === id) candidate = null;
            if (drag?.id === id) { drag = null; dragState(); }
            if (!contacts.delete(id)) return;
            if (pinch?.ids.includes(id)) {
                cancelAnimationFrame(pinchFrame); pinchFrame = undefined;
                pinch = null;
                if (contacts.size >= 2) beginPinch();
                else dragState();
            }
            if (!contacts.size) touchBlocked = false;
        }
        viewport.addEventListener('pointerdown',start);
        viewport.addEventListener('pointermove',move);
        for (const type of ['pointerup','pointercancel','lostpointercapture']) viewport.addEventListener(type,done);
        viewport.addEventListener('pointerleave',() => { if (pointer === null) tip.hidden = true; });
        viewport.addEventListener('wheel',event => {
            if (!event.ctrlKey || !event.deltaY) return;
            event.preventDefault();
            setZoom(zoomLevel*Math.exp(clamp(-event.deltaY*.003,-.22,.22)),at(event));
        }, {passive:false});
        viewport.addEventListener('keydown',event => {
            if (!canNavigate()) return;
            if (event.code === 'Space') { event.preventDefault(); space = true; dragState(); }
            if (event.key === '+' || event.key === '=') { event.preventDefault(); stepZoom(1); }
            if (event.key === '-') { event.preventDefault(); stepZoom(-1); }
        });
        viewport.addEventListener('keyup',event => { if (event.code === 'Space') { space = false; dragState(); } });
        viewport.addEventListener('blur',() => { space = false; dragState(); });
        document.addEventListener('visibilitychange',() => { if (document.hidden) { stopGestures(); stopPlayback(true); clearLabels(); clearPointers(); } });
        el('pen').addEventListener('click',() => setTool('pen'));
        el('eraser').addEventListener('click',() => setTool('erase'));
        el('board-laser')?.addEventListener('click',() => setTool('laser'));
        el('pan')?.addEventListener('click',() => setTool('pan'));
        if (staff) {
            async function act(work, navigate = false) {
                if (!editable() || (navigate && !canNavigate())) return;
                const stamp = generation;
                busy = true; pointerEnd(); aimPending = null; clearTrail(localLaser); toolState();
                try {
                    // Commit the last drawn points on this page before changing the revision.
                    while (stamp === generation && options.canEdit() && (writing || pending.length)) await (writing || write());
                    const current = () => stamp === generation && options.canEdit() && (!navigate || controlsView());
                    if (!current()) return;
                    const result = await work(current);
                    if (stamp === generation && result?.boardRev) targetRev = result.boardRev;
                } catch (e) { if (stamp === generation) message(options.explain(e)); }
                finally { busy = false; toolState(); scheduleWrite(); }
            }
            function page(doc,n) {
                act(() => options.send('board-page',{board:data.rev,doc,page:n}), true);
            }
            el('page-prev').addEventListener('click',() => page(data?.doc?.id || null,data.page-1));
            el('page-next').addEventListener('click',() => page(data?.doc?.id || null,data.page+1));
            el('page-go').addEventListener('click',() => page(data?.doc?.id || null,Number(el('page-number').value)));
            el('board-doc').addEventListener('change',e => page(e.target.value||null,1));
            el('board-clear').addEventListener('click',() => {
                if (window.confirm('فقط نوشته‌های همین صفحه پاک شود؟')) act(() => options.send('board-clear',{board:data.rev}));
            });
            el('board-upload').addEventListener('submit',event => {
                event.preventDefault();
                act(async current => {
                    const field = el('board-file'), file = field.files[0];
                    if (!file) return;
                    if (file.size > 16*1024*1024) { message('حداکثر حجم هر فایل ۱۶ مگابایت است.'); return; }
                    const rev = data.rev;
                    let pages = 1;
                    if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
                        const pdf = await pdfLib();
                        const loading = pdf.getDocument({data:new Uint8Array(await file.arrayBuffer()),...pdfOptions()});
                        try { pages = (await loading.promise).numPages; }
                        finally { await loading.destroy(); }
                    }
                    if (!current()) return;
                    const result = await options.send('upload',{board:rev,pages},file);
                    field.value = '';
                    return result;
                }, true);
            });
        }
        toolState();
        return {position:() => ({board:data?.rev || 0,ink:cursor}),framing:() => framing,takeView,takeAim,sync,reset};
    };
})();
