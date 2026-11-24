(() => {
    'use strict';
    const modes = ['live','idle','system'];
    const idleLimit = 5 * 60 * 1000;
    const isPupil = id => typeof id === 'string' && /^student-[1-9]\d{0,17}$/.test(id);
    const letters = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('fa', {granularity:'grapheme'}) : null;
    function shortMessage(text) {
        const line = text.replace(/\s+/gu,' ').trim();
        const units = letters ? [...letters.segment(line)].map(x => x.segment) : Array.from(line);
        return units.length > 60 ? units.slice(0,59).join('') + '…' : line;
    }

    window.ClassroomAttention = changeMode => {
        const el = id => document.getElementById(id);
        const modeField = el('wake-mode'), status = el('wake-status'), control = el('wake-control');
        const wake = window.isSecureContext ? window.navigator?.wakeLock : null;
        const supported = typeof wake?.request === 'function';
        const chat = {node:el('board-chat-toast'), time:6000, limit:2, queue:[]};
        const join = {node:el('board-join-toast'), time:3000, limit:3, queue:[]};
        const preview = el('board-chat-preview'), snippet = el('board-chat-snippet'), chatStatus = el('board-chat-status');
        const detail = el('board-chat-detail'), sender = el('board-chat-sender'), full = el('board-chat-full'), close = el('board-chat-close');
        let mode = 'live', live = false, expanded = false, known = null, chatReady = false;
        let lastInput = Date.now(), idleTimer, lock, pending = false, generation = 0, blockedUntil = 0, wanted = false;
        function release(sentinel) { if (sentinel && !sentinel.released) sentinel.release().catch(() => {}); }
        function indicator(now) {
            if (!control) return;
            const held = !!lock && !lock.released;
            control.dataset.awake = String(held);
            const text = !supported ? 'مرورگر این دستگاه قفل بیداری ندارد؛ انتخاب دبیر همچنان به دستگاه‌های سازگار می‌رسد.'
                : !live || document.hidden ? 'فقط در زنگ فعال و زبانهٔ قابل‌دیدن.'
                : mode === 'system' ? 'زمان خاموشی را تنظیمات دستگاه تعیین می‌کند.'
                : mode === 'idle' && now - lastInput >= idleLimit ? 'توقف پس از ۵ دقیقه؛ برای ادامه صفحه را لمس کن.'
                : held ? 'صفحه در این زبانه روشن می‌ماند.'
                : pending ? 'در انتظار اجازهٔ دستگاه…'
                : 'دستگاه اجازه نداده است؛ باتری یا تنظیمات گوشی را بررسی کن.';
            if (status.textContent !== text) status.textContent = text;
        }
        function reconcile(tryRequest = false) {
            const now = Date.now();
            const left = idleLimit - (now - lastInput);
            const eligible = supported && live && !document.hidden && mode !== 'system';
            if ((!eligible || mode !== 'idle') && idleTimer !== undefined) {
                clearTimeout(idleTimer); idleTimer = undefined;
            }
            if (eligible && mode === 'idle' && left > 0 && idleTimer === undefined) {
                idleTimer = setTimeout(() => { idleTimer = undefined; reconcile(); }, left);
            }
            const next = eligible && (mode !== 'idle' || left > 0);
            if (next !== wanted) { wanted = next; generation++; tryRequest = true; }
            if (!wanted && lock) { const old = lock; lock = null; release(old); }
            if (wanted && !lock && !pending && tryRequest && now >= blockedUntil) {
                const stamp = generation;
                pending = true;
                Promise.resolve().then(() => wake.request('screen')).then(sentinel => {
                    if (stamp !== generation || !wanted || document.hidden || sentinel.released) { release(sentinel); return; }
                    lock = sentinel;
                    sentinel.addEventListener('release', () => {
                        if (lock !== sentinel) return;
                        lock = null;
                        blockedUntil = Date.now() + 15000; // Don't fight battery/screen policies or retry on each status read.
                        indicator(Date.now());
                    }, {once:true});
                }).catch(() => { if (stamp === generation) blockedUntil = Date.now() + 15000; }).finally(() => {
                    pending = false;
                    reconcile(stamp !== generation);
                });
            }
            indicator(now);
        }
        function activity(event) {
            if (!supported || !event.isTrusted || !live || document.hidden || mode === 'system') return;
            lastInput = Date.now();
            // Don't do DOM work on each coalesced drawing sample while the existing lease holds.
            if (lock && idleTimer !== undefined) return;
            if (lock && mode === 'live') return;
            if (event.type !== 'pointermove' || !wanted) reconcile(event.type !== 'pointermove');
        }
        for (const event of ['pointerdown','pointermove','wheel','keydown','input']) {
            document.addEventListener(event, activity, {capture:true,passive:true});
        }
        modeField?.addEventListener('change', () => {
            const next = modeField.value;
            if (control.hidden || modeField.disabled || !modes.includes(next)) { modeField.value = mode; return; }
            // Apply only the acknowledged lesson state, never a locally saved choice.
            if (next !== mode) changeMode(next);
        });

        const canNotify = () => live && expanded && !document.hidden;
        function hide(channel) {
            clearTimeout(channel.timer); channel.timer = undefined; channel.current = null;
            channel.node.hidden = true;
            if (channel === chat) {
                detail.hidden = true;
                preview.hidden = false;
                preview.setAttribute('aria-expanded','false');
                snippet.textContent = chatStatus.textContent = sender.textContent = full.textContent = '';
            } else channel.node.textContent = '';
        }
        function clearToasts() {
            for (const channel of [chat, join]) { channel.queue = []; hide(channel); }
        }
        function display(channel) {
            if (!canNotify() || !channel.queue.length || channel.current) return;
            const item = channel.current = channel.queue.shift();
            channel.node.hidden = false;
            if (channel === chat) snippet.textContent = chatStatus.textContent = `${item.name}: ${shortMessage(item.text)}`;
            else channel.node.textContent = item;
            channel.timer = setTimeout(() => dismiss(channel), channel.time);
        }
        function dismiss(channel) {
            const hadFocus = channel === chat && channel.node.contains(document.activeElement);
            hide(channel);
            if (hadFocus) el('board-expand')?.focus({preventScroll:true});
            display(channel);
        }
        function notify(channel, items) {
            if (!canNotify() || !items.length) return;
            channel.queue.push(...items);
            display(channel);
            channel.queue = channel.queue.slice(-channel.limit);
        }
        preview.addEventListener('click', () => {
            if (!canNotify() || !chat.current || !detail.hidden) return;
            clearTimeout(chat.timer); chat.timer = undefined;
            sender.textContent = chat.current.name;
            full.textContent = chat.current.text;
            detail.hidden = false;
            preview.setAttribute('aria-expanded','true');
            preview.hidden = true;
            close.focus({preventScroll:true});
        });
        close.addEventListener('click', () => dismiss(chat));
        const dismissOutside = event => {
            if (!detail.hidden && !chat.node.contains(event.target)) dismiss(chat);
        };
        document.addEventListener('pointerdown', dismissOutside, {capture:true,passive:true});
        document.addEventListener('click', dismissOutside, {capture:true});
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || detail.hidden) return;
            event.preventDefault();
            event.stopPropagation(); // Close the detail before the board's own Escape handler.
            dismiss(chat);
        }, {capture:true});
        document.addEventListener('visibilitychange', () => {
            known = null; chatReady = false; clearToasts();
            if (!document.hidden) blockedUntil = 0;
            reconcile(!document.hidden);
        });

        reconcile();
        return {
            setLive(value, requested, teacherMayChoose = false, saving = false) {
                const nextMode = value && modes.includes(requested) ? requested : 'live';
                if (control) {
                    control.hidden = !value || !teacherMayChoose;
                    if (control.hidden && control.open) control.open = false;
                    modeField.disabled = control.hidden || saving;
                    if (modeField.value !== nextMode) modeField.value = nextMode;
                }
                if (live === value && mode === nextMode) return;
                live = value; mode = nextMode;
                if (live) { lastInput = Date.now(); blockedUntil = 0; }
                else { known = null; chatReady = false; clearToasts(); }
                reconcile(true);
            },
            expand(value) { expanded = value; if (!expanded) clearToasts(); },
            snapshot(profiles, messages, more, self) {
                const next = new Set(Object.keys(profiles || {}).filter(isPupil));
                if (known) notify(join, [...next].filter(id => id !== self && !known.has(id))
                    .map(id => `${profiles[id].name || 'دانش‌آموز'} وارد کلاس شد`));
                known = next;
                if (chatReady && !more) {
                    const rows = messages.filter(m => m.recent === true && isPupil(m.author) && m.author !== self && typeof m.text === 'string');
                    notify(chat, rows.slice(-3).map(row => ({name:profiles?.[row.author]?.name || row.by || 'دانش‌آموز', text:row.text})));
                }
                chatReady = !more;
            },
        };
    };
})();
