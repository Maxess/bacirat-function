(() => {
    'use strict';
    const el = (id) => document.getElementById(id);
    class PublicError extends Error {
        constructor(message, status) { super(message); this.status = status; }
    }
    async function request(root, action, data = {}, {multipart = false, keepalive = false} = {}) {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), multipart ? 90000 : 7000);
        try {
            const headers = {'X-Classroom-CSRF': root.dataset.csrf};
            let body;
            if (multipart) body = data;
            else { headers['Content-Type'] = 'application/json'; body = JSON.stringify({action, ...data}); }
            const res = await fetch(root.dataset.api, {method:'POST', credentials:'same-origin', cache:'no-store', headers, body, signal:ctl.signal, keepalive});
            const reply = await res.json().catch(() => ({}));
            if (!res.ok || reply.ok !== true) throw new PublicError(reply.message || 'درخواست کلاس کامل نشد.', res.status);
            return reply;
        } finally {
            clearTimeout(timer);
        }
    }

    const root = el('classroom');
    if (!root) return;
    const lk = window.LivekitClient;
    const staff = root.dataset.moderator === 'yes';
    const roomClass = Number(root.dataset.class);
    const api = (action, data = {}, options) => request(root, action, {class: roomClass, ...data}, options);
    const student = (id) => /^student-[1-9]\d{0,17}$/.test(id);
    let active = null, connecting = false, busy = false, polling = false, needsRefresh = false, permanent = false, stopped = false;
    let wanted = true, autoStart = staff, state = {phase:'idle', ready:null}, chatCursor = 0, seconds = {}, admitted = false;
    let pollTimer, flow = 0, retryAt = 0, attempts = 0;
    let peopleKey = '', peopleTimers = new Map(), slotKey = null;

    function note(text = '') {
        el('notice').textContent = text;
        el('notice').hidden = !text;
    }
    function micNote(text = '') {
        el('mic-feedback').textContent = text;
        el('mic-feedback').hidden = !text;
    }
    function connected() { return active?.room.state === lk?.ConnectionState.Connected; }
    function hasAccess() { return !!active && connected() && admitted; }
    function scope(link = active) {
        return link ? {lesson:link.lesson, epoch:link.epoch, sid:link.room.localParticipant.sid || ''} : {};
    }
    function maySpeak(person) {
        const p = person?.permissions;
        const sources = p?.canPublishSources;
        // Backend grants a single explicit source; never assume that a missing constraint means permission.
        return p?.canPublish === true && Array.isArray(sources) && sources.includes(2);
    }
    function duration(value) {
        const n = Math.max(0, Number(value) || 0);
        return [Math.floor(n / 3600), Math.floor(n % 3600 / 60), n % 60].map(v => String(v).padStart(2,'0')).join(':');
    }
    function microphoneFailure(local, enabling, error) {
        const capture = enabling ? local?.lastMicrophoneError : null;
        const name = error?.name || capture?.name;
        if (['NotAllowedError','PermissionDeniedError','SecurityError'].includes(name) || capture?.name === 'NotAllowedError') {
            return 'مرورگر اجازهٔ استفاده از میکروفون را نداده است. (MIC-ACCESS)';
        }
        if (enabling && (capture || ['NotFoundError','DevicesNotFoundError','NotReadableError','TrackStartError','OverconstrainedError'].includes(name))) {
            return 'مرورگر نتوانست میکروفون را راه‌اندازی کند. (MIC-CAPTURE)';
        }
        if (enabling && ['OperationError','NegotiationError'].includes(name)) return 'مسیر ارسال صدا برقرار نشد. (MIC-RTC)';
        if (!connected() || ['ConnectionError','SignalReconnectError'].includes(name) || (name === 'PublishTrackError' && error.status === 408)) {
            return 'درخواست میکروفون به‌دلیل اختلال اتصال کامل نشد. (MIC-CONNECTION)';
        }
        if (enabling && (!maySpeak(local) || (name === 'PublishTrackError' && error.status === 403))) {
            return 'اجازهٔ ارسال صدا در کلاس تأیید نشد؛ از دبیر نوبت بگیر. (MIC-GRANT)';
        }
        return enabling ? 'میکروفون برای ارسال صدا فعال نشد. (MIC-PUBLISH)' : 'خاموش‌کردن میکروفون کامل نشد. (MIC-MUTE)';
    }

    const board = window.ClassroomBoard(root, {
        canEdit: () => staff && hasAccess(),
        async send(action, data, file) {
            if (!hasAccess() || !staff) throw new PublicError('تخته در حالت انتظار قابل ویرایش نیست.');
            if (file) {
                const form = new FormData();
                for (const [k,v] of Object.entries({class:roomClass, ...scope(), ...data})) form.set(k,String(v));
                form.set('file', file);
                await request(root, 'upload', form, {multipart:true});
            } else await api(action, {...scope(), ...data});
            refresh();
        },
        explain: e => e instanceof PublicError ? e.message : 'پردازش فایل یا ارتباط با تخته کامل نشد.',
    });

    async function run(work) {
        if (busy) return;
        busy = true;
        render();
        try { await work(); }
        catch (err) { note(err instanceof PublicError ? err.message : 'عملیات کامل نشد؛ اتصال یا وضعیت کلاس را بررسی کن.'); }
        finally { busy = false; render(); }
    }

    function personButton(person, label, action, success) {
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'secondary'; btn.textContent = label;
        btn.setAttribute('aria-label', `${label}: ${person.name || person.identity}`);
        btn.addEventListener('click', () => run(async () => {
            if (!hasAccess()) return;
            await api(action, {...scope(), identity:person.identity});
            note(success);
        }));
        return btn;
    }
    function renderPeople() {
        const field = el('people');
        if (!hasAccess()) {
            if (peopleKey) field.replaceChildren();
            peopleKey = ''; peopleTimers.clear();
            return;
        }
        const local = active.room.localParticipant;
        const people = [local, ...active.room.remoteParticipants.values()];
        const key = JSON.stringify(people.map(p => [p.identity,p.name,maySpeak(p)]));
        if (key !== peopleKey) {
            peopleKey = key; peopleTimers.clear();
            field.replaceChildren(...people.map(person => {
                const li = document.createElement('li');
                const line = document.createElement('div'); line.className = 'person-line';
                const name = document.createElement('span');
                const allowed = maySpeak(person);
                name.textContent = (person.name || person.identity) + (local === person ? ' (شما)' : '') + (allowed ? ' · نوبت صدا' : '');
                line.append(name);
                if (student(person.identity)) {
                    const timer = document.createElement('bdi'); timer.className = 'duration';
                    timer.setAttribute('aria-label','مدت حضور ثبت‌شده');
                    peopleTimers.set(person.identity, timer); line.append(timer);
                }
                li.append(line);
                if (staff && (!allowed || student(person.identity))) {
                    const box = document.createElement('div'); box.className = 'participant-controls';
                    if (!allowed) box.append(personButton(person,'دادن نوبت صدا','give-mic','نوبت داده شد؛ گوینده میکروفون را خودش روشن می‌کند.'));
                    if (student(person.identity)) {
                        box.append(personButton(person,'قطع میکروفون','cut-mic','میکروفون این دانش‌آموز قطع شد.'));
                        box.append(personButton(person,'قطع تصویر','cut-video','مجوز تصویر این دانش‌آموز بسته شد.'));
                    }
                    li.append(box);
                }
                return li;
            }));
        }
        for (const [id, node] of peopleTimers) node.textContent = duration(seconds[id] || 0);
        for (const btn of field.querySelectorAll('button')) btn.disabled = busy;
    }

    function syncSlots() {
        if (!staff) return;
        const field = el('slot');
        const choices = state.slot
            ? [{id:state.slot, label:[state.period, state.subject, state.bell].filter(Boolean).join(' · ')}]
            : state.schedule?.choices || [];
        const key = JSON.stringify(choices);
        if (key === slotKey) return;
        slotKey = key;
        const value = field.value;
        const option = (id, label) => {
            const node = document.createElement('option');
            node.value = String(id); node.textContent = label;
            return node;
        };
        field.replaceChildren(option('', 'انتخاب زنگ'), ...choices.map(({id, label}) => option(id, label)));
        field.value = choices.some(({id}) => String(id) === value) ? value : choices.length === 1 ? String(choices[0].id) : '';
    }
    function bellMessage() {
        const schedule = state.schedule;
        if (!schedule) return 'در حال بررسی زنگ‌های امروز…';
        if (schedule.choices.length > 1) return 'زنگ موردنظر را از برنامهٔ امروز انتخاب کن.';
        if (schedule.choices.length === 1) return 'زنگ مشخص است؛ برای ورود می‌توانی «شروع زنگ» را بزنی.';
        if (schedule.next) return `زنگ بعدی: ${schedule.next} (به وقت تهران).`;
        return schedule.timed ? 'برای این حساب اکنون زنگی در ساعت‌های ثبت‌شدهٔ امروز نیست.' : 'زنگ مجاز دیگری برای امروز در برنامهٔ این کلاس نیست.';
    }

    function render() {
        const live = hasAccess();
        const phase = state.phase;
        const connectingNow = connecting || (!!active && !live);
        el('live-panel').hidden = !live;
        el('waiting').hidden = live;
        el('status').textContent = live ? 'کلاس در جریان است' : 'اتاق انتظار';
        el('lecture-title').textContent = state.subject ? [state.subject, state.period, state.bell].filter(Boolean).join(' · ') : el('lecture-title').dataset.original;
        el('leave').hidden = staff ? (!wanted && !active) : !wanted;
        if (staff) {
            const slot = el('slot');
            const currentSlot = state.slot || 0;
            slot.disabled = !!currentSlot || busy;
            el('open').disabled = busy || !!active || connecting || state.ready === null || permanent || phase === 'closing'
                || phase === 'reserved' || (!currentSlot && !slot.value) || (phase === 'paused' && !state.resumable);
            el('open').textContent = phase === 'paused' ? 'ادامهٔ زنگ' : currentSlot ? 'پیوستن به زنگ' : 'شروع زنگ';
            el('close').hidden = !state.lesson || phase === 'reserved';
            el('close').disabled = busy || phase === 'closing';
            el('clear-floor').disabled = !live || busy;
        } else el('join').hidden = wanted || permanent;
        el('wait-title').textContent = phase === 'paused' ? 'کلاس موقتاً متوقف است' : phase === 'closing' ? 'کلاس در حال پایان است'
            : connectingNow ? 'در حال تأیید ورود…' : staff ? 'در انتظار شروع زنگ' : 'در انتظار ورود دبیر یا مسئول مدرسه';
        el('wait-message').textContent = permanent ? 'برای دسترسی دوباره صفحه را از پنل مدرسه باز کنید.'
            : phase === 'reserved' ? 'زنگ فعلی برای این حساب مجاز نیست. پس از پایان زنگ می‌توانی جلسهٔ خودت را شروع کنی.'
            : state.ready === false ? 'ارتباط با سرور رسانه برقرار نیست. دسترسی موقتاً بسته است.'
            : state.callbackMissing ? 'رویداد اتصال از LiveKit تأیید نشده است. مدیر مدرسه نشانی وب‌هوک در تنظیمات افزونه را بررسی کند.'
            : phase === 'paused' ? 'تا بازگشت دبیر یا مسئول مجاز منتظر بمانید؛ شمارش حضور متوقف است.'
            : staff ? bellMessage()
            : 'شما وارد اتاق انتظار شده‌اید. نیازی به اقدامی نیست؛ لطفاً تا ورود دبیر، مدیر یا ناظم منتظر بمانید.';
        const local = active?.room.localParticipant;
        el('microphone').disabled = !live || busy || !maySpeak(local);
        el('microphone').textContent = local?.isMicrophoneEnabled ? 'خاموش‌کردن میکروفون' : 'روشن‌کردن میکروفون';
        el('enable-audio').hidden = !live || active.room.canPlaybackAudio;
        el('chat-text').disabled = !live || busy;
        for (const audio of el('audio-tracks').children) audio.muted = !live;
        renderPeople();
    }

    function clearActivity() {
        if (admitted || chatCursor || board.position().board) {
            el('chat-log').replaceChildren();
            el('chat-text').value = '';
            board.reset();
        }
        admitted = false;
        chatCursor = 0; seconds = {};
    }
    function release(link = active, warn = false) {
        if (!link || active !== link) return;
        const current = scope(link);
        active = null;
        clearActivity();
        flow++;
        el('audio-tracks').replaceChildren();
        if (warn && current.sid) api('leave', current, {keepalive:true}).catch(() => {});
        link.room.disconnect().catch(() => {});
        render();
    }
    function pushChat(values) {
        const log = el('chat-log');
        const end = log.scrollHeight - log.clientHeight - log.scrollTop < 45;
        for (const row of values || []) {
            if (row.n <= chatCursor) continue;
            const item = document.createElement('li');
            const name = document.createElement('bdi'); name.className = 'chat-by'; name.textContent = row.by;
            const text = document.createElement('span'); text.className = 'chat-text'; text.textContent = row.text;
            item.append(name,text); log.append(item);
            chatCursor = row.n;
        }
        while (log.children.length > 120) log.firstElementChild.remove();
        if (end) log.scrollTop = log.scrollHeight;
    }

    function makeRoom() {
        return new lk.Room({
            singlePeerConnection:false, // Preserve the tested first-speaker fix; publishing does not renegotiate the receiving PC.
            audioCaptureDefaults:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},
            publishDefaults:{audioPreset:{maxBitrate:24000},dtx:true,red:false,forceStereo:false},
        });
    }
    async function enter(getTicket, gesture = false) {
        if (active || connecting || !wanted || stopped || permanent || !lk || !window.isSecureContext) return;
        const pending = ++flow;
        connecting = true;
        const room = makeRoom();
        if (gesture) room.startAudio().catch(() => {});
        let link = null;
        try {
            const ticket = await getTicket();
            if (pending !== flow || !wanted || stopped || permanent) { await room.disconnect(); return; }
            link = {room, lesson:ticket.lesson, epoch:ticket.epoch};
            active = link;
            flow++; // Ignore status requests made before this transport was installed.
            for (const event of [lk.RoomEvent.ParticipantConnected,lk.RoomEvent.ParticipantDisconnected,lk.RoomEvent.LocalTrackPublished,
                lk.RoomEvent.LocalTrackUnpublished,lk.RoomEvent.AudioPlaybackStatusChanged,lk.RoomEvent.ConnectionStateChanged]) {
                room.on(event, () => { if (active === link) render(); });
            }
            room.on(lk.RoomEvent.TrackSubscribed, (track) => {
                if (active !== link || track.kind !== lk.Track.Kind.Audio) return;
                const node = track.attach(); node.muted = !hasAccess(); el('audio-tracks').append(node);
            });
            room.on(lk.RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach(node => node.remove()));
            room.on(lk.RoomEvent.ParticipantPermissionsChanged, (before, person) => {
                if (active !== link) return;
                if (person === room.localParticipant && !maySpeak(person)) person.setMicrophoneEnabled(false).catch(() => {});
                if (!staff && person === room.localParticipant && hasAccess() && before && maySpeak({permissions:before}) !== maySpeak(person)) {
                    note(maySpeak(person) ? 'نوبت صدا داری؛ اگر آماده‌ای، خودت میکروفون را روشن کن.' : 'نوبت صدا گرفته شد؛ هنوز می‌توانی گوش بدهی.');
                }
                render();
            });
            const retrying = () => {
                if (active !== link) return;
                if (staff) release(link, true); // Stop the staff lease at the first signal-loss event, not just after ICE timeout.
                else {
                    const last = scope(link);
                    clearActivity();
                    if (last.sid) api('leave', last, {keepalive:true}).catch(() => {});
                    render();
                }
                refresh();
            };
            room.on(lk.RoomEvent.SignalReconnecting, retrying);
            room.on(lk.RoomEvent.Reconnecting, retrying);
            room.on(lk.RoomEvent.Reconnected, refresh);
            room.on(lk.RoomEvent.Disconnected, (reason) => {
                if (active !== link) return;
                const code = Number.isInteger(reason) ? lk.DisconnectReason?.[reason] : '';
                if (code === 'DUPLICATE_IDENTITY') {
                    wanted = false;
                    note('این حساب در دستگاه یا صفحهٔ دیگری وارد شده است. (DUPLICATE_IDENTITY)');
                } else if (!['ROOM_DELETED','CLIENT_INITIATED'].includes(code)) {
                    note(`ارتباط رسانه قطع شد. هر زمان کلاس آماده باشد، دوباره وصل می‌شویم. (${code || 'ROOM-DISCONNECTED'})`);
                }
                retryAt = Date.now() + 1700;
                release(link, true); refresh();
            });
            await room.connect(ticket.url, ticket.token);
            attempts = 0; retryAt = 0;
            if (active === link) { note(); micNote(); }
        } catch (error) {
            if (link && active === link) release(link);
            else await room.disconnect().catch(() => {});
            attempts++;
            retryAt = Date.now() + Math.min(15000, 1600 * (2 ** Math.min(4, attempts - 1)));
            if (error instanceof PublicError && error.status === 403) { if (gesture) wanted = false; }
            if (gesture) throw error;
            if (!(error instanceof PublicError && error.status === 409)) note(error instanceof PublicError ? error.message : 'ورود رسانه ممکن نشد؛ اتصال را بررسی کن. (ROOM-CONNECT)');
        } finally {
            connecting = false;
            render(); refresh();
        }
    }

    async function poll() {
        if (polling || stopped || permanent) return;
        polling = true; needsRefresh = false;
        const captured = flow;
        const local = connected() ? scope() : {};
        try {
            const result = await api('status', {...local, chat:chatCursor, ...board.position()});
            if (captured !== flow || stopped) return;
            const prior = state;
            state = result;
            syncSlots();
            if (active && (active.epoch !== state.epoch || active.lesson !== state.lesson || !result.ready
                || !['live', ...(staff ? ['starting'] : [])].includes(state.phase))) release(active);
            if (staff && prior.lesson && state.phase === 'idle') wanted = false;
            // Observe/rejoin one lesson per entry. An old tab never starts the next lesson on its own.
            if (state.lesson || state.phase === 'reserved' || (!state.schedule?.timed && state.schedule?.choices.length > 1)) autoStart = false;
            if (active && local.sid && scope().sid === local.sid && connected() && result.admitted === true) {
                admitted = true; seconds = result.seconds || {};
                render(); pushChat(result.chat);
                board.sync(result.board, scope());
                needsRefresh ||= !!(result.chatMore || result.board?.more);
            } else clearActivity();
            if (!active && !connecting && wanted && state.ready && Date.now() >= retryAt) {
                if (state.joinable) {
                    const info = {lesson:state.lesson,epoch:state.epoch};
                    enter(() => api('ticket',info));
                } else if (staff && state.resumable && state.slot && !state.callbackMissing) {
                    const slot = state.slot;
                    enter(() => api('open',{slot}));
                } else if (autoStart && state.phase === 'idle' && !document.hidden && state.schedule?.date === root.dataset.bellDate
                    && state.schedule?.choices.length === 1) {
                    autoStart = false;
                    enter(() => api('open',{slot:state.schedule.choices[0].id}));
                }
            }
        } catch (error) {
            if (captured !== flow || stopped) return;
            release(active, true);
            clearActivity();
            state = {...state, ready:false, phase:state.lesson ? 'paused' : 'idle', resumable:false};
            permanent = error instanceof PublicError && [401,403,404].includes(error.status);
            if (permanent) wanted = false;
            note(error instanceof PublicError ? error.message : 'ارتباط با سایت قطع است؛ تا بازگشت اتصال، کلاس در انتظار می‌ماند.');
        } finally {
            polling = false; render();
            if (!stopped && !permanent) pollTimer = setTimeout(poll, needsRefresh ? 50 : (active || connecting ? 1900 : 3400) + Math.random()*270);
        }
    }
    function refresh() {
        if (stopped || permanent) return;
        if (polling) { needsRefresh = true; return; }
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 30);
    }

    el('microphone').addEventListener('click', () => run(async () => {
        const link = active;
        const local = link?.room.localParticipant;
        if (!hasAccess() || !maySpeak(local)) return;
        micNote();
        const enabling = !local.isMicrophoneEnabled;
        try {
            await local.setMicrophoneEnabled(enabling);
            if (link !== active || !hasAccess()) { await local.setMicrophoneEnabled(false).catch(() => {}); return; }
            if (local.isMicrophoneEnabled !== enabling) micNote(microphoneFailure(local,enabling));
        } catch (error) { micNote(microphoneFailure(local,enabling,error)); }
    }));
    el('enable-audio').addEventListener('click', () => run(async () => {
        if (!hasAccess()) return;
        try { await active.room.startAudio(); }
        catch (_) { throw new PublicError('پخش صدا روی دستگاه شنونده فعال نشد. (AUDIO-PLAYBACK)'); }
    }));
    el('chat-form').addEventListener('submit', event => {
        event.preventDefault();
        run(async () => {
            if (!hasAccess()) return;
            const field = el('chat-text');
            await api('chat', {...scope(), text:field.value}); field.value = ''; refresh();
        });
    });
    el('leave').addEventListener('click', () => {
        wanted = false; flow++; release(active,true); clearActivity(); render(); refresh();
    });
    if (staff) {
        el('slot').addEventListener('change', render);
        el('open').addEventListener('click', () => run(async () => {
            if (state.phase === 'closing' || state.ready === null || permanent) return;
            const slot = state.slot || Number(el('slot').value);
            if (!slot) return;
            wanted = true;
            autoStart = false;
            retryAt = 0;
            await enter(() => api('open',{slot}), true);
        }));
        el('clear-floor').addEventListener('click', () => run(async () => {
            if (!hasAccess()) return;
            await api('cut-all-mics',scope()); note('نوبت صدا از همه گرفته شد.');
        }));
        el('close').addEventListener('click', () => {
            if (!state.lesson || !window.confirm('کلاس برای همه پایان یابد و گزارش حضور ثبت شود؟')) return;
            run(async () => {
                wanted = false; flow++;
                const response = api('close',{lesson:state.lesson});
                release(active,true);
                await response;
                state = {phase:'idle', ready:null};
                syncSlots();
                note('زنگ پایان یافت؛ گزارش حضور در پنل ثبت شد.'); refresh();
            });
        });
    } else el('join').addEventListener('click', () => { wanted = true; retryAt = 0; note(); render(); refresh(); });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(pollTimer); release(active,true); });
    window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; refresh(); } });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    el('lecture-title').dataset.original = el('lecture-title').textContent;
    if (!lk || !window.isSecureContext) {
        permanent = true;
        note('برای ورود به رسانه به مرورگر سازگار، بستهٔ کامل کلاس و HTTPS معتبر نیاز است.');
    }
    render(); refresh();
})();
