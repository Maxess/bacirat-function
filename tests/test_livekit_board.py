"""Optional real pinned SFU and SDK check. Loopback only; no school or real tokens."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from test_native_livekit import ADDRESS, KEY, ROOT, SECRET, local_sfu, token
from test_webhook_setup import installer

ENV = ('MPM_TEST_LIVEKIT', 'MPM_TEST_CHROMIUM')
JOIN = r'''
async ticket => {
    const lk = LivekitClient;
    window.room = new lk.Room({singlePeerConnection:false});
    window.cues = [];
    room.on(lk.RoomEvent.DataReceived,(buf,person,kind,topic) => {
        cues.push({cue:JSON.parse(new TextDecoder().decode(buf)),identity:person?.identity,sid:person?.sid,
            grant:person?.permissions?.canPublishData, kind,topic});
    });
    await room.connect('ws://127.0.0.1:7880',ticket,{autoSubscribe:false,websocketTimeout:4500,peerConnectionTimeout:4500});
    return room.localParticipant.sid;
}
'''
# In this fixture both ICE transports are enabled. Read the active pair, not
# candidate enumeration or the scheme of the signaling socket.
PAIR = r'''
async direction => {
    const stats = await room.engine.pcManager[direction].getStats();
    for (const row of stats.values()) {
        if (row.type!=='transport' || !row.selectedCandidatePairId) continue;
        const chosen = stats.get(row.selectedCandidatePairId);
        if (chosen?.state!=='succeeded') continue;
        const local = stats.get(chosen.localCandidateId), remote = stats.get(chosen.remoteCandidateId);
        return [local.protocol, local.candidateType, remote.protocol, remote.candidateType, remote.port];
    }
    return [];
}
'''
SEND = r'''
async (seq) => {
    const payload=new TextEncoder().encode(JSON.stringify(['a'.repeat(24),room.localParticipant.sid,1,seq,'l',[[45,23,0]]]));
    try { await room.localParticipant.publishData(payload,{reliable:false,topic:'baci.board.cue.v1'}); } catch {}
    return payload.length;
}
'''


@unittest.skipUnless(all(os.environ.get(x) for x in ENV), 'needs isolated LiveKit and Chromium')
class LiveKitBoardTest(unittest.IsolatedAsyncioTestCase):
    async def test_laser_grant_revoke_and_selected_udp(self):
        from playwright.async_api import async_playwright
        room = 'test-board-loopback'
        deployment = {'api_key':KEY,'api_secret':SECRET}

        async def grant(enabled):
            jwt = installer.jwt(deployment, video={'room':room,'roomAdmin':True})
            body = json.dumps({'room':room,'identity':'teacher-1','permission':{
                'can_subscribe':True,'can_publish':False,'can_publish_data':enabled}})
            reply = await asyncio.to_thread(installer.post_json, ADDRESS+'/twirp/livekit.RoomService/UpdateParticipant',
                body.encode(), 'application/json', 'Bearer '+jwt, 3)
            self.assertIs(reply['permission'].get('can_publish_data',False),enabled)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with ZipFile(ROOT/'artifacts/baci-classroom-recorder-v0.4.23.zip') as archive:
                sdk = archive.extract('livekit-client.umd.js', directory)
            async with local_sfu(directory,loopback=True), async_playwright() as p:
                browser = await p.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],chromium_sandbox=True)
                try:
                    presenter, pupil = await browser.new_page(), await browser.new_page()
                    for page in (presenter,pupil):
                        await page.goto(ADDRESS,timeout=4000)
                        await page.add_script_tag(path=sdk)
                    sid = await presenter.evaluate(JOIN,token(room,'teacher-1',listener=True))
                    await pupil.evaluate(JOIN,token(room,'student-1',listener=True))
                    await presenter.evaluate(SEND,1)  # LiveKit suppresses listener-only traffic, even from a raw SDK call.
                    await grant(True)
                    await presenter.wait_for_function('room.localParticipant.permissions?.canPublishData === true',timeout=4500)
                    await pupil.wait_for_function("room.remoteParticipants.get('teacher-1')?.permissions?.canPublishData === true",timeout=4500)
                    size = await presenter.evaluate(SEND,3)  # Cue 2 is deliberately not delivered; no catch-up is needed.
                    self.assertLessEqual(size,512)
                    await pupil.wait_for_function('cues.length === 1',timeout=4500)
                    self.assertEqual(await pupil.evaluate('cues'),[{'cue':['a'*24,sid,1,3,'l',[[45,23,0]]],
                        'identity':'teacher-1','sid':sid,'grant':True,'kind':1,'topic':'baci.board.cue.v1'}])
                    for page, direction in ((presenter,'publisher'),(pupil,'subscriber')):
                        local, local_type, remote, remote_type, port = await page.evaluate(PAIR,direction)
                        self.assertEqual((local,remote,port),('udp','udp',7882))
                        self.assertNotIn('relay',(local_type,remote_type))
                    await grant(False)
                    await presenter.wait_for_function('room.localParticipant.permissions?.canPublishData === false',timeout=4500)
                    await presenter.evaluate(SEND,4)
                    await asyncio.sleep(.4)
                    self.assertEqual(await pupil.evaluate('cues.length'),1)
                finally:
                    await browser.close()
