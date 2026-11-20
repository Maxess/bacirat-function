(() => {
    'use strict';
    window.ClassroomBoard = (root, options) => {
        const el = id => document.getElementById(id);
        const card = el('board'), stage = el('board-stage'), viewport = el('board-scroll');
        const paper = el('board-paper'), ink = el('board-ink');
        const context = ink.getContext('2d');
        const background = paper.getContext('2d', {alpha:false});
        const zoom = el('board-zoom'), zoomOut = el('board-zoom-out'), zoomIn = el('board-zoom-in');
        const zoomSteps = [.75, 1, 1.25, 1.5, 2, 2.5];
        const percentage = new Intl.NumberFormat('fa-IR', {style:'percent'});
        let zoomLevel = 1;
        let data = null, transport = null, cursor = 0, ops = [], draft = [], stroke = null, pointer = null;
        let tool = 'pen', busy = false, renderVersion = 0, documentVersion = 0, docKey = '', docsKey = '';
        let drag = null;
        let library, job, rendering, imageLoad, imageFile;
        const staff = root.dataset.moderator === 'yes';
        const url = value => new URL(value, document.baseURI).href;
        const widthLimit = 2048;

        function message(text = '') {
            el('board-note').textContent = text;
            el('board-note').hidden = !text;
        }
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
            if (w && h) {
                stage.style.width = `${Math.max(1, Math.floor(Math.min(w, h * paper.width / paper.height) * zoomSteps[zoomLevel]))}px`;
                dragState();
            }
        }
        function canDrag() {
            return (!staff || tool === 'pan') && (viewport.scrollWidth > viewport.clientWidth || viewport.scrollHeight > viewport.clientHeight);
        }
        function dragState() { viewport.dataset.drag = drag ? 'moving' : canDrag() ? 'ready' : 'no'; }
        viewport.addEventListener('pointerdown', event => {
            if (event.pointerType !== 'mouse' || event.button !== 0 || !canDrag()) return;
            event.preventDefault();
            drag = {id:event.pointerId, x:event.clientX + viewport.scrollLeft, y:event.clientY + viewport.scrollTop};
            viewport.setPointerCapture(event.pointerId);
            dragState();
        });
        viewport.addEventListener('pointermove', event => {
            if (drag?.id !== event.pointerId) return;
            viewport.scrollLeft = drag.x - event.clientX;
            viewport.scrollTop = drag.y - event.clientY;
        });
        for (const type of ['pointerup','pointercancel','lostpointercapture']) {
            viewport.addEventListener(type, event => { if (drag?.id === event.pointerId) { drag = null; dragState(); } });
        }
        function setZoom(level, keepCenter = true) {
            const x = (viewport.scrollLeft + viewport.clientWidth / 2) / Math.max(1, viewport.scrollWidth);
            const y = (viewport.scrollTop + viewport.clientHeight / 2) / Math.max(1, viewport.scrollHeight);
            zoomLevel = Math.max(0, Math.min(zoomSteps.length - 1, level));
            zoom.textContent = percentage.format(zoomSteps[zoomLevel]);
            zoomOut.disabled = zoomLevel === 0;
            zoomIn.disabled = zoomLevel === zoomSteps.length - 1;
            fitView();
            if (keepCenter) {
                viewport.scrollLeft = x * viewport.scrollWidth - viewport.clientWidth / 2;
                viewport.scrollTop = y * viewport.scrollHeight - viewport.clientHeight / 2;
            }
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
        function redraw() {
            context.clearRect(0,0,ink.width,ink.height);
            for (const op of ops) paint(op);
            for (const op of draft) paint(op);
            if (stroke) paint(stroke);
        }
        function pointerEnd() {
            const previous = pointer;
            pointer = null; stroke = null;
            if (previous !== null && ink.hasPointerCapture?.(previous)) ink.releasePointerCapture(previous);
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
            if (!staff) return;
            const ready = !!data && options.canEdit() && !busy;
            const pages = data?.doc?.pages || 1;
            const n = data?.page || 1;
            for (const id of ['pen','eraser','pan','ink-color','ink-size','board-clear','board-file','board-doc','page-number','page-go']) el(id).disabled = !ready;
            el('board-upload').querySelector('button').disabled = !ready;
            el('page-number').max = String(pages);
            el('page-number').disabled = el('page-go').disabled = !ready || pages === 1;
            el('page-prev').disabled = !ready || n <= 1;
            el('page-next').disabled = !ready || n >= pages;
        }
        function sync(next, scope) {
            if (!next) return;
            const rival = transport && (transport.epoch !== scope.epoch || transport.sid !== scope.sid);
            const resetPage = next.reset || !data || next.rev !== data.rev || rival;
            const reloadDoc = rival || (next.doc?.id || '') !== docKey;
            const changedPaper = !data || reloadDoc || next.page !== data.page;
            if (resetPage) { ops = []; draft = []; cursor = 0; pointerEnd(); }
            transport = scope;
            data = next;
            cursor = next.cursor;
            if (next.ops.length) ops.push(...next.ops);
            if (changedPaper) {
                // Do not briefly show a page from the previous document during parsing.
                fit(1600,1000);
                show(next,scope,reloadDoc);
            } else if (resetPage || (next.ops.length && (draft.length || stroke))) redraw();
            else for (const op of next.ops) paint(op);
            const caption = next.doc ? `${next.doc.name} · صفحهٔ ${next.page} از ${next.doc.pages}` : 'تختهٔ آزاد · همگام';
            if (el('board-caption').textContent !== caption) el('board-caption').textContent = caption;
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
                toolState();
            }
        }
        function reset() {
            data = transport = null;
            cursor = 0; ops = []; draft = []; pointerEnd(); clearDocument();
            stage.classList.remove('has-document');
            fit(1600,1000);
            setZoom(1, false);
            if (card.classList.contains('expanded')) setExpanded(false);
            el('board-caption').textContent = ''; message(); toolState();
        }
        function setExpanded(expand) {
            card.classList.toggle('expanded',expand);
            for (const node of root.querySelectorAll('.room-top, .session-strip, .room-side')) node.inert = expand;
            const btn = el('board-expand');
            btn.setAttribute('aria-pressed',String(expand));
            btn.setAttribute('aria-label', expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته');
            btn.title = expand ? 'بازگشت به کلاس' : 'نمای بزرگ تخته';
            fitView();
            if (expand) btn.focus({preventScroll:true});
        }
        el('board-expand').addEventListener('click', () => setExpanded(!card.classList.contains('expanded')));
        card.addEventListener('keydown', e => { if (e.key === 'Escape') setExpanded(false); });
        zoom.addEventListener('click', () => setZoom(1));
        zoomOut.addEventListener('click', () => setZoom(zoomLevel - 1));
        zoomIn.addEventListener('click', () => setZoom(zoomLevel + 1));
        if ('ResizeObserver' in window) new ResizeObserver(fitView).observe(viewport);
        else window.addEventListener('resize', fitView);
        setZoom(1, false);

        if (staff) {
            const files = el('board-files'), toggle = el('toggle-files');
            toggle.addEventListener('click', () => {
                files.hidden = !files.hidden;
                toggle.setAttribute('aria-expanded', String(!files.hidden));
            });
            let queue = Promise.resolve();
            function setTool(value) {
                tool = value;
                el('pen').setAttribute('aria-pressed',String(value==='pen'));
                el('eraser').setAttribute('aria-pressed',String(value==='erase'));
                el('pan').setAttribute('aria-pressed',String(value==='pan'));
                stage.classList.toggle('pan',value==='pan');
                dragState();
            }
            function xy(event) {
                const r = ink.getBoundingClientRect();
                const limit = n => Math.round(Math.max(0,Math.min(1,n))*10000);
                return [limit((event.clientX-r.left)/r.width),limit((event.clientY-r.top)/r.height)];
            }
            function begin(event) {
                if (tool === 'pan' || !options.canEdit() || !data || busy || pointer !== null || event.button !== 0) return;
                event.preventDefault();
                pointer = event.pointerId;
                ink.setPointerCapture(pointer);
                stroke = {tool, color:el('ink-color').value, size:Number(el('ink-size').value), points:[xy(event)]};
                paint(stroke);
            }
            function submitStroke(rev, epoch, ops) {
                queue = queue.then(async () => {
                    if (!options.canEdit() || data?.rev !== rev || epoch !== transport?.epoch) return;
                    await options.send('board-ink',{board:rev,strokes:ops});
                }).catch(e => message(options.explain(e))).finally(() => {
                    draft = draft.filter(d => !ops.includes(d));
                    redraw();
                });
            }
            function flush() {
                if (!stroke || !data || !transport) return;
                draft.push(stroke);
                submitStroke(data.rev,transport.epoch,[stroke]);
            }
            function move(event) {
                if (pointer !== event.pointerId || !stroke) return;
                const point = xy(event);
                const last = stroke.points[stroke.points.length-1];
                if (Math.hypot(point[0]-last[0],point[1]-last[1]) < 16) return;
                paint({...stroke,points:[last,point]});
                stroke.points.push(point);
                if (stroke.points.length >= 96) {
                    flush();
                    stroke = {...stroke,points:[point]};
                }
            }
            function done(event) {
                if (pointer !== event.pointerId || !stroke) return;
                if (event.type === 'pointerup') move(event);
                flush(); pointerEnd();
            }
            ink.addEventListener('pointerdown',begin);
            ink.addEventListener('pointermove',move);
            ink.addEventListener('pointerup',done);
            ink.addEventListener('pointercancel',done);
            ink.addEventListener('lostpointercapture',done);
            el('pen').addEventListener('click',() => setTool('pen'));
            el('eraser').addEventListener('click',() => setTool('erase'));
            el('pan').addEventListener('click',() => setTool('pan'));
            async function act(work) {
                if (!data || busy || !options.canEdit()) return;
                busy = true; toolState(); message();
                try { await work(); }
                catch (e) { message(options.explain(e)); }
                finally { busy = false; toolState(); }
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
                act(async () => {
                    const field = el('board-file'), file = field.files[0];
                    if (!file) return;
                    if (file.size > 16*1024*1024) { message('حداکثر حجم هر فایل ۱۶ مگابایت است.'); return; }
                    const rev = data.rev;
                    const current = transport.epoch;
                    let pages = 1;
                    if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
                        const pdf = await pdfLib();
                        const loading = pdf.getDocument({data:new Uint8Array(await file.arrayBuffer()),...pdfOptions()});
                        try { pages = (await loading.promise).numPages; }
                        finally { await loading.destroy(); }
                    }
                    if (data?.rev !== rev || transport?.epoch !== current) throw new Error('Board changed');
                    await options.send('upload',{board:rev,pages},file);
                    field.value = '';
                });
            });
        }
        return {position:() => ({board:data?.rev || 0,ink:cursor}),sync,reset};
    };
})();
