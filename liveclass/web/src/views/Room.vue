<script setup>
/* اتاق کلاس LiveClass — چیدمان: استیج (ویدیوها | تخته | اشتراک صفحه) + پنل کناری (حضار | گفت‌وگو) + نوار ابزار شناور */
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import { api } from '../api.js'
import { Room, RoomEvent, Track } from 'livekit-client'
import VideoTile from '../components/VideoTile.vue'
import Board from '../components/Board.vue'
import { loadPrefs, savePrefsLocal, PRESETS } from '../prefs.js'

const props = defineProps({ mode: { type: String, required: true }, sessionId: [Number, String], brand: Object })

const creds = ref(null)
const error = ref('')
const connecting = ref(true)
const micOn = ref(true)
const camOn = ref(true)
const sharingScreen = ref(false)
const handRaised = ref(false)
const stage = ref('grid')              // grid | board | screen
const sideTab = ref('participants')   // participants | chat
const chat = ref([])
const chatInput = ref('')
const participants = shallowRef([])
const speakingIds = ref(new Set())
const unread = ref(0)
const boardOpen = ref(false)
const connected = ref(false)
const screenVideo = ref(null)
const boardEl = ref(null)
const audioSink = ref(null)
const pdfState = ref(null)          // {id, page, name}
const pdfTotal = ref(0)
const recordStatus = ref('idle')    // idle | recording | stopped
const videoPrefs = ref(loadPrefs())

let room = null
let heartbeatTimer = null
let pendingBoardOps = null
const hands = {}                      // identity -> bool
const audioEls = new Map()            // identity -> audio element

const isTeacher = computed(() => props.mode === 'teacher')
const canPublish = computed(() => isTeacher.value || creds.value?.can_publish !== false)
const liveSessionId = computed(() => creds.value?.session_id ?? props.sessionId)
const studentToken = computed(() => isTeacher.value ? ''
  : (JSON.parse(sessionStorage.getItem('lcStudent') || '{}').token || ''))
const boardPdf = computed(() => pdfState.value
  ? { ...pdfState.value, url: `/api/board/pdf/${pdfState.value.id}${isTeacher.value ? '' : `?token=${studentToken.value}`}` }
  : null)
const displayName = computed(() => isTeacher.value ? 'دبیر' : (creds.value?.student_name || 'دانش‌آموز'))
const camParticipants = computed(() => participants.value.filter(p => !p.screenTrack))
const screenParticipant = computed(() => participants.value.find(p => p.screenTrack) || null)
const hasScreen = computed(() => !!screenParticipant.value)
const classTitle = computed(() => creds.value?.class_title || '')
const teacherName = computed(() => participants.value.find(p => p.isTeacher)?.name || '')

async function loadCreds() {
  if (isTeacher.value) {
    const data = await api.get(`/api/sessions/${props.sessionId}/token`)
    return { livekit_url: data.livekit_url, token: data.token, room: data.room, session_id: data.session_id,
             class_id: data.class_id, student_name: null, can_publish: true }
  }
  const raw = sessionStorage.getItem('lcStudent')
  if (!raw) throw new Error('مشخصات ورود پیدا نشد؛ از لینک اختصاصی کلاس دوباره وارد شوید.')
  const d = JSON.parse(raw)
  return { livekit_url: d.livekit_url, token: d.livekit_token, room: d.room, session_id: d.session_id,
           student_name: d.student_name, class_title: d.class_title, can_publish: d.can_publish !== false }
}

function rebuildParticipants() {
  if (!room) return
  const list = []
  const push = (p, isLocal) => {
    const cam = p.getTrackPublication(Track.Source.Camera)
    const mic = p.getTrackPublication(Track.Source.Microphone)
    const scr = p.getTrackPublication(Track.Source.ScreenShare)
    list.push({
      identity: p.identity,
      name: p.name || p.identity,
      isLocal,
      isTeacher: p.identity.startsWith('teacher-'),
      camTrack: cam?.track ? cam.track : null,
      screenTrack: scr?.track ? scr.track : null,
      camMuted: !cam || cam.isMuted,
      micMuted: !mic || mic.isMuted,
      handRaised: !!hands[p.identity],
    })
  }
  push(room.localParticipant, true)
  room.remoteParticipants.forEach(p => push(p, false))
  list.sort((a, b) => ((b.isTeacher ? 1 : 0) - (a.isTeacher ? 1 : 0)) || a.name.localeCompare(b.name, 'fa'))
  participants.value = list
  if (!list.some(p => p.screenTrack) && stage.value === 'screen') stage.value = 'grid'
  attachScreen()
}

function attachScreen() {
  const el = screenVideo.value
  const track = screenParticipant.value?.screenTrack
  if (!el || !track) return
  el.srcObject = null
  track.attach(el)
}

function attachRemoteAudio(track, participant) {
  if (!audioSink.value) return
  detachRemoteAudio(participant?.identity)
  const audio = document.createElement('audio')
  audio.autoplay = true
  track.attach(audio)
  audioSink.value.appendChild(audio)
  audioEls.set(participant.identity, audio)
}
function detachRemoteAudio(identity) {
  const el = identity ? audioEls.get(identity) : null
  if (el) { el.remove(); audioEls.delete(identity) }
}

function pushSystemChat(text) {
  chat.value.push({ kind: 'system', text, ts: Date.now() })
}

function routeData(msg, participant) {
  if (!msg || typeof msg !== 'object') return
  const from = partisansSafeName(participant)
  if (msg.type === 'chat') {
    chat.value.push({ kind: 'msg', user: msg.user || from || 'کاربر', text: String(msg.text ?? ''), ts: msg.ts || Date.now() })
    if (sideTab.value !== 'chat') unread.value += 1
  } else if (msg.type === 'board' && msg.op) {
    boardEl.value?.applyRemote(msg.op)
  } else if (msg.type === 'board-pdf') {
    pdfState.value = msg.pdf || null
    if (msg.pdf) { stage.value = 'board' } else { pdfTotal.value = 0 }
  } else if (msg.type === 'board-state') {
    boardOpen.value = !!msg.open
    if (msg.open) { stage.value = 'board' } else if (stage.value === 'board') { stage.value = 'grid' }
  } else if (msg.type === 'hand') {
    const id = participant?.identity
    if (id) {
      hands[id] = !!msg.raised
      rebuildParticipants()
      pushSystemChat(msg.raised ? `✋ ${from} دستش را بالا برد` : `✋ ${from} دستش را پایین آورد`)
    }
  }
}
function partisansSafeName(p) { return p?.name || p?.identity || '' }

async function publishData(obj) {
  if (!room) return
  try { await room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(obj)), { reliable: true }) } catch { /* داده اختیاری است */ }
}

async function fetchBoard() {
  try {
    const body = isTeacher.value ? {} : { token: JSON.parse(sessionStorage.getItem('lcStudent') || '{}').token }
    const d = await api.post(`/api/sessions/${liveSessionId.value}/board/fetch`, body)
    boardOpen.value = !!d.open
    pendingBoardOps = d.ops || []
    pdfState.value = d.pdf || null
    if (d.open) stage.value = 'board'
  } catch { pendingBoardOps = [] }
}

async function connect() {
  try {
    creds.value = await loadCreds()
  } catch (e) {
    error.value = e.message; connecting.value = false; return
  }
  try {
    await fetchBoard()
    // ترجیحات کیفیتِ حساب (برای کاربر لاگین) را هم بخوانیم — اگر آفلاین بود همان localStorage
    try {
      const pref = await api.get('/api/me/preferences')
      videoPrefs.value = { video_preset: pref.video_preset, video_fps: pref.video_fps }
      savePrefsLocal(videoPrefs.value)
    } catch { /* دانش‌آموز بدون کوکی — localStorage ملاک است */ }
    const pr = PRESETS[videoPrefs.value.video_preset] || PRESETS.optimized

    room = new Room({
      adaptiveStream: true,
      dynacast: true,
      // حالت انتخابی کاربر (پیش‌فرض: اقتصادی ۶۴۰×۳۶۰@۱۵) → پهنای باند و CPU بسیار کمتر
      videoCaptureDefaults: { resolution: { width: pr.w, height: pr.h, frameRate: videoPrefs.value.video_fps } },
      publishDefaults: { simulcast: true },
    })
    room
      .on(RoomEvent.ParticipantConnected, rebuildParticipants)
      .on(RoomEvent.ParticipantDisconnected, (p) => { detachRemoteAudio(p?.identity); rebuildParticipants() })
      .on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
        if (track.kind === Track.Kind.Audio) attachRemoteAudio(track, participant)
        rebuildParticipants()
      })
      .on(RoomEvent.TrackUnsubscribed, (track, pub, participant) => {
        if (track.kind === Track.Kind.Audio) detachRemoteAudio(participant?.identity)
        rebuildParticipants()
      })
      .on(RoomEvent.TrackPublished, rebuildParticipants)
      .on(RoomEvent.TrackUnpublished, rebuildParticipants)
      .on(RoomEvent.TrackMuted, rebuildParticipants)
      .on(RoomEvent.TrackUnmuted, rebuildParticipants)
      .on(RoomEvent.ActiveSpeakersChanged, (speakers) => { speakingIds.value = new Set(speakers.map(s => s.identity)) })
      .on(RoomEvent.DataReceived, (payload, participant) => {
        try { routeData(JSON.parse(new TextDecoder().decode(payload)), participant) } catch { /* پیام خراب — نادیده */ }
      })
      .on(RoomEvent.Disconnected, () => {
        connected.value = false
        if (!isTeacher.value) error.value = 'ارتباط با اتاق قطع شد (ممکن است کلاس توسط دبیر بسته شده باشد).'
      })

    await room.connect(creds.value.livekit_url, creds.value.token)
    connected.value = true
    if (canPublish.value) {
      await room.localParticipant.setCameraEnabled(true).catch(() => { camOn.value = false })
      await room.localParticipant.setMicrophoneEnabled(true).catch(() => { micOn.value = false })
    } else {
      camOn.value = false; micOn.value = false
    }
    rebuildParticipants()
    await nextTick()
    if (boardEl.value && pendingBoardOps) boardEl.value.loadInitial(pendingBoardOps)

    if (isTeacher.value) fetchRecordStatus()
    if (!isTeacher.value) {
      heartbeatTimer = setInterval(async () => {
        try {
          const raw = sessionStorage.getItem('lcStudent')
          if (raw) {
            const d = JSON.parse(raw)
            await api.post('/api/student/heartbeat', { token: d.token, session_id: d.session_id })
          }
        } catch { /* اینترنت قطع‌ووصل است؛ تلاش بعدی ادامه می‌دهد */ }
      }, 15000)
    }
  } catch (e) {
    error.value = `اتصال به اتاق برقرار نشد: ${e.message || e}`
  } finally {
    connecting.value = false
  }
}

async function toggleMic() {
  if (!canPublish.value) return noticeLock()
  micOn.value = !micOn.value
  await room?.localParticipant.setMicrophoneEnabled(micOn.value).catch(() => { micOn.value = !micOn.value })
}
async function toggleCam() {
  if (!canPublish.value) return noticeLock()
  camOn.value = !camOn.value
  await room?.localParticipant.setCameraEnabled(camOn.value).catch(() => { camOn.value = !camOn.value })
}
async function toggleScreen() {
  if (!canPublish.value) return noticeLock()
  const next = !sharingScreen.value
  try {
    await room?.localParticipant.setScreenShareEnabled(next)
    sharingScreen.value = next
    if (next) stage.value = 'screen'
  } catch { sharingScreen.value = false }
}
function noticeLock() { pushSystemChat('🔒 ظرفیت ویدیو/صوت این جلسه تکمیل شده است؛ شما به‌صورت دیداری وارد شدید.') }

async function toggleHand() {
  handRaised.value = !handRaised.value
  const d = { type: 'hand', raised: handRaised.value }
  await publishData(d)
  hands[room?.localParticipant.identity] = handRaised.value
  rebuildParticipants()
}

async function openBoardTab() {
  if (isTeacher.value) {
    if (stage.value === 'board' && boardOpen.value) {
      // دبیر: بستن تخته برای همه
      boardOpen.value = false
      stage.value = 'grid'
      await api.post(`/api/sessions/${liveSessionId.value}/board/state`, {}).catch(() => {})
      await publishData({ type: 'board-state', open: false })
      return
    }
    stage.value = 'board'
    boardOpen.value = true
    await api.post(`/api/sessions/${liveSessionId.value}/board/state`, { open: true }).catch(() => {})
    await publishData({ type: 'board-state', open: true })
    return
  }
  stage.value = 'board'
}

/* ---------- کتاب PDF روی تخته (فقط دبیر بارگذاری/ورق‌زنی می‌کند) ---------- */
function applyPdfLocal(pdf) {
  pdfState.value = pdf ? { ...pdf } : null
  if (!pdf) pdfTotal.value = 0
}

async function uploadPdf(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > 25 * 1024 * 1024) { pushSystemChat('⚠️ حجم PDF بیش از ۲۵ مگابایت است.'); return }
  const fd = new FormData()
  fd.append('file', file)
  try {
    const d = await api.postForm(`/api/sessions/${liveSessionId.value}/board/pdf`, fd)
    applyPdfLocal(d.pdf)
    // تخته را برای همه باز کن تا کتاب را ببینند
    if (!boardOpen.value) await openBoardTab()
    await publishData({ type: 'board-pdf', pdf: pdfState.value })
    pushSystemChat(`📄 «${d.pdf.name}» روی تخته بارگذاری شد.`)
  } catch (e) {
    pushSystemChat(`⚠️ بارگذاری PDF ناموفق بود: ${e.message}`)
  }
}

async function togglePage(delta) {
  if (!isTeacher.value || !pdfState.value) return
  const total = pdfTotal.value || 10000
  const page = Math.min(Math.max(1, pdfState.value.page + delta), total)
  if (page === pdfState.value.page) return
  try {
    await api.post(`/api/sessions/${liveSessionId.value}/board/pdf/page`, { page })
    applyPdfLocal({ ...pdfState.value, page })
    await publishData({ type: 'board-pdf', pdf: pdfState.value })
  } catch (e) { pushSystemChat(`⚠️ تغییر صفحه ناموفق بود: ${e.message}`) }
}

async function closePdf() {
  if (!isTeacher.value) return
  try {
    await api.post(`/api/sessions/${liveSessionId.value}/board/pdf/close`, {})
    applyPdfLocal(null)
    await publishData({ type: 'board-pdf', pdf: null })
    pushSystemChat('📄 کتاب از تخته برداشته شد.')
  } catch (e) { pushSystemChat(`⚠️ ${e.message}`) }
}

function onPdfLoaded({ total }) { pdfTotal.value = total }

/* ---------- ضبط جلسه ---------- */
async function fetchRecordStatus() {
  try {
    const d = await api.get(`/api/sessions/${liveSessionId.value}/record/status`)
    recordStatus.value = d.record_status || 'idle'
  } catch { /* ضبط در دسترس نیست */ }
}

async function toggleRecord() {
  if (!isTeacher.value) return
  try {
    if (recordStatus.value === 'recording') {
      await api.post(`/api/sessions/${liveSessionId.value}/record/stop`, {})
      recordStatus.value = 'stopped'
      pushSystemChat('⏹ ضبط متوقف شد؛ پس از آماده‌شدن فایل، در صفحه‌ی کلاس دانلود می‌شود.')
    } else {
      const d = await api.post(`/api/sessions/${liveSessionId.value}/record/start`, {})
      recordStatus.value = 'recording'
      pushSystemChat(d.already ? '⏺ ضبط از قبل در جریان است.' : '⏺ ضبط جلسه شروع شد.')
    }
  } catch (e) {
    pushSystemChat(e.status === 503
      ? '⚠️ ضبط روی این سرور فعال نیست (LIVECLASS_EGRESS_ENABLE=1 لازم است).'
      : `⚠️ ضبط انجام نشد: ${e.message}`)
  }
}

function onBoardOp(op) {
  publishData({ type: 'board', op })
  const body = isTeacher.value
    ? { op }
    : { op, token: JSON.parse(sessionStorage.getItem('lcStudent') || '{}').token }
  const path = op.t === 'clear' ? 'board/clear' : 'board/op'
  const payload = op.t === 'clear' ? { ...body, op_id: op.id } : body
  api.post(`/api/sessions/${liveSessionId.value}/${path}`, payload)
    .catch(e => { if (e.status === 409) pushSystemChat('⚠️ جلسه بسته شد؛ تغییرات تخته ذخیره نمی‌شود.') })
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text) return
  const msg = { type: 'chat', user: displayName.value, text, ts: Date.now() }
  await publishData(msg)
  chat.value.push({ kind: 'msg', user: 'شما', text, ts: msg.ts, mine: true })
  chatInput.value = ''
}

async function kick(p) {
  if (!confirm(`${p.name} از جلسه اخراج شود؟`)) return
  try { await api.post(`/api/sessions/${liveSessionId.value}/kick`, { identity: p.identity }) } catch (e) { alert(e.message) }
}

function isSpeaking(identity) { return speakingIds.value.has(identity) }

function leave() {
  const clsId = creds.value?.class_id
  room?.disconnect()
  if (isTeacher.value && clsId) location.hash = `#/class/${clsId}`
  else { sessionStorage.removeItem('lcStudent'); location.hash = '#/' }
}

onMounted(connect)
onUnmounted(() => { clearInterval(heartbeatTimer); room?.disconnect() })
</script>

<template>
  <div class="room2">
    <div class="room2-main">
      <div class="stage-tabs">
        <button class="stage-tab" :class="{ active: stage === 'grid' }" @click="stage = 'grid'">🎥 دوربین‌ها</button>
        <button class="stage-tab" :class="{ active: stage === 'board' }" :title="isTeacher ? (boardOpen ? 'کلیک: بستن تخته برای همه' : 'کلیک: نمایش تخته برای همه') : 'تخته کلاس'" @click="openBoardTab">📋 تخته<span v-if="isTeacher && boardOpen"> (برای همه)</span></button>
        <button v-if="hasScreen" class="stage-tab" :class="{ active: stage === 'screen' }" @click="stage = 'screen'">🖥️ اشتراک صفحه</button>
        <label v-if="isTeacher" class="stage-tab upload-tab" title="بارگذاری کتاب PDF روی تخته">📄 کتاب PDF<input type="file" accept="application/pdf,.pdf" hidden @change="uploadPdf" /></label>
        <span v-if="recordStatus === 'recording'" class="record-pill"><span class="live-dot"></span>در حال ضبط</span>
        <span v-if="connected" class="live-pill"><span class="live-dot"></span>پخش زنده · {{ participants.length }} نفر</span>
      </div>

      <div class="stage-area">
        <div v-if="connecting" class="center-fill muted">در حال اتصال به اتاق…</div>
        <div v-else-if="error" class="center-fill">
          <div class="card" style="max-width:440px;text-align:center">
            <h2>⚠️</h2>
            <p class="muted">{{ error }}</p>
            <a v-if="!isTeacher" class="btn primary" href="#/" style="margin-top:8px">بازگشت</a>
          </div>
        </div>
        <template v-else>
          <Board v-show="stage === 'board'" ref="boardEl" :can-clear="isTeacher" :can-draw="true" :pdf="boardPdf" @op="onBoardOp" @pdf-loaded="onPdfLoaded" />
          <div v-if="pdfState && stage === 'board'" class="pdf-nav">
            <button v-if="isTeacher" class="pdf-btn" :disabled="pdfState.page <= 1" @click="togglePage(-1)">‹ قبلی</button>
            <div class="pdf-meta">
              <b :title="pdfState.name">{{ pdfState.name }}</b>
              <span>صفحه {{ pdfState.page }} از {{ pdfTotal || '…' }}</span>
            </div>
            <button v-if="isTeacher" class="pdf-btn" :disabled="!!pdfTotal && pdfState.page >= pdfTotal" @click="togglePage(1)">بعدی ›</button>
            <button v-if="isTeacher" class="pdf-btn close" title="برداشتن کتاب از تخته" @click="closePdf">✕</button>
          </div>

          <div v-show="stage === 'grid'" class="video-grid">
            <VideoTile v-for="p in camParticipants" :key="p.identity"
                       :track="p.camTrack" :name="p.name" :is-local="p.isLocal"
                       :speaking="isSpeaking(p.identity)" :cam-muted="p.camMuted" :mic-muted="p.micMuted" />
            <div v-if="!camParticipants.length" class="center-fill muted">هنوز کسی تصویرش را روشن نکرده است.</div>
          </div>

          <div v-show="stage === 'screen'" class="screen-stage">
            <video ref="screenVideo" autoplay playsinline></video>
            <div class="screen-strip">
              <VideoTile v-for="p in camParticipants" :key="p.identity"
                         :track="p.camTrack" :name="p.name" :is-local="p.isLocal"
                         :speaking="isSpeaking(p.identity)" :cam-muted="p.camMuted" :mic-muted="p.micMuted" />
            </div>
          </div>
        </template>
      </div>

      <div class="callbar" v-if="!connecting && !error">
        <button class="call-btn" :class="[micOn ? 'on' : 'off', { locked: !canPublish }]" :title="canPublish ? (micOn ? 'بستن میکروفون' : 'بازکردن میکروفون') : 'در این جلسه فقط بیننده هستید'" @click="toggleMic">{{ micOn ? '🎤' : '🔇' }}</button>
        <button class="call-btn" :class="[camOn ? 'on' : 'off', { locked: !canPublish }]" :title="canPublish ? (camOn ? 'بستن دوربین' : 'بازکردن دوربین') : 'در این جلسه فقط بیننده هستید'" @click="toggleCam">{{ camOn ? '🎥' : '📵' }}</button>
        <button class="call-btn" :class="{ on: sharingScreen, locked: !canPublish }" title="اشتراک صفحه" @click="toggleScreen">🖥️</button>
        <button class="call-btn" :class="{ 'hand-on': handRaised }" :title="handRaised ? 'پایین آوردن دست' : 'بالا بردن دست'" @click="toggleHand">✋</button>
        <button v-if="isTeacher" class="call-btn" :class="{ recording: recordStatus === 'recording' }" :title="recordStatus === 'recording' ? 'توقف ضبط جلسه' : 'شروع ضبط جلسه'" @click="toggleRecord">{{ recordStatus === 'recording' ? '⏹' : '⏺' }}</button>
        <button class="call-btn danger" title="خروج از کلاس" @click="leave">خروج 🚪</button>
      </div>
    </div>

    <aside class="room2-side">
      <div class="side-tabs">
        <button class="side-tab" :class="{ active: sideTab === 'participants' }" @click="sideTab = 'participants'">👥 حضار <b>({{ participants.length }})</b></button>
        <button class="side-tab" :class="{ active: sideTab === 'chat' }" @click="sideTab = 'chat'; unread = 0">💬 گفت‌وگو<span v-if="unread" class="side-badge">{{ unread }}</span></button>
      </div>

      <div class="side-body" v-show="sideTab === 'participants'">
        <div class="participants">
          <div v-for="p in participants" :key="p.identity" class="participant-row" :class="{ 'is-teacher': p.isTeacher }">
            <div class="p-ava" :class="{ teacher: p.isTeacher }">{{ (p.name || '؟').trim().charAt(0) }}</div>
            <div class="p-name">{{ p.name }}{{ p.isLocal ? ' (شما)' : '' }}<div v-if="p.isTeacher" class="p-role">دبیر</div></div>
            <span v-if="p.handRaised" class="p-icons p-hand" title="دست بالا">✋</span>
            <span class="p-icons">{{ p.micMuted ? '🔇' : '🎙️' }}</span>
            <span class="p-icons">{{ p.camMuted ? '📷' : '🎥' }}</span>
            <button v-if="isTeacher && !p.isLocal && !p.isTeacher" class="btn sm danger" @click="kick(p)">اخراج</button>
          </div>
          <div v-if="!participants.length" class="muted small">هنوز کسی وارد نشده است.</div>
        </div>
        <div v-if="teacherName" class="muted small" style="padding:8px 4px 0">کلاس با حضور دبیر اداره می‌شود؛ در صورت خروج دبیر، جلسه بسته می‌شود.</div>
      </div>

      <div class="side-body" v-show="sideTab === 'chat'">
        <div class="chatbox">
          <div v-for="(m, i) in chat" :key="i" class="chatline" :class="{ mine: m.mine, system: m.kind === 'system' }">
            <template v-if="m.kind === 'system'"><span class="system-text">{{ m.text }}</span></template>
            <template v-else>
              <div class="meta"><b>{{ m.user }}</b><time>{{ new Date(m.ts).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' }) }}</time></div>
              <div>{{ m.text }}</div>
            </template>
          </div>
          <div v-if="!chat.length" class="muted small">هنوز پیامی نیست؛ اولین پیام را بفرستید.</div>
        </div>
        <form class="chatform" @submit.prevent="sendChat">
          <input class="input" v-model="chatInput" placeholder="پیام به کلاس…" maxlength="500" />
          <button class="btn primary" type="submit">ارسال</button>
        </form>
      </div>
    </aside>

    <div ref="audioSink" style="display:none"></div>
  </div>
</template>
