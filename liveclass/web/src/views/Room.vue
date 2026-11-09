<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { Room, RoomEvent, Track } from 'livekit-client'

const props = defineProps({ mode: { type: String, required: true }, sessionId: [Number, String] , brand: Object})

const creds = ref(null)       // {livekit_url, token, room, session_id, name, class_title}
const error = ref('')
const connecting = ref(true)
const micOn = ref(true)
const camOn = ref(true)
const sharingScreen = ref(false)
const chat = ref([])
const chatInput = ref('')
const participants = ref([])  // [{identity, name, isLocal}]
let room = null
let heartbeatTimer = null

const isTeacher = computed(() => props.mode === 'teacher')
const displayName = computed(() => isTeacher.value ? 'دبیر' : (creds.value?.student_name || 'دانش‌آموز'))

async function loadCreds() {
  if (isTeacher.value) {
    const data = await api.get(`/api/sessions/${props.sessionId}/token`)
    return { livekit_url: data.livekit_url, token: data.token, room: data.room, session_id: data.session_id, class_id: data.class_id, student_name: null }
  }
  const raw = sessionStorage.getItem('lcStudent')
  if (!raw) throw new Error('مشخصات ورود پیدا نشد؛ از لینک کلاس دوباره وارد شوید.')
  const d = JSON.parse(raw)
  return { livekit_url: d.livekit_url, token: d.livekit_token, room: d.room, session_id: d.session_id, student_name: d.student_name, class_title: d.class_title }
}

function rebuildParticipants() {
  if (!room) return
  const list = []
  list.push({ identity: room.localParticipant.identity, name: (room.localParticipant.name || displayName.value) + ' (من)', isLocal: true })
  room.remoteParticipants.forEach(p => list.push({ identity: p.identity, name: p.name || p.identity, isLocal: false }))
  participants.value = list
}

function tileId(identity) { return `tile-${CSS.escape(identity)}` }

function attachTrack(identity, track) {
  const el = document.getElementById(tileId(identity))
  if (!el) return
  if (track.kind === Track.Kind.Video) {
    const video = document.createElement('video')
    video.autoplay = true; video.playsInline = true; video.muted = true
    track.attach(video)
    el.querySelector('video')?.remove()
    el.prepend(video)
  } else if (track.kind === Track.Kind.Audio) {
    const audio = document.createElement('audio')
    audio.autoplay = true
    track.attach(audio)
    el.appendChild(audio)
  }
}

async function connect() {
  try {
    creds.value = await loadCreds()
  } catch (e) {
    error.value = e.message
    connecting.value = false
    return
  }
  try {
    room = new Room({ adaptiveStream: true, dynacast: true })
    room
      .on(RoomEvent.TrackSubscribed, (track, pub, participant) => attachTrack(participant.identity, track))
      .on(RoomEvent.ParticipantConnected, rebuildParticipants)
      .on(RoomEvent.ParticipantDisconnected, rebuildParticipants)
      .on(RoomEvent.LocalTrackPublished, (pub) => {
        if (pub.track) attachTrack(room.localParticipant.identity, pub.track)
      })
      .on(RoomEvent.DataReceived, (payload, participant) => {
        try {
          const msg = JSON.parse(new TextDecoder().decode(payload))
          if (msg.type === 'chat') {
            chat.value.push({ user: msg.user || participant?.name || 'کاربر', text: msg.text, ts: Date.now() })
          }
        } catch { /* ignore */ }
      })
      .on(RoomEvent.Disconnected, () => {
        if (!isTeacher.value) error.value = 'ارتباط با اتاق قطع شد (ممکن است کلاس بسته شده باشد).'
      })

    await room.connect(creds.value.livekit_url, creds.value.token)
    await room.localParticipant.setCameraEnabled(true)
    await room.localParticipant.setMicrophoneEnabled(true)
    rebuildParticipants()

    // رندر ویدیوهای منتشرشده‌ی قبلی + خودمان
    room.remoteParticipants.forEach(p => {
      p.trackPublications.forEach(pub => { if (pub.track) attachTrack(p.identity, pub.track) })
    })
    room.localParticipant.trackPublications.forEach(pub => { if (pub.track) attachTrack(room.localParticipant.identity, pub.track) })

    if (!isTeacher.value) {
      heartbeatTimer = setInterval(async () => {
        try {
          const raw = sessionStorage.getItem('lcStudent')
          if (raw) {
            const d = JSON.parse(raw)
            await api.post('/api/student/heartbeat', { token: d.token, session_id: d.session_id })
          }
        } catch { /* ignore */ }
      }, 15000)
    }
  } catch (e) {
    error.value = `اتصال به اتاق برقرار نشد: ${e.message}`
  } finally {
    connecting.value = false
  }
}

async function toggleMic() { micOn.value = !micOn.value; await room?.localParticipant.setMicrophoneEnabled(micOn.value) }
async function toggleCam() { camOn.value = !camOn.value; await room?.localParticipant.setCameraEnabled(camOn.value) }
async function toggleScreen() {
  sharingScreen.value = !sharingScreen.value
  try { await room?.localParticipant.setScreenShareEnabled(sharingScreen.value) }
  catch { sharingScreen.value = false }
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || !room) return
  const payload = new TextEncoder().encode(JSON.stringify({ type: 'chat', user: displayName.value, text }))
  await room.localParticipant.publishData(payload, { reliable: true })
  chat.value.push({ user: 'من', text, ts: Date.now() })
  chatInput.value = ''
}

async function kick(p) {
  if (!confirm(`${p.name} اخراج شود؟`)) return
  try { await api.post(`/api/sessions/${props.sessionId}/kick`, { identity: p.identity }) } catch (e) { alert(e.message) }
}

function leave() {
  const clsId = creds.value?.class_id
  room?.disconnect()
  location.hash = isTeacher.value && clsId ? `#/class/${clsId}` : '#/'
}

onMounted(connect)
onUnmounted(() => { clearInterval(heartbeatTimer); room?.disconnect() })
</script>

<template>
  <div class="room-layout">
    <div class="stage">
      <div v-if="connecting" class="muted">در حال اتصال به اتاق…</div>
      <div v-else-if="error" class="card" style="max-width:420px">
        <h2>⚠️ مشکل</h2>
        <p class="muted">{{ error }}</p>
        <a v-if="!isTeacher" class="btn" href="#/">بازگشت</a>
      </div>
      <template v-else>
        <div class="tiles">
          <div v-for="p in participants" :key="p.identity" class="tile" :id="tileId(p.identity)">
            <span class="tag">{{ p.name }}</span>
          </div>
        </div>
        <div class="controls">
          <button class="btn icon" :class="{ primary: micOn }" @click="toggleMic" :title="micOn ? 'بستن میکروفون' : 'بازکردن میکروفون'">{{ micOn ? '🎤' : '🔇' }}</button>
          <button class="btn icon" :class="{ primary: camOn }" @click="toggleCam" :title="camOn ? 'بستن دوربین' : 'بازکردن دوربین'">{{ camOn ? '🎥' : '📷' }}</button>
          <button class="btn icon" :class="{ primary: sharingScreen }" @click="toggleScreen" title="اشتراک صفحه">🖥️</button>
          <button class="btn icon danger" @click="leave" title="خروج از اتاق">🚪</button>
        </div>
      </template>
    </div>

    <aside class="side">
      <h3>👥 حضار ({{ participants.length }})</h3>
      <div class="participants">
        <div v-for="p in participants" :key="p.identity" class="participant-row">
          <span>{{ p.isLocal ? '⭐' : '🙋' }}</span>
          <span class="spacer">{{ p.name }}</span>
          <button v-if="isTeacher && !p.isLocal" class="btn sm danger" @click="kick(p)">اخراج</button>
        </div>
      </div>
      <h3>💬 گفت‌وگو</h3>
      <div class="chatbox">
        <div v-for="(m, i) in chat" :key="i" class="chatline"><b>{{ m.user }}:</b> {{ m.text }}</div>
        <div v-if="!chat.length" class="muted small">هنوز پیامی نیست.</div>
      </div>
      <form class="chatform" @submit.prevent="sendChat">
        <input class="input" v-model="chatInput" placeholder="پیام…" />
        <button class="btn primary sm" type="submit">ارسال</button>
      </form>
    </aside>
  </div>
</template>
