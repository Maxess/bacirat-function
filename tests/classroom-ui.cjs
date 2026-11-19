const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/classroom.js'), 'utf8');
const listener = {canPublish: false, canPublishSources: [], canSubscribe: true};
const mic = {...listener, canPublish: true, canPublishSources: [2]};

class Element {
    constructor() { this.children = []; this.handlers = {}; this.textContent = ''; }
    replaceChildren() { this.children = []; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(event, handler) { this.handlers[event] = handler; }
    async click() { if (!this.disabled && !this.hidden) await this.handlers.click(); }
}

function participant(identity) {
    return {
        identity, name: identity, permissions: listener, isMicrophoneEnabled: false, micCalls: [],
        async setMicrophoneEnabled(enabled) {
            this.micCalls.push(enabled);
            if (enabled && this.micError) {
                this.lastMicrophoneError = this.captureError ? this.micError : undefined;
                throw this.micError;
            }
            this.isMicrophoneEnabled = enabled;
        },
    };
}

function fixture(moderator) {
    const ids = ['classroom', 'status', 'people', 'people-panel', 'leave', 'microphone', 'mic-feedback', 'enable-audio', 'audio-tracks',
        ...(moderator ? ['open', 'close', 'clear-floor'] : ['join'])];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
    nodes.classroom.dataset = { moderator: moderator ? 'yes' : 'no', api: 'school-plugins.php?plugin=school-classroom&route=api', csrf: 'test-csrf' };
    let room;
    const localIdentity = moderator ? 'teacher-1' : 'student-1';
    class Room extends EventEmitter {
        constructor(options) {
            super();
            this.options = options;
            room = this;
            this.localParticipant = participant(localIdentity);
            this.remoteParticipants = new Map((moderator ? ['student-1', 'student-2', 'admin-1'] : ['teacher-1', 'student-2'])
                .map(id => [id, participant(id)]));
            this.canPlaybackAudio = true;
        }
        async startAudio() { if (this.playbackError) throw this.playbackError; }
        async connect() { this.state = 'connected'; this.emit('ConnectionStateChanged'); }
        async disconnect() { this.state = 'disconnected'; this.emit('Disconnected'); }
    }
    const requests = [];
    const api = {
        fail: false,
        change(id, permissions) {
            const target = id === localIdentity ? room.localParticipant : room.remoteParticipants.get(id);
            const before = target.permissions;
            target.permissions = permissions;
            room.emit('ParticipantPermissionsChanged', before, target);
        },
    };
    vm.runInNewContext(script, {
        window: {isSecureContext: true, addEventListener() {}, LivekitClient: {
            ConnectionState: {Connected: 'connected', Reconnecting: 'reconnecting', SignalReconnecting: 'signalReconnecting'},
            DisconnectReason: {15: 'MEDIA_FAILURE'}, Track: {Kind: {Audio: 'audio'}}, Room,
            RoomEvent: new Proxy({}, {get: (_, key) => key}),
        }},
        document: {getElementById: id => nodes[id] ?? null, createElement: () => new Element()},
        async fetch(url, options) {
            assert.equal(url, nodes.classroom.dataset.api);
            assert.equal(options.method, 'POST');
            assert.equal(options.headers['X-Classroom-CSRF'], 'test-csrf');
            const body = JSON.parse(options.body);
            requests.push(body);
            if (api.fail) return {ok: false, json: async () => ({ok: false, message: 'SFU verification failed'})};
            if (body.action === 'give-mic') api.change(body.identity, mic);
            if (body.action === 'cut-mic') api.change(body.identity, listener);
            if (body.action === 'cut-all-mics') {
                for (const p of [room.localParticipant, ...room.remoteParticipants.values()]) api.change(p.identity, listener);
            }
            return {ok: true, json: async () => ({ok: true, url: 'wss://media.example.test', token: 'fixture'})};
        },
    });
    function controls(id) {
        return nodes.people.children.find(row => row.textContent.startsWith(id)).children[0]?.children ?? [];
    }
    return {nodes, requests, api, controls, get room() { return room; }};
}

test('moderator has separate per-student controls and waits for a verified API result', async () => {
    const ui = fixture(true);
    await ui.nodes.open.click();
    assert.equal(ui.room.options.singlePeerConnection, false);
    assert.deepEqual(ui.controls('student-1').map(x => x.textContent), ['دادن نوبت صدا', 'قطع میکروفون', 'قطع تصویر']);
    assert.equal(ui.controls('admin-1').length, 1);
    await ui.controls('student-1')[0].click();
    await ui.controls('student-2')[0].click();
    assert.deepEqual(ui.requests.slice(1), [
        {action: 'give-mic', identity: 'student-1'}, {action: 'give-mic', identity: 'student-2'},
    ]);
    assert.equal(ui.room.remoteParticipants.get('student-1').permissions, mic);
    assert.deepEqual(ui.room.remoteParticipants.get('student-1').micCalls, []);
    await ui.controls('student-1')[1].click();
    assert.deepEqual(ui.requests.at(-1), {action: 'cut-video', identity: 'student-1'});
    assert.equal(ui.room.remoteParticipants.get('student-1').permissions, mic);
    await ui.controls('student-1')[0].click();
    assert.deepEqual(ui.requests.at(-1), {action: 'cut-mic', identity: 'student-1'});
    assert.equal(ui.room.remoteParticipants.get('student-2').permissions, mic);
    ui.api.fail = true;
    await ui.nodes['clear-floor'].click();
    assert.deepEqual(ui.requests.at(-1), {action: 'cut-all-mics'});
    assert.equal(ui.nodes.status.textContent, 'SFU verification failed');
    ui.api.fail = false;
    await ui.nodes['clear-floor'].click();
    assert.match(ui.nodes.status.textContent, /از همه گرفته شد/);
    assert.equal(ui.room.remoteParticipants.get('student-2').permissions, listener);
});

test('student enters as a listener, grant does not activate the microphone, and revoke leaves incoming audio alone', async () => {
    const ui = fixture(false);
    await ui.nodes.join.click();
    const local = ui.room.localParticipant;
    assert.equal(ui.room.options.singlePeerConnection, false);
    assert.equal(ui.nodes.microphone.disabled, true);
    assert.deepEqual(ui.controls('student-2'), []);
    ui.api.change(local.identity, mic);
    assert.equal(ui.nodes.microphone.disabled, false);
    assert.deepEqual(local.micCalls, []);
    await ui.nodes.microphone.click();
    assert.deepEqual(local.micCalls, [true]);
    const audioNode = new Element();
    ui.room.emit('TrackSubscribed', {kind: 'audio', attach: () => audioNode});
    ui.api.change(local.identity, {...mic, canPublishSources: [1]}); // camera-only must not enable microphone
    assert.equal(ui.nodes.microphone.disabled, true);
    assert.deepEqual(local.micCalls, [true, false]);
    assert.deepEqual(ui.nodes['audio-tracks'].children, [audioNode]);
    await ui.nodes.leave.click();
    await ui.nodes.join.click();
    assert.equal(ui.nodes.microphone.disabled, true);
    assert.deepEqual(ui.room.localParticipant.micCalls, []);
});

test('mic capture, grant, negotiation, publication, and receiver playback have distinct safe feedback', async () => {
    const ui = fixture(false);
    const {nodes} = ui;
    await nodes.join.click();
    const local = ui.room.localParticipant;
    ui.api.change(local.identity, mic);
    for (const [name, capture, status, code] of [
        ['NotAllowedError', true, undefined, 'MIC-ACCESS'],
        ['NotReadableError', true, undefined, 'MIC-CAPTURE'],
        ['OperationError', false, undefined, 'MIC-RTC'],
        ['PublishTrackError', false, 403, 'MIC-GRANT'],
        ['PublishTrackError', false, 408, 'MIC-CONNECTION'],
        ['Error', false, undefined, 'MIC-PUBLISH'],
    ]) {
        local.micError = Object.assign(new Error('private signaling data'), {name, status});
        local.captureError = capture;
        await nodes.microphone.click();
        assert.equal(local.isMicrophoneEnabled, false);
        assert.equal(nodes['mic-feedback'].hidden, false);
        assert.ok(nodes['mic-feedback'].textContent.endsWith(`(${code})`));
        assert.doesNotMatch(nodes['mic-feedback'].textContent, /private signaling data/);
    }
    local.micError = undefined;
    const toggle = local.setMicrophoneEnabled;
    local.setMicrophoneEnabled = async () => {}; // SDK resolved without publishing the track.
    await nodes.microphone.click();
    assert.match(nodes['mic-feedback'].textContent, /MIC-PUBLISH/);
    local.setMicrophoneEnabled = toggle;
    await nodes.microphone.click();
    assert.equal(local.isMicrophoneEnabled, true);
    assert.equal(nodes['mic-feedback'].hidden, true);
    assert.match(nodes.status.textContent, /برای ارسال صدا روشن است/);
    ui.room.canPlaybackAudio = false;
    ui.room.playbackError = new Error('private playback data');
    ui.room.emit('AudioPlaybackStatusChanged');
    assert.equal(nodes['enable-audio'].hidden, false);
    const micCalls = local.micCalls.length;
    await nodes['enable-audio'].click();
    assert.match(nodes.status.textContent, /AUDIO-PLAYBACK/);
    assert.doesNotMatch(nodes.status.textContent, /private playback data/);
    assert.equal(local.micCalls.length, micCalls);
});

test('reconnecting keeps the mic visible but disabled and preserves a safe failure after disconnect', async () => {
    const ui = fixture(false);
    const {nodes} = ui;
    await nodes.join.click();
    const active = ui.room;
    const local = active.localParticipant;
    ui.api.change(local.identity, mic);
    local.setMicrophoneEnabled = async () => {
        active.state = 'signalReconnecting';
        active.emit('ConnectionStateChanged');
        active.emit('SignalReconnecting');
        throw Object.assign(new Error('private negotiation data'), {name: 'OperationError'});
    };
    await nodes.microphone.click();
    assert.equal(nodes.microphone.hidden, false);
    assert.equal(nodes.microphone.disabled, true);
    assert.match(nodes.status.textContent, /اتصال دوباره/);
    assert.match(nodes['mic-feedback'].textContent, /MIC-RTC/);
    active.state = 'connected';
    active.emit('ConnectionStateChanged');
    active.emit('Reconnected');
    assert.equal(nodes.microphone.disabled, false);
    active.state = 'disconnected';
    active.emit('Disconnected', 15);
    assert.equal(nodes.microphone.hidden, true);
    assert.equal(nodes.join.hidden, false);
    assert.match(nodes.status.textContent, /MEDIA_FAILURE/);
    assert.match(nodes['mic-feedback'].textContent, /MIC-RTC/);
    await nodes.join.click();
    assert.equal(nodes['mic-feedback'].hidden, true);
    assert.equal(nodes.microphone.disabled, true);
});
