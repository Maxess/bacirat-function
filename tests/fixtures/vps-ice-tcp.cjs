// Test only: real Chrome clients on a private TAP to the exact installed VPS.
// The school origin is fulfilled locally; TLS, admission, ICE and RTP hit the VM.
const assert = require('node:assert/strict');
const { createHmac } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const [, , dir, mode='tcp'] = process.argv;
assert.ok(mode==='tcp' || mode==='blocked');
const { chromium } = require(process.env.BACI_QA_PLAYWRIGHT);
const pair = JSON.parse(readFileSync(join(dir, 'pair.json'), 'utf8'));
const school = 'https://school.example.test/campus/tcp-qa';
const signal = 'wss://media.example.test';
assert.equal(pair.signal_url, signal, 'Only the isolated fixture host is supported');
const room = 'baci-tcp-qa';
const ids = ['teacher-11', 'student-12'];
const encode = data => Buffer.from(JSON.stringify(data)).toString('base64url');

function jwt(video, sub) {
    const now = Math.floor(Date.now() / 1000);
    const message = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ iss: pair.api_key, sub, nbf: now - 5, exp: now + 120, video })}`;
    return `${message}.${createHmac('sha256', pair.api_secret).update(message).digest('base64url')}`;
}

async function rpc(method, body) {
    const response = await fetch(`https://media.example.test/twirp/livekit.RoomService/${method}`, {
        method: 'POST', signal: AbortSignal.timeout(8000), redirect: 'error',
        headers: { Authorization: `Bearer ${jwt({ roomCreate: true, roomAdmin: true, room }, 'admin-13')}`,
            'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    assert.equal(response.status, 200, `${method}: signed request not accepted`);
}

let stage = 'create';
(async () => {
    await rpc('CreateRoom', { name: room, empty_timeout: 60, max_participants: 2 });
    let browser;
    try {
        stage = 'browser';
        browser = await chromium.launch({ executablePath: process.env.BACI_QA_CHROMIUM,
            args: ['--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server'] });
        if (mode === 'blocked') {
            stage = 'WSS works but neither ICE path is available';
            const page = await browser.newPage();
            await page.route(`${school}**`, r => r.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Isolated no-ICE QA</title>' }));
            await page.goto(school);
            await page.addScriptTag({ path: join(dir, 'livekit-client.umd.js') });
            const token = jwt({ roomJoin: true, room, canSubscribe: true, canPublish: false, canPublishData: false }, ids[0]);
            const result = await page.evaluate(async ({ signal, token }) => {
                const r = new LivekitClient.Room();
                let signaling = false, connected = false;
                r.on(LivekitClient.RoomEvent.SignalConnected, () => { signaling = true; });
                try {
                    await r.connect(signal, token, { maxRetries: 0, websocketTimeout: 10000, peerConnectionTimeout: 6000 });
                    connected = true;
                } catch {} finally { await r.disconnect(); }
                return { signaling, connected };
            }, { signal, token });
            assert.deepEqual(result, { signaling: true, connected: false });
            console.log('QA_RESILIENCE With UDP 7882 and TCP 7881 blocked, trusted WSS joined signaling but no ICE connection was possible.');
            return;
        }
        const pages = [];
        for (const id of ids) {
            stage = `join-${id}`;
            const ctx = await browser.newContext({ permissions: ['microphone', 'camera'] });
            await ctx.route(`${school}**`, r => r.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Isolated TCP media QA</title>' }));
            const page = await ctx.newPage();
            pages.push(page);
            await page.goto(school);
            await page.addScriptTag({ path: join(dir, 'livekit-client.umd.js') });
            const token = jwt({ roomJoin: true, room, canSubscribe: true, canPublish: false, canPublishData: false }, id);
            await page.evaluate(async ({ signal, token }) => {
                const r = window.qaRoom = new LivekitClient.Room({ adaptiveStream: false, dynacast: false });
                await r.connect(signal, token, { maxRetries: 0, websocketTimeout: 10000, peerConnectionTimeout: 20000 });
            }, { signal, token });
        }
        for (const [i, page] of pages.entries()) {
            stage = `publish-${ids[i]}`;
            await rpc('UpdateParticipant', { room, identity: ids[i], permission: { can_subscribe: true, can_publish: true,
                can_publish_sources: i === 0 ? ['MICROPHONE', 'CAMERA'] : ['MICROPHONE'] } });
            await page.waitForFunction(() => window.qaRoom.localParticipant.permissions?.canPublish, undefined, { timeout: 6000 });
            await page.evaluate(async camera => {
                const raw = await navigator.mediaDevices.getUserMedia({ audio: true,
                    video: camera ? { width: 320, height: 240, frameRate: 10 } : false });
                for (const track of raw.getTracks()) {
                    const video = track.kind === 'video';
                    await qaRoom.localParticipant.publishTrack(track, { source: video ? LivekitClient.Track.Source.Camera : LivekitClient.Track.Source.Microphone,
                        videoCodec: 'vp8', simulcast: false });
                }
            }, i === 0);
        }
        stage = 'selected TCP candidates and decoded media';
        for (const [i, page] of pages.entries()) {
            await page.waitForFunction(async ({ other, needsVideo }) => {
                const remote = qaRoom.remoteParticipants.get(other);
                for (const source of needsVideo ? ['microphone', 'camera'] : ['microphone']) {
                    const report = await remote?.getTrackPublication(source)?.track?.getRTCStatsReport();
                    if (!report) return false;
                    const kind = source === 'camera' ? 'video' : 'audio';
                    const inbound = [...report.values()].find(row => row.type === 'inbound-rtp' && row.kind === kind && row.packetsReceived >= 10);
                    if (!inbound || (kind === 'video' && inbound.framesDecoded < 3)) return false;
                    const candidatePair = report.get(report.get(inbound.transportId)?.selectedCandidatePairId);
                    const server = report.get(candidatePair?.remoteCandidateId);
                    if (server?.protocol !== 'tcp' || server?.port !== 7881) return false;
                }
                return true;
            }, { other: ids[1 - i], needsVideo: i === 1 }, { timeout: 16000 });
        }
        console.log('QA_RESILIENCE Chrome peers exchanged Opus and decoded VP8 with UDP blocked; selected TCP ICE port 7881.');
    } finally {
        if (browser) await browser.close();
        await rpc('DeleteRoom', { room });
    }
})().catch(error => {
    // Credentials and token-bearing URLs must not be written to CI logs.
    console.error(`TCP fixture failed at ${stage}: ${error.name} ${error.cause?.code || ''}`);
    process.exitCode = 1;
});
