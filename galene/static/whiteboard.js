// Copyright (c) 2024.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.

'use strict';

// Whiteboard is a lightweight, real-time collaborative whiteboard.
//
// Strokes are relayed through Galene's usermessage channel (kind
// 'whiteboard'), so no extra server infrastructure is needed.  Points
// are stored in normalised coordinates (0..1) so the board scales to
// any canvas size.  Only users holding the 'whiteboard' permission can
// draw; everyone else views.

const Whiteboard = (() => {
    let canvas = null;
    let ctx = null;
    let visible = false;
    let drawing = false;
    let currentStroke = null;
    let canDraw = false;
    let strokes = new Map(); // id -> {color, width, points}
    let lastSend = 0;

    const COLOR = '#1a1a1a';
    const WIDTH = 0.004; // fraction of canvas width

    function $(id) { return document.getElementById(id); }

    function init() {
        canvas = $('whiteboard-canvas');
        if(!canvas) return;
        ctx = canvas.getContext('2d');
        resize();

        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointerleave', onUp);

        const btn = $('whiteboard-button');
        if(btn) btn.addEventListener('click', toggle);

        const clearBtn = $('whiteboard-clear');
        if(clearBtn) clearBtn.addEventListener('click', clearBoard);

        const closeBtn = $('whiteboard-close');
        if(closeBtn) closeBtn.addEventListener('click', toggle);

        window.addEventListener('resize', () => {
            if(visible) { resize(); redraw(); }
        });
    }

    function resize() {
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.max(1, Math.floor(rect.width * dpr));
        canvas.height = Math.max(1, Math.floor(rect.height * dpr));
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function toggle() {
        visible = !visible;
        const overlay = $('whiteboard-overlay');
        if(overlay) overlay.classList.toggle('invisible', !visible);
        if(visible) {
            resize();
            redraw();
        }
    }

    function setCanDraw(v) {
        canDraw = v;
        const toolbar = $('whiteboard-toolbar');
        if(toolbar) toolbar.classList.toggle('invisible', !v);
    }

    function point(e) {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        return [x, y];
    }

    function onDown(e) {
        if(!canDraw || !visible) return;
        drawing = true;
        canvas.setPointerCapture(e.pointerId);
        const id = Math.random().toString(36).slice(2, 10);
        currentStroke = { id, color: COLOR, width: WIDTH, points: [point(e)] };
        strokes.set(id, { color: COLOR, width: WIDTH, points: [point(e)] });
        drawStroke(currentStroke);
        sendStroke(currentStroke);
        lastSend = performance.now();
    }

    function onMove(e) {
        if(!drawing || !currentStroke) return;
        const p = point(e);
        currentStroke.points.push(p);
        const stored = strokes.get(currentStroke.id);
        if(stored) stored.points.push(p);
        drawStroke({ ...currentStroke, points: [currentStroke.points[currentStroke.points.length - 2], p] });
        const now = performance.now();
        if(now - lastSend > 33) { // ~30 fps
            sendStroke(currentStroke);
            currentStroke = { ...currentStroke, points: [p] };
            lastSend = now;
        }
    }

    function onUp() {
        if(!drawing) return;
        drawing = false;
        // Only send if there is at least one new segment: a batch
        // holding a single point carries nothing the peers don't
        // already have, since the previous batch ended on that point.
        if(currentStroke && currentStroke.points.length > 1) {
            sendStroke(currentStroke);
        }
        currentStroke = null;
    }

    function sendStroke(stroke) {
        if(!window.serverConnection) return;
        window.serverConnection.userMessage('whiteboard', '', stroke, true);
    }

    function drawStroke(stroke) {
        if(!ctx || !stroke || !stroke.points || stroke.points.length === 0) return;
        const rect = canvas.getBoundingClientRect();
        ctx.strokeStyle = stroke.color || COLOR;
        ctx.lineWidth = (stroke.width || WIDTH) * rect.width;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        const pts = stroke.points;
        if(pts.length === 1) {
            ctx.moveTo(pts[0][0] * rect.width, pts[0][1] * rect.height);
            ctx.lineTo(pts[0][0] * rect.width + 0.1, pts[0][1] * rect.height + 0.1);
        } else {
            ctx.moveTo(pts[0][0] * rect.width, pts[0][1] * rect.height);
            for(let i = 1; i < pts.length; i++) {
                ctx.lineTo(pts[i][0] * rect.width, pts[i][1] * rect.height);
            }
        }
        ctx.stroke();
    }

    function redraw() {
        if(!ctx) return;
        const rect = canvas.getBoundingClientRect();
        ctx.clearRect(0, 0, rect.width, rect.height);
        for(const stroke of strokes.values()) {
            drawStroke(stroke);
        }
    }

    function clearBoard() {
        if(!canDraw) return;
        strokes.clear();
        redraw();
        if(window.serverConnection) {
            window.serverConnection.userMessage('whiteboard-clear', '', null, true);
        }
    }

    // Called from gotUserMessage for kind 'whiteboard'.
    function onStroke(stroke) {
        if(!stroke || !stroke.id) return;
        let stored = strokes.get(stroke.id);
        if(!stored) {
            stored = { color: stroke.color, width: stroke.width, points: [] };
            strokes.set(stroke.id, stored);
        }
        if(stroke.points) {
            for(const p of stroke.points) stored.points.push(p);
        }
        if(visible) drawStroke(stroke);
    }

    // Called from gotUserMessage for kind 'whiteboard-state'.
    function onState(list) {
        if(!Array.isArray(list)) return;
        strokes.clear();
        for(const stroke of list) {
            if(stroke && stroke.id) {
                strokes.set(stroke.id, {
                    color: stroke.color,
                    width: stroke.width,
                    points: stroke.points || [],
                });
            }
        }
        if(visible) redraw();
    }

    // Called from gotUserMessage for kind 'whiteboard-clear'.
    function onClear() {
        strokes.clear();
        if(visible) redraw();
    }

    return { init, toggle, setCanDraw, onStroke, onState, onClear };
})();

// Expose for galene.js
window.Whiteboard = Whiteboard;
