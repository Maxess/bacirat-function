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
    const isRepeatSlot = value => typeof value === 'string' && /^repeat-[a-f0-9]{32}$/.test(value);
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
    const profileKey = p => p && `${p.width}×${p.height}/${p.frameRate}/${p.bitrate}`;
    const screenToggle = el('screen-toggle'), screenAction = el('screen-action'), screenVideo = el('screen-video');
    const screenHint = el('screen-hint'), boardScreenHint = el('board-screen-hint');
    const qualityButton = el('quality-open'), qualityDialog = el('quality-dialog');
    const recordButton = el('record-start'), recordStart = el('record-start-dialog'), recordClose = el('record-close-dialog');
    const recordStatus = el('recording-status');
    const recordBoard = el('record-board-display'), recordScreen = el('record-screen-display');
    let closeIntent = null;
    const qualityFields = ['teacherCamera','studentCamera','teacherScreen','studentScreen'];
    const canCaptureCamera = typeof window.navigator?.mediaDevices?.getUserMedia === 'function';
    const displayPolicy = document.permissionsPolicy ?? document.featurePolicy;
    const screenBlocked = displayPolicy?.features?.().includes('display-capture') && displayPolicy.allowsFeature?.('display-capture') === false
        ? 'اجازهٔ مرورگر برای اشتراک صفحه در این برگه بسته است؛ کلاس را دوباره باز کن.'
        : typeof window.navigator?.mediaDevices?.getDisplayMedia !== 'function'
            ? 'این مرورگر امکان اشتراک صفحه ندارد. این محدودیت از مرورگر است، نه اجازهٔ کلاس؛ برای اشتراک از یک مرورگر سازگار روی رایانه وارد شو.' : '';
    const canCaptureScreen = !screenBlocked;
    const attention = window.ClassroomAttention(changeWake);
    let active = null, connecting = false, busy = false, polling = false, needsRefresh = false, permanent = false, stopped = false;
    let wanted = true, autoStart = staff, optedIntoTest = false, state = {phase:'idle', ready:null}, chatCursor = 0, seconds = {}, admitted = false;
    let pollTimer, flow = 0, retryAt = 0, attempts = 0;
    let statusAt = 0, inkAt = Infinity, aimAt = Infinity, inkRead = 0, inkRetryAt = 0;
    let peopleKey = '', peopleRows = new Map(), speakersKey = '', slotKey = null;
    const cameraTiles = new Map();
    const cameraPreview = el('camera-preview'), previewVideo = el('camera-preview-video');
    let previewTile = null;
    let shownScreen = null, shownPublication = null;
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
            if (!hasAccess() || (!staff && action !== 'board-ink') || ((file || action === 'board-page') && state.board?.navigate !== true)) {
                throw new PublicError('ویرایش تخته در این وضعیت مجاز نیست.');
            }
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
            if (!staff || !hasAccess() || (feature === 'board' && enabled && state.board?.navigate !== true)) return;
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

    function changePupilScreen(person) {
        return run(async () => {
            const link = active, {identity,sid} = person, local = scope();
            if (!staff || !hasAccess() || !state.pupilScreenEnabled || state.members?.[identity] !== sid) return;
            const selected = state.screen;
            if (selected && (selected.identity !== identity || selected.sid !== sid)) return;
            try {
                const result = await api('screen-access', {...local,identity,targetSid:sid,enabled:!selected});
                if (active !== link || !hasAccess() || scope().sid !== local.sid) return;
                flow++;
                state = {...state,screen:result.screen};
                if (result.screen) note('اجازه داده شد؛ دانش‌آموز باید خودش صفحه را انتخاب کند.');
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
            if (student(id)) {
                const btn = document.createElement('button');
                btn.type = 'button'; btn.className = 'icon-button'; btn.dataset.feature = 'screen';
                btn.append(permissionIcons.querySelector('.icon-present').cloneNode(true));
                btn.addEventListener('click', () => changePupilScreen(person));
                box.append(controls.screen = btn);
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
                const value = seconds[id];
                put(row.timer, Number.isInteger(value) ? duration(value) : '—');
            }
            for (const [feature, btn] of Object.entries(row.controls)) {
                if (feature === 'screen') {
                    const selected = state.screen?.identity === id && state.screen.sid === person.sid;
                    const label = `${selected ? 'لغو اجازهٔ' : 'دادن اجازهٔ'} اشتراک صفحه · ${personName(person)}`;
                    btn.hidden = state.pupilScreenEnabled !== true;
                    btn.disabled = busy || state.members?.[id] !== person.sid || (!!state.screen && !selected);
                    btn.dataset.granted = String(selected);
                    btn.setAttribute('aria-pressed', String(selected));
                    btn.setAttribute('aria-label', label); btn.title = label;
                    continue;
                }
                const on = feature === 'mic' ? maySpeak(person) && person.isMicrophoneEnabled
                    : feature === 'camera' ? mayCamera(person) && person.isCameraEnabled : false;
                const enabled = granted(feature, id);
                const label = `${features[feature].label} · ${personName(person)}`;
                btn.dataset.granted = String(enabled);
                btn.dataset.on = String(!!on);
                btn.setAttribute('aria-pressed', String(enabled));
                btn.setAttribute('aria-label', label);
                btn.title = `${enabled ? 'لغو اجازهٔ' : 'دادن اجازهٔ'} ${label}${on ? ' (روشن)' : ''}`;
                btn.hidden = (feature === 'camera' && !state.cameraCodec)
                    || (feature === 'board' && !enabled && state.board?.navigate !== true);
                btn.disabled = busy || state.members?.[id] !== person.sid || btn.hidden;
            }
        }
    }

    function stopCamera(link) {
        link.cameraAttempt++;
        const {cameraTrack:track, cameraCapture:capture} = link;
        link.cameraTrack = link.cameraCapture = link.cameraProfile = null;
        capture?.stop();
        if (!track) return;
        // End capture immediately, even if signaling is unavailable or permission was revoked mid-publish.
        track.stop();
        return link.room.localParticipant.unpublishTrack(track, true).catch(() => {});
    }
    function canReceiveVideo(person, pub) {
        if (!hasAccess() || document.hidden || pub.kind !== lk.Track.Kind.Video || pub.isMuted) return false;
        if (pub.source === cameraSource) return !!state.cameraCodec && mayCamera(person) && pub === person.getTrackPublication(cameraSource);
        return pub.source === screenSource && mayScreen(person) && person.identity === state.screen?.identity && person.sid === state.screen.sid
            && pub === person.getTrackPublication(screenSource);
    }

    // Subscribe only after confirmed admission. The presenter's screen and any permitted
    // webcams can coexist; neither changes voice/board, and a hidden tab stops receiving video.
    function updateSubscriptions() {
        if (!active || !connected()) return;
        const live = hasAccess();
        for (const person of active.room.remoteParticipants.values()) {
            for (const pub of person.trackPublications.values()) {
                const wanted = (live && pub.kind === lk.Track.Kind.Audio && pub.source === micSource) || canReceiveVideo(person, pub);
                if (pub.isDesired !== wanted) pub.setSubscribed(wanted);
            }
        }
    }

    function detachCameraPreview() {
        if (!previewTile) return;
        previewTile.track.detach(previewVideo);
        previewTile = null;
    }
    function showCameraPreview(tile) {
        if (!hasAccess() || document.hidden || cameraPreview.open || typeof cameraPreview.showModal !== 'function') return;
        detachCameraPreview();
        put(el('camera-preview-name'), tile.caption.textContent);
        cameraPreview.showModal();
        previewTile = tile;
        // Attach the same single-layer track. No new receiver, permission or capture.
        tile.track.attach(previewVideo);
    }
    cameraPreview.addEventListener('close', () => { if (!cameraPreview.open) detachCameraPreview(); });

    function renderCameras() {
        const strip = el('camera-strip');
        const cameraPeople = new Map();
        if (hasAccess() && state.cameraCodec && !document.hidden) {
            const room = active.room;
            for (const person of [room.localParticipant, ...room.remoteParticipants.values()]) {
                const pub = person.getTrackPublication(cameraSource);
                if (!mayCamera(person) || !pub?.track || pub.isMuted || pub.track.kind !== lk.Track.Kind.Video
                    || (person === room.localParticipant ? pub.track !== active.cameraTrack : !pub.isDesired || !canReceiveVideo(person, pub))) continue;
                cameraPeople.set(person.identity, {track:pub.track, name:personName(person)});
            }
        }
        for (const [id, tile] of cameraTiles) {
            if (cameraPeople.get(id)?.track === tile.track) continue;
            if (previewTile === tile) {
                if (cameraPreview.open) cameraPreview.close();
                detachCameraPreview();
            }
            tile.track.detach(tile.video); tile.figure.remove(); cameraTiles.delete(id);
        }
        for (const [id,{track,name}] of cameraPeople) {
            let tile = cameraTiles.get(id);
            if (!tile) {
                const figure = document.createElement('figure'), caption = document.createElement('figcaption');
                const video = document.createElement('video');
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'camera-tile-button';
                button.setAttribute('aria-haspopup', 'dialog');
                video.autoplay = true; video.playsInline = true; video.muted = true;
                figure.dataset.identity = id;
                track.attach(video);
                button.append(video); figure.append(button, caption); strip.append(figure);
                tile = {track,video,caption,figure,button}; cameraTiles.set(id,tile);
                button.addEventListener('click', () => showCameraPreview(tile));
            }
            put(tile.caption, name);
            const label = `بزرگ‌نمایی دوربین ${name}`;
            if (tile.button.title !== label) {
                tile.button.title = label;
                tile.button.setAttribute('aria-label', label);
            }
            if (previewTile === tile) put(el('camera-preview-name'), name);
        }
        strip.hidden = !cameraTiles.size;
    }

    function itsScreen(link) {
        return link && state.screen?.identity === link.room.localParticipant.identity && state.screen.sid === link.room.localParticipant.sid;
    }
    function stopScreen(link) {
        link.screenAttempt++;
        const track = link.screenTrack;
        link.screenTrack = link.screenProfile = null;
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
    function playableScreen() {
        return shownPublication && screenVideo.readyState >= 2 && screenVideo.videoWidth > 0 ? shownPublication : null;
    }
    function renderArchiveControls() {
        const publication = playableScreen();
        const prefs = state.recording?.archive;
        const allowed = hasAccess() && state.board?.navigate === true && !!prefs;
        for (const [button,selected,visible] of [[recordBoard,prefs?.board === true,allowed],
            [recordScreen,!!publication && prefs?.screen === publication.track,allowed && !!publication]]) {
            if (!button) continue;
            button.hidden = !visible;
            button.disabled = !visible || busy;
            button.setAttribute('aria-pressed',String(selected && visible));
        }
    }
    function setArchiveSource(source) {
        return run(async () => {
            const prefs = state.recording?.archive;
            const publication = source === 'screen' ? playableScreen() : null;
            if (!hasAccess() || !prefs || state.board?.navigate !== true || (source === 'screen' && !publication)) return;
            const link = active, local = scope(link), recording = state.recording.id;
            const enabled = source === 'board' ? !prefs.board : prefs.screen !== publication.track;
            try {
                const reply = await api('record-source',{...local,source,enabled,...publication});
                if (link !== active || !hasAccess() || scope().sid !== local.sid || state.recording?.id !== recording) return;
                flow++;
                state = {...state,recording:{...state.recording,archive:reply.archive}};
            } finally { refresh(); }
        });
    }
    function renderScreen() {
        const live = hasAccess();
        const selected = state.screen;
        const local = active?.room.localParticipant;
        const mine = live && itsScreen(active);
        if (active?.screenProfile && (!mine || !state.screenEnabled || !mayScreen(local))) stopScreen(active);
        let person, pub, display = null;
        if (live && selected && !document.hidden) {
            person = mine ? local : active.room.remoteParticipants.get(selected.identity);
            pub = person?.getTrackPublication(screenSource);
            if (person?.sid === selected.sid && mayScreen(person) && pub?.track?.kind === lk.Track.Kind.Video && !pub.isMuted
                && (mine ? pub.track === active.screenTrack : pub.isDesired && canReceiveVideo(person, pub))) display = pub.track;
        }
        if (shownScreen !== display) {
            shownScreen?.detach(screenVideo);
            display?.attach(screenVideo);
            shownScreen = display;
        }
        el('screen-panel').hidden = !display;
        if (display) put(el('screen-label'), `اشتراک صفحهٔ ${personName(person)}`);
        shownPublication = display && /^TR_[A-Za-z0-9_-]+$/.test(pub.trackSid ?? '') ? {track:pub.trackSid,publisherSid:selected.sid} : null;
        renderArchiveControls();
        if (!screenToggle) return false;
        const button = screenToggle;
        // The invitation is visible independently of picker support and the SFU update's arrival order.
        const configured = live && state.screenEnabled === true;
        const show = configured && (staff ? !!state.quality?.screen : mine);
        const sharing = !!(mine && active.screenTrack);
        const pending = !!mine && (!state.quality?.screen || !mayScreen(local));
        button.hidden = !show;
        button.disabled = !show || !canCaptureScreen || pending || busy || !!active?.screenBusy || (!!selected && !mine);
        const label = sharing ? 'توقف اشتراک صفحه' : !!selected && !mine ? 'ارائه‌دهندهٔ دیگری در حال اشتراک است'
            : mine ? 'اشتراک صفحه' : 'آماده‌سازی اشتراک صفحه';
        button.setAttribute('aria-label',label); button.title = label;
        button.setAttribute('aria-pressed',String(sharing));
        button.dataset.prepared = String(!!mine && !sharing);
        if (screenAction) put(screenAction, sharing ? 'توقف اشتراک' : 'اشتراک صفحه');
        const invited = show && !sharing;
        const reason = screenBlocked || (pending ? 'در حال همگام‌سازی اجازهٔ اشتراک صفحه…' : '');
        if (screenHint) {
            const hint = invited ? (staff ? screenBlocked : reason || 'دبیر اجازه داد؛ برای شروع، «اشتراک صفحه» را بزن.') : '';
            put(screenHint,hint);
            screenHint.hidden = !hint;
        }
        if (boardScreenHint) {
            const hint = invited ? (staff ? screenBlocked : reason || 'دبیر اجازهٔ اشتراک صفحه داد. برای شروع به نمای کلاس برگرد.') : '';
            put(boardScreenHint,hint);
            boardScreenHint.hidden = !hint;
        }
        return show;
    }

    function setAccessMenu(open, restoreFocus = false) {
        const menu = el('access-menu'), btn = el('toggle-access');
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        if (restoreFocus) btn.focus();
    }
    function renderAccessMenu(live) {
        el('access-features').hidden = !live;
        el('access-camera').hidden = !state.cameraCodec;
        el('camera-unavailable').hidden = !!state.cameraCodec;
        el('pupil-screen-hint').hidden = state.pupilScreenEnabled !== true;
        if (recordButton) {
            recordButton.hidden = !live || state.recording?.start !== true;
            recordButton.disabled = busy;
            if (recordButton.hidden && recordStart.open) recordStart.close();
        }
        if (qualityButton) {
            qualityButton.hidden = !live || !state.quality?.levels;
            qualityButton.disabled = busy;
            el('quality-save').disabled = busy;
            if (qualityButton.hidden && qualityDialog.open) qualityDialog.close();
        }
        for (const feature of Object.keys(features)) {
            const anyException = Object.keys(state.access?.[feature]?.members || {}).length > 0;
            const all = granted(feature);
            const give = el(`all-${feature}-give`), cut = el(`all-${feature}-cut`);
            give.setAttribute('aria-pressed', String(all && !anyException));
            cut.setAttribute('aria-pressed', String(!all && !anyException));
            give.hidden = feature === 'board' && state.board?.navigate !== true;
            give.disabled = !live || busy || give.hidden || (all && !anyException) || (feature === 'camera' && !state.cameraCodec);
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
        if (schedule.choices.some(choice => isRepeatSlot(choice.id))) return 'برای تکرار یک زنگ پایان‌یافته، «برگزاری مجدد» را انتخاب کن. گزارش این اجرا جدا خواهد بود.';
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
        const recording = state.recording;
        const capture = recording?.notice;
        root.dataset.recording = capture === 'live' ? 'yes' : 'no';
        if (recordStatus) {
            const text = capture === 'live' ? 'کلاس در حال ضبط است'
                : capture === 'preparing' ? 'ضبط کلاس در حال آماده‌سازی است'
                : capture === 'stopped' ? 'ضبط متوقف شد و فیلم ارسال نمی‌شود.' : '';
            put(recordStatus, text);
        }
        if (recordClose?.open && closeIntent?.lesson !== state.lesson) recordClose.close();
        el('live-panel').hidden = !live;
        el('waiting').hidden = live;
        root.dataset.live = live ? 'yes' : 'no';
        attention.setLive(live, state.wakeMode, state.wakeControl === true, busy);
        const status = live ? 'در جریان' : connectingNow ? 'در حال اتصال' : 'اتاق انتظار';
        put(el('status'), status);
        el('status-dot').title = status;
        const start = state.startTime || '';
        el('lesson-start').hidden = !start;
        put(el('lesson-start-time'), faDigits(start));
        for (const id of panels) el(`toggle-${id}`).disabled = !live;
        if (staff) {
            const slot = el('slot');
            const currentSlot = state.slot || 0;
            slot.disabled = !!currentSlot || busy;
            el('staff-start').hidden = live;
            el('open').disabled = busy || !!active || connecting || state.ready === null || permanent || phase === 'closing'
                || phase === 'reserved' || (!currentSlot && !slot.value) || (phase === 'paused' && !state.resumable);
            const test = (currentSlot || slot.value) === fridaySlot;
            el('open').textContent = test ? 'ورود به آزمایش جمعه' : phase === 'paused' ? 'ادامهٔ زنگ' : currentSlot ? 'پیوستن به زنگ'
                : isRepeatSlot(slot.value) ? 'برگزاری مجدد زنگ' : 'شروع زنگ';
            const end = currentSlot === fridaySlot ? 'بستن اتاق آزمایشی برای همه' : 'بستن کلاس برای همه';
            put(el('close-label'), end);
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
        if ((active?.cameraTrack || active?.cameraBusy) && (!live || !state.cameraCodec || !mayCamera(local))) stopCamera(active);
        if (live) {
            // Do not reopen a device or browser picker automatically after a profile change.
            const cameraChanged = active.cameraProfile && active.cameraProfile !== profileKey(state.quality?.camera);
            const screenChanged = active.screenProfile && active.screenProfile !== profileKey(state.quality?.screen);
            if (cameraChanged) stopCamera(active);
            if (screenChanged) stopScreen(active);
            if (cameraChanged || screenChanged) note('کیفیت تصویر تغییر کرد؛ برای ادامه، دوربین یا اشتراک صفحه را دوباره روشن کن.');
        }
        const camOn = live && !!active.cameraTrack && !!local.isCameraEnabled;
        const canCamera = live && canCaptureCamera && !!state.cameraCodec && !!state.quality?.camera && mayCamera(local);
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
        chatCursor = 0; seconds = {};
        delete state.profiles; delete state.profilesRev;
        delete state.quality; delete state.qualityRev;
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
            const meta = document.createElement('div'); meta.className = 'chat-meta';
            const name = document.createElement('bdi'); name.className = 'chat-by'; name.textContent = row.by;
            meta.append(name);
            if (typeof row.clock === 'string' && /^\d{2}:\d{2}$/.test(row.clock)) {
                const stamp = document.createElement('time'); stamp.className = 'chat-clock'; stamp.dir = 'ltr';
                stamp.dateTime = row.clock; stamp.textContent = faDigits(row.clock);
                stamp.title = 'ساعت ارسال به وقت تهران';
                meta.append(stamp);
            }
            const text = document.createElement('span'); text.className = 'chat-text'; text.textContent = row.text;
            item.append(meta,text);
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
            adaptiveStream:false, // Enlarging a tile must not request another receiver or quality layer.
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
            link = {room, lesson:ticket.lesson, epoch:ticket.epoch, speakers:[], cameraAttempt:0, cameraTrack:null, cameraCapture:null, cameraBusy:false,
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
        // File/page commands and pupil permissions must use the same fresh authority
        // as the board toolbar, including a handoff received between status polls.
        const presenterChanged = staff && (view.navigate === true) !== (state.board?.navigate === true);
        state.board = view;
        board.sync(view, local, requestedAt);
        if (presenterChanged) {
            renderArchiveControls();
            renderAccessMenu(hasAccess());
            renderPeople();
        }
        inkRead = performance.now();
        // Keep the pointer visible while it fades, without continuing fast polling for an old snapshot.
        const freshLaser = (view.laser?.points?.at(-1)?.[2] ?? 0) - (inkRead - requestedAt) > 1200;
        inkAt = inkRead + (view.more ? 85 : (view.moving || view.viewMoving || freshLaser ? 280 : 850)) + Math.random()*(view.more ? 35 : 140);
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
                const result = await api('board-sync', {...local, ...position, ...board.takeAim(), ...board.takeView()});
                if (captured === flow && !stopped && hasAccess() && local.sid === scope().sid) receiveBoard(result.board, local, requestedAt);
                return;
            }
            const people = !document.hidden && !el('people-panel').hidden;
            const result = await api('status', {...local, ...position, chat:chatCursor, people,
                profilesRev:hasAccess() ? state.profilesRev : undefined, qualityRev:hasAccess() ? state.qualityRev : undefined});
            if (captured !== flow || stopped) return;
            const prior = state;
            if (result.admitted && result.epoch === prior.epoch && result.lesson === prior.lesson) {
                if (result.profilesRev && result.profilesRev === prior.profilesRev) result.profiles ??= prior.profiles;
                if (result.qualityRev && result.qualityRev === prior.qualityRev) result.quality ??= prior.quality;
            }
            state = result;
            syncSlots();
            if (active && (active.epoch !== state.epoch || active.lesson !== state.lesson || !result.ready
                || !['live', ...(staff ? ['starting'] : [])].includes(state.phase))) release(active);
            if (staff && prior.lesson && state.phase === 'idle') { wanted = false; optedIntoTest = false; }
            // Repeat choices never auto-open and don't change the ordinary timetable's auto-open rules.
            const regularChoices = (state.schedule?.choices || []).filter(choice => !isRepeatSlot(choice.id));
            // Observe/rejoin one lesson per entry. An old tab never starts the next lesson on its own.
            if (state.lesson || state.phase === 'reserved' || regularChoices.some(choice => choice.id === fridaySlot || choice.manual)
                || (!state.schedule?.timed && regularChoices.length > 1)) autoStart = false;
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
                    && regularChoices.length === 1) {
                    autoStart = false;
                    enter(() => api('open',{slot:regularChoices[0].id}));
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
    if (qualityButton) {
        const errorMessage = el('quality-message');
        qualityButton.addEventListener('click', () => {
            if (!hasAccess() || !state.quality?.levels || busy) return;
            for (const key of qualityFields) el(`quality-${key}`).value = state.quality.levels[key];
            errorMessage.hidden = true;
            setAccessMenu(false);
            qualityDialog.showModal();
        });
        el('quality-cancel').addEventListener('click', () => qualityDialog.close());
        qualityDialog.addEventListener('close', () => el('toggle-access').focus());
        el('quality-form').addEventListener('submit', event => {
            event.preventDefault();
            return run(async () => {
                if (!hasAccess() || !state.quality?.levels) return;
                const link = active, local = scope();
                const levels = Object.fromEntries(qualityFields.map(key => [key,el(`quality-${key}`).value]));
                errorMessage.hidden = true;
                try {
                    const result = await api('set-quality', {...local,levels});
                    if (active !== link || !hasAccess() || scope().sid !== local.sid) return;
                    flow++;
                    state = {...state,quality:result.quality,qualityRev:result.qualityRev};
                    qualityDialog.close();
                    note('کیفیت تصویر برای این کلاس ذخیره شد.');
                } catch (err) {
                    put(errorMessage, err instanceof PublicError ? err.message : 'ذخیرهٔ کیفیت کامل نشد؛ دوباره تلاش کن.');
                    errorMessage.hidden = false;
                } finally { refresh(); }
            });
        });
    }

    // Own both the physical capture and, if needed, its pre-encoder square crop.
    // Never publish a camera image in a size larger than the selected profile.
    function cameraCapture(raw, profile, ended) {
        let output = raw, input, timer, stopped = false;
        const stop = () => {
            if (stopped) return;
            stopped = true;
            clearTimeout(timer);
            if (output !== raw) output.stop();
            raw.stop();
            if (input) { input.pause(); input.srcObject = null; }
        };
        const prepare = async () => {
            const {width,height} = raw.getSettings();
            if (width === profile.width && height === profile.height) {
                // With a native crop, keep exact square dimensions even when the device rotates.
                try {
                    await raw.applyConstraints({width:{exact:width},height:{exact:height},aspectRatio:{exact:1},frameRate:{max:profile.frameRate}});
                    const size = raw.getSettings();
                    if (size.width === width && size.height === height && !(size.frameRate > profile.frameRate + .1)) return raw;
                } catch (_) { /* Use the fixed-size canvas instead. */ }
            }
            if (stopped || raw.readyState !== 'live') return null;
            const canvas = document.createElement('canvas');
            canvas.width = profile.width; canvas.height = profile.height;
            const ctx = canvas.getContext('2d', {alpha:false});
            const unsupported = () => new PublicError('این مرورگر نمی‌تواند تصویر مربعِ سبک وبکم را ارسال کند. (CAMERA-CROP)');
            if (!ctx || typeof canvas.captureStream !== 'function') throw unsupported();
            input = document.createElement('video');
            input.muted = true; input.playsInline = true;
            input.srcObject = new window.MediaStream([raw]);
            try { await input.play(); }
            catch (_) { if (stopped) return null; throw unsupported(); }
            if (stopped || raw.readyState !== 'live') return null;
            const stream = canvas.captureStream(profile.frameRate);
            const [track] = stream.getVideoTracks();
            for (const extra of stream.getTracks()) if (extra !== track) extra.stop();
            if (!track) throw unsupported();
            output = track;
            const draw = () => {
                if (stopped) return;
                if (raw.readyState !== 'live' || output.readyState !== 'live') { ended(); return; }
                const w = input.videoWidth, h = input.videoHeight, side = Math.min(w,h);
                try {
                    if (side > 0 && input.readyState >= 2) ctx.drawImage(input, (w-side)/2, (h-side)/2, side, side,
                        0, 0, canvas.width, canvas.height);
                } catch (_) { ended(); return; }
                timer = setTimeout(draw, 1000/profile.frameRate);
            };
            draw();
            return output;
        };
        return {prepare,stop};
    }

    el('camera').addEventListener('click', async () => {
        const link = active, codec = state.cameraCodec, cameraVideo = state.quality?.camera;
        const local = link?.room.localParticipant;
        if (!hasAccess() || !codec || !cameraVideo || !canCaptureCamera || !mayCamera(local) || link.cameraBusy) return;
        link.cameraBusy = true;
        const {sid} = scope(link);
        const attempt = ++link.cameraAttempt, profile = profileKey(cameraVideo);
        const valid = () => link === active && hasAccess() && scope(link).sid === sid && state.cameraCodec === codec
            && mayCamera(local) && link.cameraAttempt === attempt && profile === profileKey(state.quality?.camera);
        render();
        let raw, capture, keep = false;
        try {
            if (link.cameraTrack) { await stopCamera(link); return; }
            link.cameraProfile = profile;
            // The source can be larger than the encoded square to accommodate ordinary camera modes.
            const {width,height,frameRate} = cameraVideo;
            const video = {facingMode:{ideal:'user'}, width:{ideal:width,max:Math.max(width,256)}, height:{ideal:height,max:Math.max(height,144)},
                aspectRatio:{ideal:1}, frameRate:{ideal:frameRate,max:frameRate}};
            const openCamera = () => window.navigator.mediaDevices.getUserMedia({video,audio:false});
            let stream;
            try { stream = await openCamera(); }
            catch (error) {
                if (error?.name !== 'OverconstrainedError' || !valid()) throw error;
                // Devices with a higher minimum native mode still send only the configured square.
                video.width.max = 640; video.height.max = 480;
                stream = await openCamera();
            }
            [raw] = stream.getVideoTracks();
            for (const track of stream.getTracks()) if (track !== raw) track.stop();
            if (!raw) throw new PublicError('مرورگر تصویر دوربین را در اختیار کلاس نگذاشت.');
            if (!valid()) return;
            const ended = () => {
                if (link.cameraCapture !== capture) return;
                stopCamera(link);
                if (active === link) render();
            };
            capture = cameraCapture(raw, cameraVideo, ended);
            link.cameraCapture = capture;
            raw.addEventListener('ended', ended, {once:true});
            const oversized = () => {
                const size = raw.getSettings();
                return size.width > video.width.max || size.height > video.height.max || size.frameRate > frameRate + .1;
            };
            if (oversized()) await raw.applyConstraints(video);
            if (!valid()) return;
            if (oversized()) throw new PublicError('مرورگر نتوانست اندازه یا نرخ فریم وبکم را محدود کند. (CAMERA-SIZE)');
            const outgoing = await capture.prepare();
            if (!valid() || outgoing?.readyState !== 'live') return;
            if (outgoing !== raw) outgoing.addEventListener('ended', ended, {once:true});
            outgoing.contentHint = 'motion';
            const track = new lk.LocalVideoTrack(outgoing);
            link.cameraTrack = track;
            await local.publishTrack(track, {source:cameraSource, videoCodec:codec, simulcast:false, backupCodec:false,
                videoEncoding:{maxBitrate:cameraVideo.bitrate,maxFramerate:frameRate},degradationPreference:'maintain-resolution'});
            keep = valid();
            if (!keep && link.cameraTrack !== track) {
                // A late SDK publication can finish after stopCamera has already taken ownership away.
                track.stop();
                await local.unpublishTrack(track, true).catch(() => {});
            }
        } catch (error) {
            if (valid()) {
                note(error instanceof PublicError ? error.message
                    : ['NotAllowedError','PermissionDeniedError','SecurityError'].includes(error?.name)
                    ? 'مرورگر اجازهٔ استفاده از دوربین را نداده است. (CAMERA-ACCESS)'
                    : error?.name === 'OverconstrainedError' ? 'دستگاه کیفیت سبک وبکم را پشتیبانی نمی‌کند. (CAMERA-SIZE)'
                    : 'دوربین روشن نشد؛ دستگاه یا اتصال رسانه را بررسی کن. (CAMERA-START)');
            }
        } finally {
            if (!keep) {
                if (capture && link.cameraCapture === capture) stopCamera(link);
                else if (capture) capture.stop();
                else raw?.stop();
                if (link.cameraAttempt === attempt) link.cameraProfile = null;
            }
            link.cameraBusy = false;
            if (link === active) render();
        }
    });
    screenVideo?.addEventListener('loadeddata', renderArchiveControls);
    screenVideo?.addEventListener('emptied', renderArchiveControls);
    recordBoard?.addEventListener('click', () => setArchiveSource('board'));
    recordScreen?.addEventListener('click', () => setArchiveSource('screen'));
    screenToggle?.addEventListener('click', async () => {
        const link = active, shareVideo = state.quality?.screen;
        if (!hasAccess() || !state.screenEnabled || !shareVideo || !canCaptureScreen || link.screenBusy || busy) return;
        if (state.screen && !itsScreen(link)) return;
        const local = scope(link), person = link.room.localParticipant;
        link.screenBusy = true;
        render();
        let raw, attempt;
        try {
            if (!itsScreen(link)) {
                if (!staff) return; // A pupil can use a confirmed invitation, never grant one.
                const result = await api('screen-access',{...local,enabled:true});
                if (link !== active || !hasAccess() || scope().sid !== local.sid) return;
                flow++;
                state = {...state,screen:result.screen};
                note('آماده است؛ دوباره دکمهٔ اشتراک را بزن و یک پنجره یا زبانه انتخاب کن.');
                return;
            }
            if (link.screenTrack) { await giveBackScreen(link); return; }
            if (!mayScreen(person)) return;
            attempt = ++link.screenAttempt;
            const profile = link.screenProfile = profileKey(shareVideo);
            const current = () => link === active && hasAccess() && scope().sid === local.sid && link.screenAttempt === attempt
                && state.screenEnabled && itsScreen(link) && mayScreen(person) && profile === profileKey(state.quality?.screen);
            // A confirmed lease plus an explicit click is required. No saved capture or system sound.
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
            if (link === active && hasAccess() && (attempt === undefined || link.screenAttempt === attempt)) {
                await giveBackScreen(link).catch(() => {});
                if (link === active) note(error instanceof PublicError ? error.message : error?.name === 'NotAllowedError'
                    ? 'انتخاب صفحه لغو شد یا مرورگر اجازه نداد.' : 'اشتراک صفحه شروع نشد؛ مرورگر و اتصال را بررسی کن. (SCREEN-START)');
            }
        } finally {
            if (!link.screenTrack && link.screenAttempt === attempt) link.screenProfile = null;
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
        el('slot').addEventListener('change', () => { autoStart = false; render(); });
        el('open').addEventListener('click', () => run(async () => {
            if (state.phase === 'closing' || state.ready === null || permanent) return;
            const choice = state.slot || el('slot').value;
            const slot = choice === fridaySlot || isRepeatSlot(choice) ? choice : Number(choice);
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
            if (event.key === 'Escape' && !cameraPreview.open && !el('access-menu').hidden) { event.preventDefault(); setAccessMenu(false, true); }
        });
        for (const feature of Object.keys(features)) {
            el(`all-${feature}-give`).addEventListener('click', () => changeAccess(feature,true));
            el(`all-${feature}-cut`).addEventListener('click', () => changeAccess(feature,false));
        }
        if (recordButton) {
            recordButton.addEventListener('click', () => {
                if (!hasAccess() || busy || !state.recording?.start) return;
                setAccessMenu(false);
                recordStart.showModal();
            });
            el('record-start-cancel').addEventListener('click', () => recordStart.close());
            recordStart.addEventListener('close', () => el('toggle-access').focus());
            el('record-start-yes').addEventListener('click', () => {
                recordStart.close();
                run(async () => {
                    if (!hasAccess() || !state.recording?.start) return;
                    const local = scope();
                    const result = await api('start-recording',local);
                    if (state.lesson === local.lesson) state = {...state,recording:result.recording};
                    refresh();
                });
            });
            el('record-close-cancel').addEventListener('click', () => recordClose.close());
            recordClose.addEventListener('close', () => { closeIntent = null; el('toggle-access').focus(); });
            for (const [id,sendRecording] of [['record-close-yes',true],['record-close-no',false]]) {
                el(id).addEventListener('click', () => {
                    const intent = closeIntent;
                    recordClose.close();
                    if (intent && state.lesson === intent.lesson) finishClass(intent.lesson,false,{recording:intent.recording,sendRecording});
                });
            }
        }
        function finishClass(lesson, test, choice = {}) {
            run(async () => {
                setAccessMenu(false, true);
                const wasWanted = wanted;
                wanted = false; optedIntoTest = false; flow++;
                let reply;
                try { reply = await api('close',{lesson,...choice}); }
                catch (error) { wanted = wasWanted; refresh(); throw error; }
                release(active,true);
                state = {phase:'idle', ready:null};
                syncSlots();
                note(test ? 'اتاق آزمایشی بسته شد؛ گزارشی ثبت نشد.' : reply.recording === 'queued'
                    ? 'زنگ پایان یافت؛ فیلم برای ارسال به گروه کلاس در صف قرار گرفت.' : reply.recording === 'discarded'
                    ? 'زنگ پایان یافت؛ فیلم ارسال نمی‌شود و نسخهٔ موقت حذف خواهد شد.' : 'زنگ پایان یافت؛ گزارش حضور در پنل ثبت شد.');
                refresh();
            });
        }
        el('close').addEventListener('click', () => {
            const {lesson,recording} = state;
            const test = state.slot === fridaySlot;
            if (!lesson || busy) return;
            if (recording?.confirm && recordClose) {
                closeIntent = {lesson,recording:recording.id};
                put(el('record-close-help'), recording.canSend
                    ? `مقصد: ${recording.messenger} — ${recording.group}. «خیر» کلاس را بدون ارسال فیلم می‌بندد.`
                    : 'فقط دبیر همین زنگ یا مدیر مجاز می‌تواند ارسال فیلم را تأیید کند. با «خیر» کلاس بدون ارسال بسته می‌شود.');
                el('record-close-yes').disabled = recording.canSend !== true;
                setAccessMenu(false);
                recordClose.showModal();
            } else if (window.confirm(test ? 'اتاق آزمایشی برای همه بسته شود؟ تا پایان جمعه می‌توان دوباره آن را باز کرد.' : 'کلاس برای همه پایان یابد و گزارش حضور ثبت شود؟')) {
                finishClass(lesson,test);
            }
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
