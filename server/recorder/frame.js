(() => {
    'use strict';
    const font = 'ArchiveVazir, sans-serif';
    const cut = new Intl.Segmenter('fa',{granularity:'grapheme'});
    const brand = 'کلاس مجازی', credit = 'طراحی و توسعه : س. مهدی بیات';
    const roles = {teacher:'دبیر', admin:'مدیر'};
    const percent = new Intl.NumberFormat('fa-IR',{style:'percent'});
    const margin = 24, gap = 12, top = 68, footer = 22, galleryHeight = 166;

    // Draw only onto the archival canvas. Presentation content and cameras never share a region.
    window.ArchiveFrame = canvas => {
        const ctx = canvas.getContext('2d', {alpha:false});
        const {width,height} = canvas;
        const galleryY = height - footer - galleryHeight;
        const tiles = {x:margin+12,y:galleryY+12,w:width-2*margin-178,h:galleryHeight-24};
        const background = ctx.createLinearGradient(0,0,width,height);
        background.addColorStop(0,'#17243b'); background.addColorStop(1,'#0a1325');

        function box(x,y,w,h,r,fill,stroke) {
            ctx.beginPath(); ctx.roundRect(x,y,w,h,r);
            ctx.fillStyle = fill; ctx.fill();
            if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
        }
        const face = (size,weight) => `${weight} ${size}px ${font}`;
        function measure(text,size,weight) {
            ctx.font = face(size,weight);
            return ctx.measureText(text).width;
        }
        function label(text,x,y,{size=16,color='#e6efff',weight=400,align='right'}={}) {
            ctx.font = face(size,weight);
            ctx.direction = 'rtl'; ctx.textAlign = align; ctx.textBaseline = 'middle';
            ctx.fillStyle = color; ctx.fillText(text,x,y);
        }
        function dot(x,y,color) {
            ctx.beginPath(); ctx.arc(x,y,3.5,0,Math.PI*2); ctx.fillStyle = color; ctx.fill();
        }
        function line(x,y,w,color) {
            ctx.fillStyle = color; ctx.fillRect(x,y,w,1);
        }
        function shorten(text,max,size,weight) {
            if (measure(text,size,weight) <= max) return text;
            const parts = Array.from(cut.segment(text), hit => hit.segment);
            let lo = 0, hi = parts.length;
            while (lo < hi) {
                const mid = Math.ceil((lo + hi)/2);
                if (measure(parts.slice(0,mid).join('').trimEnd()+'…',size,weight) <= max) lo = mid;
                else hi = mid - 1;
            }
            return parts.slice(0,lo).join('').trimEnd()+'…';
        }
        let headerSource, headerLayout;
        function header(heading) {
            // Compute widths once per capture. Keep both names readable even if one is long.
            if (!headerLayout || heading !== headerSource) {
                headerSource = heading;
                const school = typeof heading?.school === 'string' ? heading.school.trim() : '';
                const subject = typeof heading?.subject === 'string' ? heading.subject.trim() : '';
                const creditWidth = Math.ceil(measure(credit,16,400)) + 32;
                const right = width-margin-48, split = 24, brandWidth = measure(brand,24,700);
                const room = right - (margin + creditWidth + 28) - brandWidth - split*(Number(!!school)+Number(!!subject));
                const schoolRoom = school ? Math.min(measure(school,22,700), subject ? Math.max(room/2,room-measure(subject,21,700)) : room) : 0;
                const titles = [
                    [shorten(school,schoolRoom,22,700),22,'#f2f7ff'],
                    [brand,24,'#d9eeff'],
                    [shorten(subject,room-schoolRoom,21,700),21,'#bfe4ff'],
                ].filter(([text])=>text);
                let x = right;
                const labels = titles.map(([text,size,color],i) => {
                    const mark = i ? x - split/2 : null;
                    if (i) x -= split;
                    const entry = {text,size,color,x,mark};
                    x -= measure(text,size,700);
                    return entry;
                });
                headerLayout = {labels,creditWidth};
            }
            line(margin,55,width-2*margin,'#304460');
            box(width-margin-34,12,34,34,9,'#1d4a53');
            box(width-margin-26,20,18,12,2,'#7defd9');
            line(width-margin-22,24,10,'#1d4a53'); line(width-margin-22,27,6,'#1d4a53');
            const {labels,creditWidth} = headerLayout;
            box(margin,13,creditWidth,32,16,'#1f3450');
            label(credit,margin+creditWidth/2,29,{color:'#bfe4ff',size:16,align:'center'});
            for (const {text,size,color,x,mark} of labels) {
                if (mark !== null) dot(mark,29,'#7ba0be');
                label(text,x,29,{size,weight:700,color});
            }
        }
        function fit(sw,sh,{x,y,w,h}) {
            const scale = Math.min(w/sw,h/sh);
            const ww = sw*scale, hh = sh*scale;
            return [Math.round(x+(w-ww)/2),Math.round(y+(h-hh)/2),Math.round(ww),Math.round(hh)];
        }
        function pane({x,y,w,h},title,draw) {
            box(x,y,w,h,14,'#17243a','#2d4060');
            label(title,x+w-32,y+19,{size:17,weight:700});
            dot(x+w-17,y+19,'#66e1ca');
            line(x+12,y+37,w-24,'#2b3c58');
            const area = {x:x+10,y:y+46,w:w-20,h:h-56};
            // The matte keeps landscape and portrait sources intact, including text at the edges.
            box(area.x,area.y,area.w,area.h,8,'#0b1527');
            ctx.save(); ctx.beginPath(); ctx.roundRect(area.x,area.y,area.w,area.h,8); ctx.clip();
            draw(area);
            ctx.restore();
        }
        function drawLaser(laser,crop,dest) {
            if (!laser?.length) return;
            const now = performance.now(), samples = laser.filter(point => point[2] > now);
            if (!samples.length) return;
            const [left,top,right,bottom] = crop, [x,y,w,h] = dest;
            const point = ([a,b]) => [x+(a-left)*w/(right-left),y+(b-top)*h/(bottom-top)];
            ctx.save(); ctx.beginPath(); ctx.rect(x,y,w,h); ctx.clip();
            ctx.strokeStyle = '#ff493e'; ctx.lineWidth = 5; ctx.lineCap = 'round';
            for (let i=1; i<samples.length; i++) {
                const a = samples[i-1];
                ctx.globalAlpha = Math.min(1,(a[2]-now)/1200);
                ctx.beginPath(); ctx.moveTo(...point(a)); ctx.lineTo(...point(samples[i])); ctx.stroke();
            }
            const head = samples.at(-1), [hx,hy] = point(head);
            ctx.globalAlpha = Math.min(1,(head[2]-now)/500);
            ctx.lineWidth = 2.5;
            ctx.beginPath(); ctx.arc(hx,hy,9,0,Math.PI*2); ctx.stroke();
            dot(hx,hy,'#ff342c');
            ctx.restore();
        }
        function boardPane(rect,paper,ink,doc,hasInk,view,laser) {
            pane(rect,doc?.mime === 'application/pdf' ? 'سند کلاس · PDF' : 'تختهٔ کلاس',area => {
                if (!doc && !hasInk) {
                    const x = area.x + area.w/2, y = area.y + area.h/2;
                    box(x-28,y-57,56,48,10,'#19334a','#305775');
                    box(x-15,y-45,30,19,3,'#57c9bc');
                    line(x-10,y-39,20,'#19334a'); line(x-10,y-34,12,'#19334a');
                    label('تخته آمادهٔ نوشتن است',x,y+22,{size:20,weight:700,align:'center'});
                    if (!laser?.length) return;
                }
                // The PDF, ink and ephemeral laser share one crop; no laser is baked into the ink.
                const crop = view?.crop ?? [0,0,10000,10000], [l,t,r,b] = crop;
                const w = paper.width/10000, h = paper.height/10000;
                const src = [l*w,t*h,(r-l)*w,(b-t)*h];
                const scale = Math.min(1,(view?.zoom ?? 100)/100);
                const dest = fit(src[2],src[3],{x:area.x+area.w*(1-scale)/2,y:area.y+area.h*(1-scale)/2,w:area.w*scale,h:area.h*scale});
                if (doc || hasInk) {
                    ctx.drawImage(paper,...src,...dest); ctx.drawImage(ink,...src,...dest);
                }
                drawLaser(laser,crop,dest);
            });
            if (view && view.zoom !== 100 && (doc || hasInk)) {
                label(percent.format(view.zoom/100),rect.x+13,rect.y+19,{size:14,color:'#a5b7d1',align:'left'});
            }
        }
        function videoPane(rect,video,title) {
            pane(rect,title,area => ctx.drawImage(video,...fit(video.videoWidth,video.videoHeight,area)));
        }
        function cameraGrid(n) {
            // Fit all published cameras, with no paging, favoring fewer rows at equal size.
            const spacing = n > 12 ? 5 : 10;
            let best = {scale:0};
            for (let cols=1; cols<=n; cols++) {
                const rows = Math.ceil(n/cols);
                const h = Math.floor(Math.min((tiles.h-spacing*(rows-1))/rows, (tiles.w-spacing*(cols-1))/cols*9/16));
                if (h > best.scale) best = {scale:h,cols,rows,h,w:Math.floor(h*16/9),spacing};
            }
            return best;
        }
        function drawCameras(cameras) {
            const right = width-margin;
            box(margin,galleryY,width-2*margin,galleryHeight,14,'#141f35','#2d4060');
            ctx.fillStyle = '#2d4060'; ctx.fillRect(right-154,galleryY+14,1,galleryHeight-28);
            dot(right-24,galleryY+30,'#a99cf8');
            label('وب‌کم‌ها',right-38,galleryY+30,{size:17,weight:700});
            label('نمای کلاس',right-24,galleryY+58,{size:14,color:'#a5b7d1'});
            const {cols,rows,w,h,spacing} = cameraGrid(cameras.length);
            const startY = tiles.y + Math.floor((tiles.h-rows*h-(rows-1)*spacing)/2);
            cameras.forEach(({role,video},i) => {
                const row = Math.floor(i/cols), count = Math.min(cols,cameras.length-row*cols);
                const rowWidth = count*w+(count-1)*spacing;
                const x = tiles.x + Math.floor((tiles.w+rowWidth)/2) - w - (i%cols)*(w+spacing);
                const y = startY+row*(h+spacing), radius = Math.min(10,h/4);
                box(x,y,w,h,radius,'#0b1527',roles[role] ? '#56759e' : '#314563');
                ctx.save(); ctx.beginPath(); ctx.roundRect(x,y,w,h,radius); ctx.clip();
                ctx.drawImage(video,...fit(video.videoWidth,video.videoHeight,{x,y,w,h}));
                if (roles[role] && h >= 75) {
                    box(x+w-60,y+h-32,52,24,6,'#0b1527');
                    label(roles[role],x+w-34,y+h-20,{size:13,align:'center'});
                }
                ctx.restore();
            });
        }
        return ({paper,ink,doc,hasInk,view,laser,cameras=[],screen,teacher,archiveBoard,preferScreen,heading}) => {
            const shared = screen && (preferScreen || !teacher);
            const primary = shared ? screen : teacher;
            const gallery = cameras.filter(({video}) => video && video !== primary);
            const bottom = gallery.length ? galleryY-gap : height-footer;
            const presentation = {x:margin,y:top,w:width-2*margin,h:bottom-top};
            const drawBoard = rect => boardPane(rect,paper,ink,doc,hasInk,view,laser);
            const drawVideo = rect => videoPane(rect,primary,shared ? 'صفحهٔ مشترک' : 'دوربین دبیر');
            ctx.fillStyle = background; ctx.fillRect(0,0,width,height);
            header(heading);
            if (!primary) drawBoard(presentation);
            else if (archiveBoard) {
                const w = (presentation.w-gap)/2;
                drawVideo({...presentation,w});
                drawBoard({...presentation,x:presentation.x+w+gap,w});
            } else drawVideo(presentation);
            if (gallery.length) drawCameras(gallery);
        };
    };
})();
