(() => {
    'use strict';
    const el = (id) => document.getElementById(id);
    const root = el('classroom');
    if (!root) return;
    const lk = window.LivekitClient;
    const moderator = root.dataset.moderator === 'yes';
    const enter = el(moderator ? 'open' : 'join');
    let room = null;
    let busy = false;

    function message(value) { el('status').textContent = value; }
    function authenticatedRoomConnected() { return room?.state === lk.ConnectionState.Connected; }
    function maySpeak(person) {
        const permissions = person?.permissions;
        const sources = permissions?.canPublishSources;
        // The SDK's protobuf TrackSource.MICROPHONE is 2. An empty list is unrestricted.
        return permissions?.canPublish === true && Array.isArray(sources) && (sources.length === 0 || sources.includes(2));
    }

    function participantControl(person, title, action, successMessage) {
        const button = document.createElement('button');
        button.type = 'button';
        button.disabled = busy;
        button.textContent = title;
        button.setAttribute('aria-label', `${title}: ${person.name || person.identity}`);
        button.addEventListener('click', () => run(async () => {
            await api(action, { identity: person.identity });
            message(successMessage);
        }));
        return button;
    }

    function render() {
        const connected = authenticatedRoomConnected();
        const local = room?.localParticipant;
        enter.hidden = !!room;
        enter.disabled = busy;
        el('leave').hidden = !room;
        el('people-panel').hidden = !connected;
        const mic = el('microphone');
        mic.hidden = !connected;
        mic.disabled = busy || !maySpeak(local);
        mic.textContent = local?.isMicrophoneEnabled ? 'خاموش‌کردن میکروفون' : 'روشن‌کردن میکروفون';
        el('enable-audio').hidden = !connected || room.canPlaybackAudio;
        if (moderator) {
            for (const id of ['close', 'clear-floor']) {
                el(id).hidden = !connected;
                el(id).disabled = busy;
            }
        }
        const people = el('people');
        people.replaceChildren();
        if (!connected) return;
        for (const person of [local, ...room.remoteParticipants.values()]) {
            const li = document.createElement('li');
            const title = person.name || person.identity;
            const allowed = maySpeak(person);
            li.textContent = title + (person === local ? ' (شما)' : '') + (allowed ? ' · نوبت صدا' : '');
            const student = /^student-[1-9]\d{0,17}$/.test(person.identity);
            if (moderator && (!allowed || student)) {
                const controls = document.createElement('div');
                controls.className = 'participant-controls';
                if (!allowed) controls.append(participantControl(person, 'دادن نوبت صدا', 'give-mic', 'نوبت صدا داده شد. گوینده باید خودش میکروفون را روشن کند.'));
                if (student) {
                    controls.append(participantControl(person, 'قطع میکروفون', 'cut-mic', 'مجوز میکروفون این دانش‌آموز قطع شد.'));
                    controls.append(participantControl(person, 'قطع تصویر', 'cut-video', 'مجوز تصویر این دانش‌آموز بسته شد.'));
                }
                li.append(controls);
            }
            people.append(li);
        }
    }

    async function api(action, data = {}) {
        const response = await fetch(root.dataset.api || 'api.php', {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: {'Content-Type': 'application/json', 'X-Classroom-CSRF': root.dataset.csrf},
            body: JSON.stringify({action, ...data}),
        });
        const body = await response.json();
        if (!response.ok || body.ok !== true) {
            throw new PublicError(typeof body.message === 'string' ? body.message : 'درخواست به نتیجه نرسید.');
        }
        return body;
    }

    class PublicError extends Error {}
    async function run(action) {
        if (busy) return;
        busy = true;
        render();
        try {
            await action();
        } catch (error) {
            message(error instanceof PublicError ? error.message : 'عملیات انجام نشد. اتصال، مجوز مرورگر یا وضعیت کلاس را بررسی کن.');
        } finally {
            busy = false;
            render();
        }
    }

    function disconnected(active, reason = 'از کلاس خارج شدی. برای برگشت، دوباره وارد شو.') {
        if (room !== active) return;
        room = null;
        el('audio-tracks').replaceChildren();
        message(reason);
        render();
    }

    if (!lk) {
        enter.disabled = true;
        message('بستهٔ رابط رسانه بارگذاری نشد. نسخهٔ ZIP کامل را روی هاست آزمایشی بگذار.');
        return;
    }

    enter.addEventListener('click', () => run(async () => {
        if (room) return;
        if (!window.isSecureContext) throw new PublicError('برای کلاس به HTTPS معتبر نیاز است.');
        const active = new lk.Room({
            audioCaptureDefaults: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
            publishDefaults: {audioPreset: {maxBitrate: 24000}, dtx: true, red: false, forceStereo: false},
        });
        room = active;
        render();
        message('در حال ورود…');
        // Try within the click gesture; keep an explicit playback button if blocked.
        active.startAudio().catch(() => {});
        for (const event of [lk.RoomEvent.ParticipantConnected, lk.RoomEvent.ParticipantDisconnected, lk.RoomEvent.TrackMuted,
            lk.RoomEvent.TrackUnmuted, lk.RoomEvent.LocalTrackPublished, lk.RoomEvent.LocalTrackUnpublished,
            lk.RoomEvent.AudioPlaybackStatusChanged, lk.RoomEvent.ConnectionStateChanged]) {
            active.on(event, render);
        }
        active.on(lk.RoomEvent.TrackSubscribed, (track) => {
            if (active === room && track.kind === lk.Track.Kind.Audio) el('audio-tracks').append(track.attach());
        });
        active.on(lk.RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((node) => node.remove()));
        active.on(lk.RoomEvent.ParticipantPermissionsChanged, (before, person) => {
            if (active !== room) return;
            if (person === active.localParticipant) {
                const allowed = maySpeak(person);
                if (!allowed) person.setMicrophoneEnabled(false).catch(() => {});
                if (!moderator && before && maySpeak({ permissions: before }) !== allowed) {
                    message(allowed ? 'اجازهٔ میکروفون داری. برای صحبت، خودت آن را روشن کن.'
                        : 'نوبت میکروفون گرفته شد. هنوز می‌توانی صدای کلاس را بشنوی.');
                }
            }
            render();
        });
        active.on(lk.RoomEvent.Reconnecting, () => message('اتصال ناپایدار است… اگر قطع شد، دوباره وارد شو.'));
        active.on(lk.RoomEvent.Reconnected, () => { message('اتصال برقرار شد.'); render(); });
        active.on(lk.RoomEvent.Disconnected, () => disconnected(active));
        try {
            const ticket = await api(moderator ? 'open' : 'join');
            await active.connect(ticket.url, ticket.token);
            message(moderator ? 'وارد شدی. از فهرست حاضران نوبت صدا بده یا مجوز هر دانش‌آموز را قطع کن.' : 'وارد شدی. اجازهٔ میکروفون را دبیر می‌دهد.');
        } catch (error) {
            await active.disconnect();
            disconnected(active);
            throw error;
        }
    }));

    el('microphone').addEventListener('click', () => run(async () => {
        const local = room?.localParticipant;
        if (!maySpeak(local)) return;
        try {
            await local.setMicrophoneEnabled(!local.isMicrophoneEnabled);
            message(local.isMicrophoneEnabled ? 'میکروفون روشن است.' : 'میکروفون خاموش است.');
        } catch (_) {
            throw new PublicError('میکروفون روشن نشد؛ اجازهٔ مرورگر، دستگاه و نوبت صدا را بررسی کن.');
        }
    }));
    el('enable-audio').addEventListener('click', () => run(() => room?.startAudio()));
    el('leave').addEventListener('click', () => run(async () => {
        const active = room;
        if (active) {
            await active.disconnect();
            disconnected(active);
        }
    }));
    if (moderator) {
        el('clear-floor').addEventListener('click', () => run(async () => {
            await api('cut-all-mics');
            message('نوبت میکروفون از همه گرفته شد.');
        }));
        el('close').addEventListener('click', () => {
            if (window.confirm('کلاس برای همه بسته شود؟')) run(async () => {
                await api('close');
                const active = room;
                if (active) await active.disconnect();
                disconnected(active, 'کلاس پایان یافت.');
            });
        });
    }
    window.addEventListener('pagehide', () => room?.disconnect());
    render();
})();
