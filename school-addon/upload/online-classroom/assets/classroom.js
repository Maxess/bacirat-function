(() => {
    'use strict';
    const el = (id) => document.getElementById(id);
    class PublicError extends Error {
        constructor(message, status) { super(message); this.status = status; }
    }
    async function request(root, action, data = {}, {multipart = false, keepalive = false} = {}) {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), multipart ? 90000 : action === 'board-sync' ? 2300 : 7000);
        try {
            const headers = {'X-Classroom-CSRF': root.dataset.csrf};
            let body;
            if (multipart) body = data;
            else { headers['Content-Type'] = 'application/json'; body = JSON.stringify({action, ...data}); }
            const priority = ['status','ticket','leave'].includes(action) ? 'high' : 'auto';
            const res = await fetch(root.dataset.api, {method:'POST', credentials:'same-origin', cache:'no-store',
                headers, body, signal:ctl.signal, keepalive, priority});
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
    const fridaySlot = 'friday-test';
    const staff = root.dataset.moderator === 'yes';
    const roomClass = Number(root.dataset.class);
    const api = (action, data = {}, options) => request(root, action, {class: roomClass, ...data}, options);
    const student = (id) => /^student-[1-9]\d{0,17}$/.test(id);
    const persian = new Intl.NumberFormat('fa-IR');
    const faDigits = text => text.replace(/[0-9]/g, digit => persian.format(Number(digit)));
    const put = (node, text) => { if (node.textContent !== text) node.textContent = text; };
    const panels = ['people', 'chat'];
    const features = {mic:{label:'میکروفون',icons:['mic','mic-off'],source:2},
        camera:{label:'دوربین',icons:['camera','camera-off'],source:1}, board:{label:'نوشتن روی تخته',icons:['pen']}};
    const permissionIcons = el('permission-icons')?.content;
    const cameraSource = lk?.Track.Source.Camera, micSource = lk?.Track.Source.Microphone, screenSource = lk?.Track.Source.ScreenShare;
    // VP8, one layer, no second codec. These are encoder targets, not wire rates.
    const cameraVideo = {width:320,height:180,frameRate:8,bitrate:85000};
    const shareVideo = {width:640,height:360,frameRate:2,bitrate:70000}; // Static pages and large text, not motion video.
    const canCaptureScreen = typeof window.navigator?.mediaDevices?.getDisplayMedia === 'function';
    const attention = window.ClassroomAttention(changeWake);
    let active = null, connecting = false, busy = false, polling = false, needsRefresh = false, permanent = false, stopped = false;
    let wanted = true, autoStart = staff, optedIntoTest = false, state = {phase:'idle', ready:null}, chatCursor = 0, seconds = {}, admitted = false;
    let pollTimer, flow = 0, retryAt = 0, attempts = 0;
    let statusAt = 0, inkAt = Infinity, aimAt = Infinity, inkRead = 0, inkRetryAt = 0;
    let peopleKey = '', peopleRows = new Map(), speakersKey = '', slotKey = null, videoMenuKey = '';
    let picture = '', wantedVideo = null;
    const cameraTiles = new Map();
    let shownScreen = null;
    let toastTimer, lastNotice = '', lastNoticeAt = -Infinity;

    function note(text = '') {
        const notice = el('notice');
        const now = performance.now();
        if (text && text === lastNotice && now - lastNoticeAt < 12000) return;
        clearTimeout(toastTimer);
        put(notice, text);
        notice.hidden = !text;
        lastNotice = text;
        lastNoticeAt = now;
        if (text) toastTimer = setTimeout(() => { notice.hidden = true; put(notice, ''); }, 4000);
    }
    function connected() { return active?.room.state === lk?.ConnectionState.Connected; }
    function hasAccess() { return !!active && connected() && admitted; }
    function scope(link = active) {
        return link ? {lesson:link.lesson, epoch:link.epoch, sid:link.room.localParticipant.sid || ''} : {};
    }
    function allows(person, feature) {
        // A LiveKit grant with no explicit sources isn't a classroom permission.
        const p = person?.permissions;
        return p?.canPublish === true && Array.isArray(p.canPublishSources) && p.canPublishSources.includes(features[feature].source);
    }
    const maySpeak = person => allows(person, 'mic');
    const mayCamera = person => allows(person, 'camera');
    const mayScreen = person => person?.permissions?.canPublish === true && Array.isArray(person.permissions.canPublishSources)
        && person.permissions.canPublishSources.includes(3); // LiveKit TrackSource.SCREEN_SHARE, not SCREEN_SHARE_AUDIO.
    function granted(feature, identity) {
        const rule = state.access?.[feature];
        return (rule?.members?.[identity] ?? rule?.all) === true;
    }
    function personName(person) {
        if (student(person.identity)) return state.profiles?.[person.identity]?.name || 'دانش‌آموز';
        return person.name || 'مسئول کلاس';
    }
    function duration(value) {
        const n = Math.max(0, Number(value) || 0);
        return faDigits([Math.floor(n / 3600), Math.floor(n % 3600 / 60)].map(v => String(v).padStart(2,'0')).join(':'));
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
        canEdit: hasAccess,
        notice: note,
        identity:() => active?.room.localParticipant.identity,
        authorName(id) {
            if (student(id)) return state.profiles?.[id]?.name || '';
            return active?.room.remoteParticipants.get(id)?.name || '';
        },
        onExpand:attention.expand,
        aimSoon() {
            aimAt = Math.min(aimAt, performance.now()+25);
            schedulePoll();
        },
        async send(action, data, file) {
            if (!hasAccess() || (!staff && action !== 'board-ink')) throw new PublicError('ویرایش تخته در این وضعیت مجاز نیست.');
            const link = active, local = scope(link);
            let reply;
            try {
                if (file) {
                    const form = new FormData();
                    for (const [k,v] of Object.entries({class:roomClass, ...local, ...data})) form.set(k,String(v));
                    form.set('file', file);
                    reply = await request(root, 'upload', form, {multipart:true});
                } else reply = await api(action, {...local, ...data});
                return reply;
            } finally {
                if (active === link && local.sid === scope().sid && hasAccess()) {
                    inkAt = Math.min(inkAt, Math.max(performance.now()+30, inkRead+(action==='board-ink' ? 245 : 65)));
                    schedulePoll();
                }
            }
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

    function changeAccess(feature, enabled, identity = null) {
        return run(async () => {
            if (!staff || !hasAccess()) return;
            const link = active, local = scope(link);
            try {
                const result = await api('set-access', {...local, feature, enabled, identity});
                if (active !== link || !hasAccess() || scope().sid !== local.sid) return;
                flow++; // Discard reads made before this acknowledged permission change.
                state = {...state, access:result.access};
            } finally { refresh(); }
        });
    }
    function changeWake(mode) {
        if (!hasAccess() || state.wakeControl !== true) return;
        return run(async () => {
            const link = active, local = scope(link);
            try {
                const result = await api('set-wake', {...local, mode});
                if (active !== link || !hasAccess() || scope().sid !== local.sid) return;
                flow++; // An older status response cannot put back the previous wake setting.
                state = {...state, wakeMode:result.wakeMode};
            } finally { refresh(); }
        });
    }

    function personButton(identity, feature) {
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'icon-button';
        btn.dataset.feature = feature;
        for (const icon of features[feature].icons) {
            const svg = permissionIcons.querySelector(`.icon-${icon}`);
            btn.append(svg.cloneNode(true));
        }
        btn.addEventListener('click', () => changeAccess(feature, !granted(feature, identity), identity));
        return btn;
    }
    function personRow(person, key, label, photo) {
        const id = person.identity;
        const li = document.createElement('li'); li.dataset.identity = id;
        const line = document.createElement('div'); line.className = 'person-line';
        const name = document.createElement('span'); name.className = 'person-name'; name.dir = 'auto'; name.textContent = label;
        const avatar = document.createElement('span'); avatar.className = 'person-avatar';
        avatar.textContent = Array.from(label)[0] || '·'; avatar.setAttribute('aria-hidden', 'true');
        if (photo) {
            const img = document.createElement('img'); img.alt = ''; img.decoding = 'async'; img.loading = 'lazy';
            const url = new URL(root.dataset.content, document.baseURI);
            for (const [k,v] of Object.entries({...scope(), photo:id})) url.searchParams.set(k,v);
            img.addEventListener('error', () => img.remove(), {once:true});
            img.src = url.href;
            avatar.append(img);
        }
        const detail = document.createElement('span'); detail.className = 'person-detail';
        detail.append(name); line.append(avatar, detail);
        let timer;
        if (student(id)) {
            timer = document.createElement('bdi'); timer.className = 'duration'; timer.dir = 'ltr';
            timer.setAttribute('aria-label','مدت حضور ثبت‌شده'); detail.append(timer);
        }
        if (active.room.localParticipant === person) {
            li.className = 'person-self';
            const self = document.createElement('span'); self.className = 'self-tag'; self.textContent = 'شما'; line.append(self);
        }
        li.append(line);
        const controls = {};
        if (staff) {
            const box = document.createElement('div'); box.className = 'participant-controls';
            for (const feature of Object.keys(features)) {
                if (feature === 'board' && !student(id)) continue;
                controls[feature] = personButton(id, feature); box.append(controls[feature]);
            }
            li.append(box);
        }
        return {key, li, timer, controls};
    }
    function renderPeople() {
        const field = el('people');
        if (!hasAccess() || el('people-panel').hidden) {
            if (peopleKey) field.replaceChildren();
            peopleKey = ''; peopleRows.clear();
            if (!hasAccess()) put(el('people-count'), '۰');
            return;
        }
        const room = active.room;
        const people = [room.localParticipant, ...room.remoteParticipants.values()];
        put(el('people-count'), persian.format(people.length));
        const entries = people.map(person => [person.identity, person.sid, personName(person), state.profiles?.[person.identity]?.photo === true]);
        const key = JSON.stringify(entries);
        if (key !== peopleKey) {
            peopleKey = key;
            const rows = new Map();
            entries.forEach(([id, sid, label, photo], i) => {
                const k = JSON.stringify([sid,label,photo]), old = peopleRows.get(id);
                rows.set(id, old?.key === k ? old : personRow(people[i], k, label, photo));
            });
            peopleRows = rows;
            field.replaceChildren(...Array.from(rows.values(), row => row.li));
        }
        for (const person of people) {
            const id = person.identity, row = peopleRows.get(id);
            if (row.timer) {
                const value = !staff && person === room.localParticipant ? state.attendance : seconds[id];
                put(row.timer, Number.isInteger(value) ? duration(value) : '—');
            }
            for (const [feature, btn] of Object.entries(row.controls)) {
                const on = feature === 'mic' ? maySpeak(person) && person.isMicrophoneEnabled
                    : feature === 'camera' ? mayCamera(person) && person.isCameraEnabled : false;
                const enabled = granted(feature, id);
                const label = `${features[feature].label} · ${personName(person)}`;
                btn.dataset.granted = String(enabled);
                btn.dataset.on = String(!!on);
                btn.setAttribute('aria-pressed', String(enabled));
                btn.setAttribute('aria-label', label);
                btn.title = `${enabled ? 'لغو اجازهٔ' : 'دادن اجازهٔ'} ${label}${on ? ' (روشن)' : ''}`;
                btn.disabled = busy || state.members?.[id] !== person.sid || (feature === 'camera' && !state.cameraCodec && !enabled);
            }
        }
    }

    function stopCamera(link) {
        link.cameraAttempt++;
        const track = link.cameraTrack;
        link.cameraTrack = null;
        if (!track) return;
        // End capture immediately, even if signaling is unavailable or permission was revoked mid-publish.
        track.stop();
        return link.room.localParticipant.unpublishTrack(track, true).catch(() => {});
    }
    // Start without any automatic subscriptions. Keep microphone delivery independent of
    // the picture selector, and unsubscribe the old picture before asking for a new one.
    function updateSubscriptions() {
        const selector = el('receive-video');
        const live = hasAccess();
        const candidates = new Map(), publications = [];
        if (active && connected()) for (const person of active.room.remoteParticipants.values()) {
            for (const pub of person.trackPublications.values()) {
                publications.push(pub);
                if (!live || pub.kind !== lk.Track.Kind.Video || pub.isMuted) continue;
                if (pub.source === screenSource && mayScreen(person) && person.identity === state.screen?.identity
                    && person.sid === state.screen.sid) candidates.set(pub.trackSid, [pub, `اشتراک صفحه · ${personName(person)}`]);
                if (pub.source === cameraSource && state.cameraCodec && mayCamera(person)) {
                    candidates.set(pub.trackSid, [pub, `دوربین · ${personName(person)}`]);
                }
            }
        }
        if (!candidates.has(picture)) picture = '';
        const labels = [...candidates].map(([sid, [, label]]) => [sid, label]);
        const menuKey = JSON.stringify(labels);
        if (videoMenuKey !== menuKey) {
            videoMenuKey = menuKey;
            const option = ([id, label]) => {
                const node = document.createElement('option');
                node.value = id; node.textContent = label;
                return node;
            };
            selector.replaceChildren(...[['','فقط صدا و تخته'], ...labels].map(option));
        }
        if (selector.value !== picture) selector.value = picture;
        selector.disabled = !live || !candidates.size;
        wantedVideo = document.hidden ? null : candidates.get(picture)?.[0] ?? null;
        const wants = pub => pub === wantedVideo || (live && pub.kind === lk.Track.Kind.Audio && pub.source === micSource);
        for (const pub of publications) if (pub.isDesired && !wants(pub)) pub.setSubscribed(false);
        for (const pub of publications) if (!pub.isDesired && wants(pub)) pub.setSubscribed(true);
    }

    function renderCameras() {
        const strip = el('camera-strip');
        const cameraPeople = new Map();
        if (hasAccess() && state.cameraCodec) {
            const room = active.room;
            for (const person of [room.localParticipant, ...room.remoteParticipants.values()]) {
                const pub = person.getTrackPublication(cameraSource);
                if (!mayCamera(person) || !pub?.track || pub.isMuted || pub.track.kind !== lk.Track.Kind.Video
                    || (person === room.localParticipant ? pub.track !== active.cameraTrack : pub !== wantedVideo)) continue;
                cameraPeople.set(person.identity, {track:pub.track, name:personName(person)});
            }
        }
        for (const [id, tile] of cameraTiles) {
            if (cameraPeople.get(id)?.track === tile.track) continue;
            tile.track.detach(tile.video); tile.figure.remove(); cameraTiles.delete(id);
        }
        for (const [id,{track,name}] of cameraPeople) {
            let tile = cameraTiles.get(id);
            if (!tile) {
                const figure = document.createElement('figure'), caption = document.createElement('figcaption');
                const video = document.createElement('video');
                video.autoplay = true; video.playsInline = true; video.muted = true;
                track.attach(video);
                figure.append(video, caption); strip.append(figure);
                tile = {track,video,caption,figure}; cameraTiles.set(id,tile);
            }
            put(tile.caption, name);
        }
        strip.hidden = !cameraTiles.size;
    }

    function itsScreen(link) {
        return link && state.screen?.identity === link.room.localParticipant.identity && state.screen.sid === link.room.localParticipant.sid;
    }
    function stopScreen(link) {
        link.screenAttempt++;
        const track = link.screenTrack;
        link.screenTrack = null;
        if (!track) return;
        track.stop();
        return link.room.localParticipant.unpublishTrack(track,true).catch(() => {});
    }
    async function giveBackScreen(link) {
        stopScreen(link);
        if (active !== link || !hasAccess() || !itsScreen(link)) return;
        const before = scope(link);
        try {
            const result = await api('screen-access',{...before,enabled:false});
            if (active === link && scope().sid === before.sid) {
                flow++;
                state = {...state,screen:result.screen};
            }
        } finally { refresh(); }
    }
    function renderScreen() {
        const live = hasAccess();
        const selected = state.screen;
        const local = active?.room.localParticipant;
        const mine = live && itsScreen(active);
        if (active?.screenTrack && (!mine || !state.screenEnabled || !mayScreen(local))) stopScreen(active);
        let person, display = null;
        if (live && selected) {
            person = mine ? local : active.room.remoteParticipants.get(selected.identity);
            const pub = person?.getTrackPublication(screenSource);
            if (person?.sid === selected.sid && mayScreen(person) && pub?.track?.kind === lk.Track.Kind.Video && !pub.isMuted
                && (mine ? pub.track === active.screenTrack : pub === wantedVideo)) display = pub.track;
        }
        const video = el('screen-video');
        if (shownScreen !== display) {
            shownScreen?.detach(video);
            display?.attach(video);
            shownScreen = display;
        }
        el('screen-panel').hidden = !display;
        if (display) put(el('screen-label'), `اشتراک صفحهٔ ${personName(person)}`);
        if (!staff) return false;
        const button = el('screen-toggle');
        const show = live && state.screenEnabled === true && canCaptureScreen;
        const sharing = !!(mine && active.screenTrack);
        button.hidden = !show;
        button.disabled = !show || busy || !!active?.screenBusy || (!!selected && !mine) || (!!mine && !mayScreen(local) && !sharing);
        const label = sharing ? 'توقف اشتراک صفحه' : !!selected && !mine ? 'ارائه‌دهندهٔ دیگری در حال اشتراک است'
            : mine ? 'انتخاب صفحه برای اشتراک' : 'آماده‌سازی اشتراک صفحه';
        button.setAttribute('aria-label',label); button.title = label;
        button.setAttribute('aria-pressed',String(sharing));
        button.dataset.prepared = String(!!mine && !sharing);
        return show;
    }

    function setAccessMenu(open, restoreFocus = false) {
        const menu = el('access-menu'), btn = el('toggle-access');
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        if (restoreFocus) btn.focus();
    }
    function renderAccessMenu(live) {
        el('room-permissions').hidden = !live;
        if (!live) setAccessMenu(false);
        el('camera-unavailable').hidden = !!state.cameraCodec;
        for (const feature of Object.keys(features)) {
            const anyException = Object.keys(state.access?.[feature]?.members || {}).length > 0;
            const all = granted(feature);
            const give = el(`all-${feature}-give`), cut = el(`all-${feature}-cut`);
            give.setAttribute('aria-pressed', String(all && !anyException));
            cut.setAttribute('aria-pressed', String(!all && !anyException));
            give.disabled = !live || busy || (all && !anyException) || (feature === 'camera' && !state.cameraCodec);
            cut.disabled = !live || busy || (!all && !anyException);
        }
    }

    function renderSpeakers() {
        // Prune the last audio activity event. Unmuting or reconnecting must not restore old activity.
        if (active) {
            const room = active.room;
            active.speakers = connected() ? active.speakers.filter(person => person.isSpeaking && person.audioLevel > 0 && person.isMicrophoneEnabled
                && (person === room.localParticipant || room.remoteParticipants.get(person.identity) === person)) : [];
        }
        const names = (hasAccess() ? active.speakers : []).map(person => [person.identity, personName(person)]);
        names.sort((a,b) => a[0].localeCompare(b[0])); // Do not move badges around as levels change.
        const key = names.length ? JSON.stringify(names) : '';
        if (key === speakersKey) return;
        speakersKey = key;
        const strip = el('speaker-strip');
        el('speakers').replaceChildren(...names.map(([, name]) => {
            const entry = document.createElement('li');
            const label = document.createElement('bdi');
            label.textContent = name;
            entry.title = name;
            entry.append(label);
            return entry;
        }));
        strip.hidden = !names.length;
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
            node.value = String(id); node.textContent = faDigits(label);
            return node;
        };
        field.replaceChildren(option('', 'انتخاب زنگ امروز'), ...choices.map(({id, label}) => option(id, label)));
        field.value = choices.some(({id}) => String(id) === value) ? value : choices.length === 1 ? String(choices[0].id) : '';
    }
    function bellMessage() {
        const schedule = state.schedule;
        if (!schedule) return 'در حال بررسی زنگ‌های امروز…';
        if (schedule.choices.length === 1 && schedule.choices[0].id === fridaySlot) return 'برای آزمایش، «ورود به آزمایش جمعه» را بزن. این جلسه گزارش حضور یا اعلان ندارد.';
        if (schedule.choices.some(choice => choice.manual)) return 'برای تک‌زنگ، درسِ این هفته را انتخاب کن و «شروع زنگ» را بزن.';
        if (schedule.choices.length > 1) return 'زنگ موردنظر را از برنامهٔ امروز انتخاب کن.';
        if (schedule.choices.length === 1) return 'زنگ مشخص است؛ برای ورود می‌توانی «شروع زنگ» را بزنی.';
        if (schedule.next) return `زنگ بعدی: ${faDigits(schedule.next)} (به وقت تهران).`;
        return schedule.timed ? 'برای این حساب اکنون زنگی در ساعت‌های ثبت‌شدهٔ امروز نیست.' : 'زنگ مجاز دیگری برای امروز در برنامهٔ این کلاس نیست.';
    }

    function render() {
        const live = hasAccess();
        const phase = state.phase;
        const connectingNow = connecting || (!!active && !live);
        el('live-panel').hidden = !live;
        el('waiting').hidden = live;
        root.dataset.live = live ? 'yes' : 'no';
        attention.setLive(live, state.wakeMode, state.wakeControl === true, busy);
        const status = live ? 'در جریان' : connectingNow ? 'در حال اتصال' : 'اتاق انتظار';
        put(el('status'), status);
        el('status-dot').title = status;
        const start = state.startTime || '';
        el('lesson-start').hidden = !start;
        root.dataset.started = start ? 'yes' : 'no';
        put(el('lesson-start-time'), faDigits(start));
        if (state.dayLabel && state.day) {
            const date = el('room-date');
            put(date, state.dayLabel);
            if (date.dateTime !== state.day) date.dateTime = state.day;
        }
        for (const id of panels) el(`toggle-${id}`).disabled = !live;
        if (!staff) {
            el('live-attendance').hidden = !live || !Number.isInteger(state.attendance);
            put(el('live-attendance-time'), duration(state.attendance));
            el('waiting-attendance').hidden = live || !Number.isInteger(state.attendance);
            put(el('waiting-attendance-time'), duration(state.attendance));
        }
        if (staff) {
            const slot = el('slot');
            const currentSlot = state.slot || 0;
            slot.disabled = !!currentSlot || busy;
            el('staff-start').hidden = live;
            el('open').disabled = busy || !!active || connecting || state.ready === null || permanent || phase === 'closing'
                || phase === 'reserved' || (!currentSlot && !slot.value) || (phase === 'paused' && !state.resumable);
            const test = (currentSlot || slot.value) === fridaySlot;
            el('open').textContent = test ? 'ورود به آزمایش جمعه' : phase === 'paused' ? 'ادامهٔ زنگ' : currentSlot ? 'پیوستن به زنگ' : 'شروع زنگ';
            const end = currentSlot === fridaySlot ? 'بستن اتاق آزمایشی برای همه' : 'پایان کلاس برای همه';
            el('close').setAttribute('aria-label', end);
            el('close').title = end;
            el('close').hidden = !state.lesson || phase === 'reserved';
            el('close').disabled = busy || phase === 'closing';
            renderAccessMenu(live);
        } else el('join').hidden = wanted || permanent;
        put(el('wait-title'), phase === 'paused' ? 'کلاس موقتاً متوقف است' : phase === 'closing' ? 'کلاس در حال پایان است'
            : connectingNow ? 'در حال تأیید ورود…' : staff ? 'در انتظار شروع زنگ' : 'در انتظار ورود دبیر یا مسئول مدرسه');
        put(el('wait-message'), permanent ? 'برای دسترسی دوباره صفحه را از پنل مدرسه باز کن.'
            : phase === 'reserved' ? (staff ? 'اتاق این کلاس در اختیار جلسهٔ دیگری است.' : 'اتاق این کلاس هنوز برای ورود دانش‌آموز باز نیست.')
            : state.ready === false ? 'ارتباط با سرور رسانه برقرار نیست. دسترسی موقتاً بسته است.'
            : state.callbackMissing ? 'رویداد اتصال از LiveKit تأیید نشده است. مدیر مدرسه نشانی وب‌هوک در تنظیمات افزونه را بررسی کند.'
            : phase === 'paused' ? 'با خروج آخرین مسئول، تایمر حضور متوقف می‌شود. بعد از بازگشت مسئول و تأیید اتصال، کلاس ادامه پیدا می‌کند.'
            : staff ? bellMessage()
            : !wanted ? 'برای ورود دوباره، «بازگشت به اتاق انتظار» را بزن.'
            : state.practiceAvailable ? 'آزمایش جمعه برای کلاس تو مجاز است. پس از تأیید اتصال دبیر، خودکار وارد می‌شوی.'
            : 'وقتی اتصال دبیر یا مسئول مدرسه تأیید شود، خودکار وارد کلاس می‌شوی.');
        const local = active?.room.localParticipant;
        el('microphone').disabled = !live || busy || !maySpeak(local);
        const micOn = live && !!local?.isMicrophoneEnabled;
        const micAction = micOn ? 'خاموش‌کردن میکروفون' : 'روشن‌کردن میکروفون';
        el('microphone').setAttribute('aria-pressed', String(micOn));
        el('microphone').setAttribute('aria-label', micAction);
        el('microphone').title = live && !maySpeak(local) ? 'برای روشن‌کردن میکروفون از دبیر نوبت بگیر' : micAction;
        const cam = el('camera');
        const camOn = live && !!active.cameraTrack && !!local.isCameraEnabled && mayCamera(local);
        const canCamera = live && !!state.cameraCodec && mayCamera(local);
        const camAction = camOn ? 'خاموش‌کردن دوربین' : 'روشن‌کردن دوربین';
        cam.hidden = !canCamera;
        cam.disabled = !canCamera || !!active?.cameraBusy;
        cam.setAttribute('aria-pressed', String(camOn));
        cam.setAttribute('aria-label', camAction); cam.title = camAction;
        el('enable-audio').hidden = !live || active.room.canPlaybackAudio;
        updateSubscriptions();
        const canShare = renderScreen();
        root.dataset.extraControls = live && (staff || canCamera || canShare || !el('enable-audio').hidden) ? 'yes' : 'no';
        el('chat-text').disabled = !live || busy;
        el('chat-empty').hidden = chatCursor > 0;
        for (const audio of el('audio-tracks').children) audio.muted = !live;
        renderPeople();
        renderSpeakers();
        renderCameras();
    }

    function clearActivity() {
        if (admitted || chatCursor || board.position().board) {
            el('chat-log').replaceChildren();
            el('chat-text').value = '';
            board.reset();
        }
        if (active) { active.speakers = []; stopCamera(active); stopScreen(active); }
        admitted = false; inkAt = aimAt = Infinity; inkRetryAt = 0;
        chatCursor = 0; seconds = {}; picture = '';
        delete state.profiles; delete state.profilesRev;
    }
    function release(link = active, warn = false) {
        if (!link || active !== link) return;
        const current = scope(link);
        active = null;
        stopCamera(link); stopScreen(link);
        clearActivity();
        flow++;
        el('audio-tracks').replaceChildren();
        if (warn && current.sid) api('leave', current, {keepalive:true}).catch(() => {});
        link.room.disconnect().catch(() => {});
        render();
    }
    function pushChat(values) {
        const messages = [];
        const log = el('chat-log');
        const anchor = log.scrollTop > 24 ? log.firstElementChild : null;
        const oldTop = anchor?.getBoundingClientRect().top;
        const added = [];
        for (const row of values || []) {
            if (row.n <= chatCursor) continue;
            const item = document.createElement('li');
            const name = document.createElement('bdi'); name.className = 'chat-by'; name.textContent = row.by;
            const text = document.createElement('span'); text.className = 'chat-text'; text.textContent = row.text;
            item.append(name,text);
            added.push(item);
            chatCursor = row.n;
            messages.push(row);
        }
        if (!added.length) return messages;
        log.prepend(...added.reverse());
        while (log.children.length > 120) log.lastElementChild.remove();
        el('chat-empty').hidden = true;
        // Preserve the old reading position if the pupil scrolled into the history.
        if (!anchor) log.scrollTop = 0;
        else if (anchor.parentNode === log) log.scrollTop += anchor.getBoundingClientRect().top - oldTop;
        return messages;
    }

    function makeRoom() {
        return new lk.Room({
            singlePeerConnection:false, // Preserve the tested first-speaker fix; publishing does not renegotiate the receiving PC.
            audioCaptureDefaults:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},
            publishDefaults:{audioPreset:{maxBitrate:12000,priority:'high'},dtx:true,red:false,forceStereo:false},
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
            link = {room, lesson:ticket.lesson, epoch:ticket.epoch, speakers:[], cameraAttempt:0, cameraTrack:null, cameraBusy:false,
                screenAttempt:0, screenTrack:null, screenBusy:false};
            active = link;
            flow++; // Ignore status requests made before this transport was installed.
            for (const event of [lk.RoomEvent.ParticipantConnected,lk.RoomEvent.ParticipantDisconnected,lk.RoomEvent.LocalTrackPublished,
                lk.RoomEvent.LocalTrackUnpublished,lk.RoomEvent.TrackPublished,lk.RoomEvent.TrackUnpublished,lk.RoomEvent.TrackMuted,lk.RoomEvent.TrackUnmuted,
                lk.RoomEvent.AudioPlaybackStatusChanged,lk.RoomEvent.ConnectionStateChanged]) {
                room.on(event, () => { if (active === link) render(); });
            }
            room.on(lk.RoomEvent.ActiveSpeakersChanged, speakers => {
                if (active !== link || !connected()) return;
                link.speakers = speakers;
                renderSpeakers();
            });
            room.on(lk.RoomEvent.TrackSubscribed, (track) => {
                if (active !== link) return;
                if (track.kind === lk.Track.Kind.Audio && track.source === micSource) {
                    const node = track.attach(); node.muted = !hasAccess(); el('audio-tracks').append(node);
                } else if (track.source === cameraSource) renderCameras();
                else if (track.source === screenSource) renderScreen();
            });
            room.on(lk.RoomEvent.TrackUnsubscribed, track => {
                if (track.kind === lk.Track.Kind.Audio) track.detach().forEach(node => node.remove());
                if (active === link && track.source === cameraSource) renderCameras();
                if (active === link && track.source === screenSource) renderScreen();
            });
            room.on(lk.RoomEvent.ParticipantPermissionsChanged, (_before, person) => {
                if (active !== link) return;
                if (person === room.localParticipant) {
                    if (!maySpeak(person) && person.isMicrophoneEnabled) person.setMicrophoneEnabled(false).catch(() => {});
                    if (!mayCamera(person)) stopCamera(link);
                    if (!mayScreen(person)) stopScreen(link);
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
            room.on(lk.RoomEvent.Reconnected, () => { if (active === link) { render(); refresh(); } });
            room.on(lk.RoomEvent.Disconnected, (reason) => {
                if (active !== link) return;
                const code = Number.isInteger(reason) ? lk.DisconnectReason?.[reason] : '';
                if (code === 'DUPLICATE_IDENTITY') {
                    wanted = false;
                    note('این حساب در دستگاه یا صفحهٔ دیگری وارد شده است. (DUPLICATE_IDENTITY)');
                } else if (!['ROOM_DELETED','CLIENT_INITIATED'].includes(code)) {
                    note(`ارتباط رسانه قطع شد. هر زمان کلاس آماده باشد، دوباره وصل می‌شویم. (${code || 'ROOM-DISCONNECTED'})`);
                }
                retryAt = Date.now() + 1700 + Math.random()*850;
                release(link, true); refresh();
            });
            await room.connect(ticket.url, ticket.token, {autoSubscribe:false});
            attempts = 0; retryAt = 0;
        } catch (error) {
            if (link && active === link) release(link);
            else await room.disconnect().catch(() => {});
            attempts++;
            retryAt = Date.now() + Math.min(15000, 1600 * (2 ** Math.min(4, attempts - 1))) + Math.random()*850;
            if (error instanceof PublicError && error.status === 403) { if (gesture) wanted = false; }
            if (gesture) throw error;
            if (!(error instanceof PublicError && error.status === 409)) note(error instanceof PublicError ? error.message : 'ورود رسانه ممکن نشد؛ اتصال را بررسی کن. (ROOM-CONNECT)');
        } finally {
            connecting = false;
            render(); refresh();
        }
    }

    function receiveBoard(view, local, requestedAt) {
        if (!view) return;
        board.sync(view, local, requestedAt);
        inkRead = performance.now();
        inkAt = inkRead + (view.more ? 85 : (view.moving || view.laser ? 280 : 850)) + Math.random()*(view.more ? 35 : 140);
    }
    async function poll(boardOnly) {
        if (polling || stopped || permanent) return;
        boardOnly &&= hasAccess() && !document.hidden;
        polling = true;
        if (!boardOnly) needsRefresh = false;
        const captured = flow;
        const local = connected() ? scope() : {};
        const requestedAt = performance.now();
        try {
            const position = {...board.position(), compact:true};
            if (boardOnly) {
                aimAt = Infinity;
                const result = await api('board-sync', {...local, ...position, ...board.takeAim()});
                if (captured === flow && !stopped && hasAccess() && local.sid === scope().sid) receiveBoard(result.board, local, requestedAt);
                return;
            }
            const people = !document.hidden && !el('people-panel').hidden;
            const result = await api('status', {...local, ...position, chat:chatCursor, people,
                profilesRev:hasAccess() ? state.profilesRev : undefined});
            if (captured !== flow || stopped) return;
            const prior = state;
            if (result.admitted && result.epoch === prior.epoch && result.lesson === prior.lesson
                && result.profilesRev && result.profilesRev === prior.profilesRev) result.profiles ??= prior.profiles;
            state = result;
            syncSlots();
            if (active && (active.epoch !== state.epoch || active.lesson !== state.lesson || !result.ready
                || !['live', ...(staff ? ['starting'] : [])].includes(state.phase))) release(active);
            if (staff && prior.lesson && state.phase === 'idle') { wanted = false; optedIntoTest = false; }
            // Observe/rejoin one lesson per entry. An old tab never starts the next lesson on its own.
            if (state.lesson || state.phase === 'reserved' || state.schedule?.choices.some(choice => choice.id === fridaySlot || choice.manual)
                || (!state.schedule?.timed && state.schedule?.choices.length > 1)) autoStart = false;
            if (active && local.sid && scope().sid === local.sid && connected() && result.admitted === true) {
                admitted = true; seconds = result.seconds || {};
                render();
                attention.snapshot(result.profiles, pushChat(result.chat), result.chatMore, active.room.localParticipant.identity);
                receiveBoard(result.board, scope(), requestedAt);
                needsRefresh ||= !!result.chatMore;
            } else clearActivity();
            if (!active && !connecting && wanted && state.ready && Date.now() >= retryAt
                && (!staff || state.slot !== fridaySlot || optedIntoTest)) {
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
            if (boardOnly) {
                // A full status read decides admission; do not let a busy ink read renew or tear down a lease.
                inkRetryAt = performance.now()+2200;
                needsRefresh = true;
                return;
            }
            release(active, true);
            clearActivity();
            state = {...state, ready:false, phase:state.lesson ? 'paused' : 'idle', resumable:false};
            permanent = error instanceof PublicError && [401,403,404].includes(error.status);
            if (permanent) wanted = false;
            note(error instanceof PublicError ? error.message : 'ارتباط با سایت قطع است؛ تا بازگشت اتصال، کلاس در انتظار می‌ماند.');
        } finally {
            polling = false;
            if (!boardOnly) {
                // Keep the supervisor pulse and first admission quick. Steady pupils can
                // poll less often, still comfortably inside the unchanged 12 s pulse TTL.
                const quick = (active || connecting) && (staff || !hasAccess());
                statusAt = performance.now() + (quick ? 1900 : hasAccess() ? 3500 : 4200)
                    + Math.random()*(quick ? 270 : 900);
                render();
            }
            schedulePoll();
        }
    }
    function schedulePoll() {
        if (stopped || permanent || polling) return;
        const now = performance.now();
        const heartbeat = needsRefresh ? 30 : Math.max(0,statusAt-now);
        const requested = Math.min(inkAt, Math.max(aimAt,inkRead+260));
        const read = !document.hidden && hasAccess() ? Math.max(0,requested-now,inkRetryAt-now) : Infinity;
        // One reader per tab, so a late ink response cannot undo a newer page or admission state.
        const quick = !needsRefresh && read + 100 < heartbeat;
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => poll(quick), quick ? read : heartbeat);
    }
    function refresh() {
        if (stopped || permanent) return;
        needsRefresh = true;
        schedulePoll();
    }

    for (const id of panels) {
        const button = el(`toggle-${id}`);
        const panel = el(`${id}-panel`);
        button.addEventListener('click', () => {
            panel.hidden = !panel.hidden;
            button.setAttribute('aria-expanded', String(!panel.hidden));
            if (!panel.hidden) panel.scrollIntoView?.({block:'nearest'});
            if (id === 'people') {
                if (panel.hidden) seconds = {};
                else refresh();
                renderPeople();
            }
        });
    }
    el('receive-video').addEventListener('change', event => {
        picture = event.target.value;
        render();
    });
    el('microphone').addEventListener('click', () => run(async () => {
        const link = active;
        const local = link?.room.localParticipant;
        if (!hasAccess() || !maySpeak(local)) return;
        const enabling = !local.isMicrophoneEnabled;
        try {
            await local.setMicrophoneEnabled(enabling);
            if (link !== active || !hasAccess() || !maySpeak(local)) { await local.setMicrophoneEnabled(false).catch(() => {}); return; }
            if (local.isMicrophoneEnabled !== enabling) note(microphoneFailure(local,enabling));
        } catch (error) { if (active === link && hasAccess()) note(microphoneFailure(local,enabling,error)); }
    }));
    el('camera').addEventListener('click', async () => {
        const link = active, codec = state.cameraCodec;
        const local = link?.room.localParticipant;
        if (!hasAccess() || !codec || !mayCamera(local) || link.cameraBusy) return;
        link.cameraBusy = true;
        const attempt = ++link.cameraAttempt;
        const valid = () => link === active && hasAccess() && state.cameraCodec === codec && mayCamera(local) && link.cameraAttempt === attempt;
        render();
        try {
            if (link.cameraTrack) { await stopCamera(link); return; }
            // This click is the only path to getUserMedia for video. A grant never calls it.
            const {width,height,frameRate} = cameraVideo;
            const track = await lk.createLocalVideoTrack({facingMode:'user',resolution:{width,height,frameRate}});
            if (!valid()) { track.stop(); return; }
            link.cameraTrack = track;
            track.mediaStreamTrack.addEventListener('ended', () => {
                if (link.cameraTrack === track) { stopCamera(link); if (active === link) render(); }
            }, {once:true});
            await local.publishTrack(track, {source:cameraSource, videoCodec:codec, simulcast:false, backupCodec:false,
                videoEncoding:{maxBitrate:cameraVideo.bitrate,maxFramerate:frameRate}});
        } catch (error) {
            if (valid()) {
                stopCamera(link);
                note(['NotAllowedError','PermissionDeniedError','SecurityError'].includes(error?.name)
                    ? 'مرورگر اجازهٔ استفاده از دوربین را نداده است. (CAMERA-ACCESS)'
                    : 'دوربین روشن نشد؛ دستگاه یا اتصال رسانه را بررسی کن. (CAMERA-START)');
            }
        } finally {
            link.cameraBusy = false;
            if (link === active) render();
        }
    });
    if (staff) el('screen-toggle').addEventListener('click', async () => {
        const link = active;
        if (!hasAccess() || !state.screenEnabled || state.cameraCodec !== 'vp8' || !canCaptureScreen || link.screenBusy || busy) return;
        if (state.screen && !itsScreen(link)) return;
        const local = scope(link), person = link.room.localParticipant;
        link.screenBusy = true;
        render();
        let raw;
        try {
            if (!itsScreen(link)) {
                const result = await api('screen-access',{...local,enabled:true});
                if (link !== active || !hasAccess() || scope().sid !== local.sid) return;
                flow++;
                state = {...state,screen:result.screen};
                note('آماده است؛ دوباره دکمهٔ اشتراک را بزن و یک پنجره یا زبانه انتخاب کن.');
                return;
            }
            if (link.screenTrack) { await giveBackScreen(link); return; }
            if (!mayScreen(person)) return;
            const attempt = ++link.screenAttempt;
            const current = () => link === active && hasAccess() && scope().sid === local.sid && link.screenAttempt === attempt
                && state.screenEnabled && itsScreen(link) && mayScreen(person);
            // Only this second, explicit click may open the browser picker. No saved capture or system sound.
            const {width,height,frameRate} = shareVideo;
            const limits = {width:{max:width},height:{max:height},frameRate:{ideal:frameRate,max:frameRate}};
            const stream = await window.navigator.mediaDevices.getDisplayMedia({video:limits,audio:false,systemAudio:'exclude',
                selfBrowserSurface:'exclude',surfaceSwitching:'exclude'});
            [raw] = stream.getVideoTracks();
            for (const track of stream.getTracks()) if (track !== raw) track.stop();
            if (!raw) throw new PublicError('مرورگر هیچ صفحه‌ای برای ارسال نداده است.');
            if (!current()) { raw.stop(); return; }
            raw.addEventListener('ended', () => {
                if (link.screenTrack?.mediaStreamTrack !== raw) return;
                giveBackScreen(link).catch(() => {}).finally(render);
            },{once:true});
            const oversized = () => {
                const size = raw.getSettings();
                return size.width > width || size.height > height || size.frameRate > frameRate + .1;
            };
            if (oversized()) await raw.applyConstraints(limits);
            if (oversized()) throw new PublicError('مرورگر نتوانست اندازه و سرعت اشتراک را محدود کند.');
            if (!current()) { raw.stop(); return; }
            if (raw.readyState !== 'live') throw new PublicError('اشتراک انتخاب‌شده پیش از ارسال متوقف شد.');
            raw.contentHint = 'detail';
            const track = new lk.LocalVideoTrack(raw);
            link.screenTrack = track;
            await person.publishTrack(track,{source:screenSource,videoCodec:'vp8',simulcast:false,backupCodec:false,
                screenShareEncoding:{maxBitrate:shareVideo.bitrate,maxFramerate:frameRate},degradationPreference:'maintain-resolution'});
            if (!current()) {
                if (link.screenTrack === track) stopScreen(link);
                else { track.stop(); await person.unpublishTrack(track,true).catch(() => {}); }
            }
        } catch (error) {
            raw?.stop();
            if (link === active && hasAccess()) {
                await giveBackScreen(link).catch(() => {});
                if (link === active) note(error instanceof PublicError ? error.message : error?.name === 'NotAllowedError'
                    ? 'انتخاب صفحه لغو شد یا مرورگر اجازه نداد.' : 'اشتراک صفحه شروع نشد؛ مرورگر و اتصال را بررسی کن. (SCREEN-START)');
            }
        } finally {
            link.screenBusy = false;
            if (link === active) { refresh(); render(); }
        }
    });
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
    el('leave').addEventListener('click', event => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        // The single exit goes back to the school panel without ending the lesson for everyone.
        wanted = false; stopped = true; flow++; clearTimeout(pollTimer);
        release(active,true);
    });
    if (staff) {
        el('slot').addEventListener('change', render);
        el('open').addEventListener('click', () => run(async () => {
            if (state.phase === 'closing' || state.ready === null || permanent) return;
            const choice = state.slot || el('slot').value;
            const slot = choice === fridaySlot ? choice : Number(choice);
            if (!slot) return;
            optedIntoTest = slot === fridaySlot;
            wanted = true;
            autoStart = false;
            retryAt = 0;
            await enter(() => api('open',{slot}), true);
        }));
        el('toggle-access').addEventListener('click', () => setAccessMenu(el('access-menu').hidden));
        document.addEventListener('click', event => {
            if (!el('access-menu').hidden && !el('room-permissions').contains(event.target)) setAccessMenu(false);
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !el('access-menu').hidden) { event.preventDefault(); setAccessMenu(false, true); }
        });
        for (const feature of Object.keys(features)) {
            el(`all-${feature}-give`).addEventListener('click', () => changeAccess(feature,true));
            el(`all-${feature}-cut`).addEventListener('click', () => changeAccess(feature,false));
        }
        el('close').addEventListener('click', () => {
            const test = state.slot === fridaySlot;
            if (!state.lesson || !window.confirm(test ? 'اتاق آزمایشی برای همه بسته شود؟ تا پایان جمعه می‌توان دوباره آن را باز کرد.' : 'کلاس برای همه پایان یابد و گزارش حضور ثبت شود؟')) return;
            run(async () => {
                wanted = false; optedIntoTest = false; flow++;
                const response = api('close',{lesson:state.lesson});
                release(active,true);
                await response;
                state = {phase:'idle', ready:null};
                syncSlots();
                note(test ? 'اتاق آزمایشی بسته شد؛ گزارشی ثبت نشد.' : 'زنگ پایان یافت؛ گزارش حضور در پنل ثبت شد.'); refresh();
            });
        });
    } else el('join').addEventListener('click', () => { wanted = true; retryAt = 0; note(); render(); refresh(); });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(pollTimer); release(active,true); });
    window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; refresh(); } });
    document.addEventListener('visibilitychange', () => { render(); if (!document.hidden) refresh(); });
    if (!lk || !window.isSecureContext) {
        permanent = true;
        note('برای ورود به رسانه به مرورگر سازگار، بستهٔ کامل کلاس و HTTPS معتبر نیاز است.');
    }
    render();
    if (!staff) statusAt = performance.now() + Math.random()*700; // Flatten the first waiting-room burst.
    schedulePoll();
})();
