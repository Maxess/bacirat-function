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
    function render() {
        const connected = authenticatedRoomConnected();
        const local = room?.localParticipant;
        enter.hidden = !!room;
        enter.disabled = busy;
        el('leave').hidden = !room;
        el('people-panel').hidden = !connected;
        const mic = el('microphone');
        mic.hidden = !connected;
        mic.disabled = busy || !local?.permissions?.canPublish;
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
            li.textContent = title + (person === local ? ' (شما)' : '') + (person.permissions?.canPublish ? ' · نوبت صدا' : '');
            if (moderator && !person.permissions?.canPublish) {
                const button = document.createElement('button');
                button.type = 'button';
                button.disabled = busy;
                button.textContent = 'دادن نوبت';
                button.addEventListener('click', () => run(async () => {
                    await api('floor', { identity: person.identity });
                    message('نوبت صدا تغییر کرد. گوینده باید خودش میکروفون را روشن کند.');
                }));
                li.append(button);
            }
            people.append(li);
        }
    }

    async function api(action, data = {}) {
        const response = await fetch('api.php', {
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
            lk.RoomEvent.TrackUnmuted, lk.RoomEvent.AudioPlaybackStatusChanged, lk.RoomEvent.ConnectionStateChanged]) {
            active.on(event, render);
        }
        active.on(lk.RoomEvent.TrackSubscribed, (track) => {
            if (active === room && track.kind === lk.Track.Kind.Audio) el('audio-tracks').append(track.attach());
        });
        active.on(lk.RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((node) => node.remove()));
        active.on(lk.RoomEvent.ParticipantPermissionsChanged, (_before, person) => {
            if (person === active.localParticipant && !person.permissions?.canPublish) {
                person.setMicrophoneEnabled(false).catch(() => {});
            }
            render();
        });
        active.on(lk.RoomEvent.Reconnecting, () => message('اتصال ناپایدار است… اگر قطع شد، دوباره وارد شو.'));
        active.on(lk.RoomEvent.Reconnected, () => { message('اتصال برقرار شد.'); render(); });
        active.on(lk.RoomEvent.Disconnected, () => disconnected(active));
        try {
            const ticket = await api(moderator ? 'open' : 'join');
            await active.connect(ticket.url, ticket.token);
            message(moderator ? 'وارد شدی. از فهرست حاضران یک گوینده انتخاب کن.' : 'وارد شدی. اجازهٔ میکروفون را دبیر می‌دهد.');
        } catch (error) {
            await active.disconnect();
            disconnected(active);
            throw error;
        }
    }));

    el('microphone').addEventListener('click', () => run(async () => {
        const local = room?.localParticipant;
        if (!local?.permissions?.canPublish) return;
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
            await api('floor', {identity: null});
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
