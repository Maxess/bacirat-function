(() => {
    'use strict';
    window.ClassroomBoard = (root, options) => {
        const el = id => document.getElementById(id);
        const card = el('board'), stage = el('board-stage'), viewport = el('board-scroll');
        const paper = el('board-paper'), ink = el('board-ink');
        const context = ink.getContext('2d');
        const background = paper.getContext('2d', {alpha:false});
        const zoom = el('board-zoom'), zoomOut = el('board-zoom-out'), zoomIn = el('board-zoom-in');
        const zoomSteps = [.75, 1, 1.25, 1.5, 2, 2.5, 3];
        const percentage = new Intl.NumberFormat('fa-IR', {style:'percent'}), pageNumber = new Intl.NumberFormat('fa-IR');
        let zoomLevel = 1;
        let data = null, transport = null, cursor = 0, ops = [], stroke = null, pointer = null;
        let pending = [], sent = [], writing = null, writeTimer, generation = 0, targetRev = 0;
        const removed = new Set(), bounds = new WeakMap();
        const packetSize = 96, eraseSize = 32, batchSize = 6, queueLimit = 48, writeDelay = 175;
        const tip = el('eraser-tip');
        let tool = 'pen', busy = false, renderVersion = 0, documentVersion = 0, docKey = '', docsKey = '';
        let drag = null, pointerType = '', candidate = null, pinch = null, pinchFrame, touchBlocked = false, penUntil = 0, space = false;
        const contacts = new Map();
        const writers = el('board-writers'), labels = new Map();
        const localLaser = el('board-laser-local'), remoteLaser = el('board-laser-remote');
        let aimPending = null, localFade, remoteFade;
        let library, job, rendering, imageLoad, imageFile;
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
        function clearDocument() {
            cancelRender();
            documentVersion++;
            job?.destroy().catch(() => {}); job = null;
            imageLoad?.abort(); imageLoad = null;
            imageFile?.close?.(); imageFile = null;
            docKey = '';
        }
        function pdfLib() {
            if (!library) library = import(url(root.dataset.pdf)).then(lib => {
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
                isEvalSupported:false, maxImageSize:24_000_000, canvasMaxAreaInBytes:24_000_000,
            };
        }
        function documentURL(doc, scope) {
            const link = new URL(root.dataset.content, document.baseURI);
            for (const [key,val] of Object.entries({...scope, doc:doc.id})) link.searchParams.set(key,val);
            return link.href;
        }
        function fitView() {
            const w = viewport.clientWidth, h = viewport.clientHeight;
            if (!w || !h) return;
            const width = `${Math.max(1, Math.round(Math.min(w, h * paper.width / paper.height) * zoomLevel))}px`;
            if (stage.style.width !== width) stage.style.width = width;
            dragState();
        }
        function canDrag() { return viewport.scrollWidth > viewport.clientWidth || viewport.scrollHeight > viewport.clientHeight; }
        function dragState() { viewport.dataset.drag = drag || pinch ? 'moving' : canDrag() && (!canWrite() || tool === 'pan' || space) ? 'ready' : 'no'; }
        const clamp = (n, min=0, max=1) => Math.max(min, Math.min(max,n));
        const at = event => [event.clientX,event.clientY];
        function center() {
            const box = viewport.getBoundingClientRect();
            return [box.left + viewport.clientWidth/2, box.top + viewport.clientHeight/2];
        }
        function relative(point) {
            const box = stage.getBoundingClientRect();
            return [clamp((point[0]-box.left)/Math.max(1,box.width)),clamp((point[1]-box.top)/Math.max(1,box.height))];
        }
        function setZoom(level, point = center(), anchor = point && relative(point)) {
            zoomLevel = clamp(level, zoomSteps[0], zoomSteps[zoomSteps.length-1]);
            const value = percentage.format(zoomLevel);
            if (zoom.textContent !== value) zoom.textContent = value;
            zoomOut.disabled = zoomLevel <= zoomSteps[0];
            zoomIn.disabled = zoomLevel >= zoomSteps[zoomSteps.length-1];
            fitView();
            if (!point) return;
            const box = stage.getBoundingClientRect();
            const dx = box.left + anchor[0]*box.width - point[0], dy = box.top + anchor[1]*box.height - point[1];
            if (Math.abs(dx) >= .5) viewport.scrollLeft += dx;
            if (Math.abs(dy) >= .5) viewport.scrollTop += dy;
        }
        function stepZoom(direction) {
            const list = direction > 0 ? zoomSteps : [...zoomSteps].reverse();
            const next = list.find(n => direction*(n-zoomLevel) > .01);
            if (next !== undefined) setZoom(next);
        }
        function fit(width, height) {
            if (!(width > 0 && height > 0 && Number.isFinite(width/height))) throw new Error('Invalid board size');
            const scale = Math.min(widthLimit / Math.max(width,height), Math.sqrt(3000000 / (width*height)));
            ink.width = paper.width = Math.max(1, Math.ceil(width * scale));
            ink.height = paper.height = Math.max(1, Math.ceil(height * scale));
            stage.style.aspectRatio = `${paper.width} / ${paper.height}`;
            fitView();
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
        function fadeDot(node) { node.classList.add('fading'); }
        function pointDot(node, point) { locate(node,point); node.hidden = false; node.classList.remove('fading'); }
        function clearPointers() {
            aimPending = null; clearTimeout(localFade); clearTimeout(remoteFade);
            localLaser.hidden = remoteLaser.hidden = true;
        }
        function showRemoteLaser(aim) {
            clearTimeout(remoteFade);
            if (!aim || aim.by === options.identity() || document.hidden) { remoteLaser.hidden = true; return; }
            pointDot(remoteLaser,aim.xy);
            remoteFade = setTimeout(() => fadeDot(remoteLaser), Math.max(0,aim.ttl - 215));
        }
        function pointerEnd() {
            if (stroke?.mode === 'laser') {
                clearTimeout(localFade);
                localFade = setTimeout(() => fadeDot(localLaser), 410);
            }
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
        async function show(view, scope, reload) {
            if (reload) clearDocument();
            else cancelRender();
            const ticket = renderVersion;
            const docTicket = documentVersion;
            stage.classList.toggle('has-document', !!view.doc);
            if (!view.doc) { message(); return; }
            const doc = view.doc;
            docKey = doc.id;
            message('در حال نمایش فایل تخته…');
            try {
                const source = documentURL(doc,scope);
                if (doc.mime === 'application/pdf') {
                    const pdf = await pdfLib();
                    if (docTicket !== documentVersion || ticket !== renderVersion) return;
                    job ??= pdf.getDocument({url:source,disableAutoFetch:true,disableStream:true,...pdfOptions()});
                    const file = await job.promise;
                    if (view.page > file.numPages) throw new Error('Invalid PDF page');
                    const page = await file.getPage(view.page);
                    if (ticket !== renderVersion) return;
                    const base = page.getViewport({scale:1});
                    const scale = fit(base.width,base.height);
                    const task = page.render({canvas:paper,canvasContext:background,viewport:page.getViewport({scale}), annotationMode:pdf.AnnotationMode.DISABLE});
                    rendering = task;
                    await task.promise;
                    if (rendering === task) rendering = null;
                    page.cleanup();
                } else {
                    if (!imageFile) {
                        const ctrl = new AbortController(); imageLoad = ctrl;
                        const res = await fetch(source,{credentials:'same-origin',cache:'no-store',signal:ctrl.signal});
                        if (!res.ok) throw new Error('Image unavailable');
                        const blob = await res.blob();
                        const img = await decodeImage(blob);
                        if (docTicket !== documentVersion || ticket !== renderVersion) { img.close?.(); return; }
                        imageFile = img;
                    }
                    fit(imageFile.width,imageFile.height);
                    background.drawImage(imageFile,0,0,paper.width,paper.height);
                }
                if (ticket === renderVersion) message();
            } catch (err) {
                if (ticket === renderVersion && err.name !== 'RenderingCancelledException') message('نمایش این صفحه یا فایل ممکن نشد. فایل باید خوانا و بدون رمز باشد.');
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
            for (const id of ['pen','pan','eraser','ink-color','ink-size']) el(id).disabled = !ready;
            dragState();
            if (!staff) return;
            const pages = data?.doc?.pages || 1;
            const n = data?.page || 1;
            for (const id of ['board-laser','board-clear','board-file','board-doc','page-number','page-go']) el(id).disabled = !ready;
            el('board-upload').querySelector('button').disabled = !ready;
            el('page-number').max = String(pages);
            el('page-number').disabled = el('page-go').disabled = !ready || pages === 1;
            el('page-prev').disabled = !ready || n <= 1;
            el('page-next').disabled = !ready || n >= pages;
        }
        function sync(next, scope) {
            if (!next) return;
            const rival = transport && (transport.lesson !== scope.lesson || transport.epoch !== scope.epoch || transport.sid !== scope.sid);
            const resetPage = next.reset || !data || next.rev !== data.rev || rival;
            const reloadDoc = rival || (next.doc?.id || '') !== docKey;
            const changedPaper = !data || reloadDoc || next.page !== data.page;
            const catchingUp = data?.more || next.more;
            const revoked = !staff && data?.write === true && next.write !== true;
            if (resetPage) wipeInk();
            else if (revoked) {
                // Keep confirmed ink; discard unsent strokes and make any old ACK harmless.
                generation++;
                clearTimeout(writeTimer); writeTimer = undefined;
                pending = []; sent = []; pointerEnd();
            }
            transport = scope;
            data = next;
            cursor = next.cursor;
            const dropped = prune();
            receive(next.ops);
            if (removed.has(stroke?.style?.id)) pointerEnd();
            for (const [id,cue] of labels) if (removed.has(cue.id)) {
                clearTimeout(cue.timer); cue.label.remove(); labels.delete(id);
            }
            showRemoteLaser(next.laser);
            if (changedPaper) {
                // Do not briefly show a page from the previous document during parsing.
                fit(1600,1000);
                show(next,scope,reloadDoc);
            } else if (resetPage || revoked || dropped || (next.ops.length && (pending.length || sent.length || next.ops.some(op => op.tool === 'erase-stroke')))) {
                redraw();
                if (!resetPage && !catchingUp && !document.hidden) lastWriters(next.ops);
            }
            else if (next.ops.length) presentInk(next, catchingUp);
            const caption = next.doc ? pageNumber.format(next.page) : '';
            if (el('board-page-label').textContent !== caption) el('board-page-label').textContent = caption;
            el('board-footer').hidden = !caption;
            if (staff) {
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
            data = transport = null;
            wipeInk(); clearDocument();
            stage.classList.remove('has-document');
            fit(1600,1000);
            setZoom(1, null);
            if (card.classList.contains('expanded')) setExpanded(false);
            el('board-page-label').textContent = ''; el('board-footer').hidden = true; toolState();
        }
        function setExpanded(expand) {
            card.classList.toggle('expanded',expand);
            options.onExpand(expand);
            for (const node of root.querySelectorAll('.room-top, .staff-actions, .screen-panel, .speaker-strip, .camera-strip, .room-side')) node.inert = expand;
            const btn = el('board-expand');
            btn.setAttribute('aria-pressed',String(expand));
            btn.setAttribute('aria-label', expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته');
            btn.title = expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته';
            fitView();
            if (expand) btn.focus({preventScroll:true});
        }
        el('board-expand').addEventListener('click', () => setExpanded(!card.classList.contains('expanded')));
        card.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            if (el('wake-control').open) { el('wake-control').open = false; el('wake-control').querySelector('summary').focus(); }
            else setExpanded(false);
        });
        zoom.addEventListener('click', () => setZoom(1));
        zoomOut.addEventListener('click', () => stepZoom(-1));
        zoomIn.addEventListener('click', () => stepZoom(1));
        if ('ResizeObserver' in window) new ResizeObserver(fitView).observe(viewport);
        else window.addEventListener('resize', fitView);
        setZoom(1, null);

        if (staff) {
            const files = el('board-files'), toggle = el('toggle-files');
            toggle.addEventListener('click', () => {
                files.hidden = !files.hidden;
                toggle.setAttribute('aria-expanded', String(!files.hidden));
            });
        }
        function setTool(value) {
            pointerEnd();
            tool = value;
            el('pen').setAttribute('aria-pressed',String(value==='pen'));
            el('eraser').setAttribute('aria-pressed',String(value==='erase'));
            el('board-laser')?.setAttribute('aria-pressed',String(value==='laser'));
            el('pan').setAttribute('aria-pressed',String(value==='pan'));
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
            clearTimeout(localFade);
            pointDot(localLaser,point);
            if (!aimPending) options.aimSoon();
            aimPending = {point, at:performance.now(), rev:data.rev};
        }
        function takeAim() {
            const item = aimPending;
            aimPending = null;
            return staff && editable() && item?.rev === data.rev && performance.now()-item.at <= 850 ? {aim:item.point} : {};
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
            viewport.scrollLeft = drag.x-event.clientX;
            viewport.scrollTop = drag.y-event.clientY;
        }
        function pair(ids) {
            const [a,b] = ids.map(id => contacts.get(id));
            return {point:[(a[0]+b[0])/2,(a[1]+b[1])/2], distance:Math.max(12,Math.hypot(a[0]-b[0],a[1]-b[1]))};
        }
        function beginPinch() {
            cancelAnimationFrame(pinchFrame); pinchFrame = undefined;
            const ids = [...contacts.keys()].slice(0,2), sample = pair(ids);
            pinch = {ids, level:zoomLevel, distance:sample.distance, anchor:relative(sample.point), zooming:false};
            drag = null; touchBlocked = true;
            abandonUnsentTouch(); pointerEnd(); aimPending = null; dragState();
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
                if (pointerType === 'pen' || performance.now() < penUntil) return;
                if (!contacts.size) touchBlocked = false;
                contacts.set(id,at(event)); grab(event);
                if (!pinch && contacts.size >= 2 && pointerType !== 'pen') { beginPinch(); return; }
                if (touchBlocked || pointer !== null) return;
                if (!editable() || tool === 'pan' || !drawingTarget(event)) { beginDrag(event); return; }
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
        el('pan').addEventListener('click',() => setTool('pan'));
        if (staff) {
            async function act(work) {
                if (!editable()) return;
                const stamp = generation;
                busy = true; pointerEnd(); toolState();
                try {
                    // Commit the last drawn points on this page before changing the revision.
                    while (stamp === generation && options.canEdit() && (writing || pending.length)) await (writing || write());
                    if (stamp !== generation || !options.canEdit()) return;
                    const result = await work(() => stamp === generation && options.canEdit());
                    if (stamp === generation && result?.boardRev) targetRev = result.boardRev;
                } catch (e) { if (stamp === generation) message(options.explain(e)); }
                finally { busy = false; toolState(); scheduleWrite(); }
            }
            function page(doc,n) {
                act(() => options.send('board-page',{board:data.rev,doc,page:n}));
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
                });
            });
        }
        return {position:() => ({board:data?.rev || 0,ink:cursor}),takeAim,sync,reset};
    };
})();
