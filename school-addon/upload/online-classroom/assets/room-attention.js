(() => {
    'use strict';
    const preference = 'baci-classroom-wake-mode';
    const modes = ['live','idle','system'];
    const idleLimit = 5 * 60 * 1000;
    const isPupil = id => typeof id === 'string' && /^student-[1-9]\d{0,17}$/.test(id);
    const letters = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('fa', {granularity:'grapheme'}) : null;
    function shortMessage(text) {
        const line = text.replace(/\s+/gu,' ').trim();
        const units = letters ? [...letters.segment(line)].map(x => x.segment) : Array.from(line);
        return units.length > 30 ? units.slice(0,29).join('') + '…' : line;
    }

    window.ClassroomAttention = () => {
        const el = id => document.getElementById(id);
        const modeField = el('wake-mode'), status = el('wake-status'), control = el('wake-control');
        const wake = window.isSecureContext ? window.navigator?.wakeLock : null;
        const supported = typeof wake?.request === 'function';
        const channels = [
            {node:el('board-chat-toast'), time:2000, limit:2, queue:[]},
            {node:el('board-join-toast'), time:1000, limit:3, queue:[]},
        ];
        let mode = 'live', live = false, expanded = false, known = null, chatReady = false;
        let lastInput = Date.now(), idleTimer, lock, pending = false, generation = 0, blockedUntil = 0, wanted = false;
        try {
            const saved = window.localStorage.getItem(preference);
            if (modes.includes(saved)) mode = saved;
        } catch (_) {} // Choices still work for the current page if storage is disabled.
        modeField.value = mode;
        modeField.disabled = !supported;

        function release(sentinel) { if (sentinel && !sentinel.released) sentinel.release().catch(() => {}); }
        function indicator(now) {
            const held = !!lock && !lock.released;
            control.dataset.awake = String(held);
            const text = !supported ? 'این مرورگر روشن‌ماندن صفحه را به کلاس واگذار نمی‌کند.'
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
            const eligible = live && !document.hidden && mode !== 'system';
            if ((!eligible || mode !== 'idle') && idleTimer !== undefined) {
                clearTimeout(idleTimer); idleTimer = undefined;
            }
            if (eligible && mode === 'idle' && left > 0 && idleTimer === undefined) {
                idleTimer = setTimeout(() => { idleTimer = undefined; reconcile(); }, left);
            }
            const next = supported && eligible && (mode !== 'idle' || left > 0);
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
            if (!event.isTrusted || !live || document.hidden || mode === 'system') return;
            lastInput = Date.now();
            // Don't do DOM work on each coalesced drawing sample while the existing lease holds.
            if (lock && idleTimer !== undefined) return;
            if (lock && mode === 'live') return;
            if (event.type !== 'pointermove' || !wanted) reconcile(event.type !== 'pointermove');
        }
        for (const event of ['pointerdown','pointermove','wheel','keydown','input']) {
            document.addEventListener(event, activity, {capture:true,passive:true});
        }
        modeField.addEventListener('change', () => {
            if (!modes.includes(modeField.value)) return;
            mode = modeField.value; lastInput = Date.now(); blockedUntil = 0;
            try { window.localStorage.setItem(preference, mode); } catch (_) {}
            reconcile(true);
        });

        const canNotify = () => live && expanded && !document.hidden;
        function clearToasts() {
            for (const channel of channels) {
                clearTimeout(channel.timer); channel.timer = undefined; channel.queue = [];
                channel.node.hidden = true; channel.node.textContent = '';
            }
        }
        function display(channel) {
            if (!canNotify() || !channel.queue.length) return;
            channel.node.textContent = channel.queue.shift();
            channel.node.hidden = false;
            channel.timer = setTimeout(() => {
                channel.timer = undefined; channel.node.hidden = true; channel.node.textContent = '';
                display(channel);
            }, channel.time);
        }
        function notify(channel, texts) {
            if (!canNotify() || !texts.length) return;
            channel.queue.push(...texts);
            if (channel.timer === undefined) display(channel);
            channel.queue = channel.queue.slice(-channel.limit);
        }
        document.addEventListener('visibilitychange', () => {
            known = null; chatReady = false; clearToasts();
            if (!document.hidden) blockedUntil = 0;
            reconcile(!document.hidden);
        });

        reconcile();
        return {
            setLive(value) {
                if (live === value) return;
                live = value;
                if (live) { lastInput = Date.now(); blockedUntil = 0; }
                else { known = null; chatReady = false; clearToasts(); }
                reconcile();
            },
            expand(value) { expanded = value; if (!expanded) clearToasts(); },
            snapshot(profiles, messages, more, self) {
                const next = new Set(Object.keys(profiles || {}).filter(isPupil));
                if (known) notify(channels[1], [...next].filter(id => id !== self && !known.has(id))
                    .map(id => `${profiles[id].name || 'دانش‌آموز'} وارد کلاس شد`));
                known = next;
                if (chatReady && !more) {
                    const rows = messages.filter(m => m.recent === true && isPupil(m.author) && m.author !== self && typeof m.text === 'string');
                    notify(channels[0], rows.slice(-3).map(row => {
                        const name = profiles?.[row.author]?.name || 'دانش‌آموز';
                        return `${name}: ${shortMessage(row.text)}`;
                    }));
                }
                chatReady = !more;
            },
        };
    };
})();
