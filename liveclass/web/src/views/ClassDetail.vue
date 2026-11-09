<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ classId: { type: Number, required: true }, user: Object })
const cls = ref(null)
const students = ref([])
const sessions = ref([])
const error = ref('')
const notice = ref('')
const newCode = ref('')
const newName = ref('')
const openSession = ref(null)
const attendance = ref([])
const copied = ref('')
const grants = ref([])
const newAccessUser = ref('')
let timer = null

const canManageAccess = computed(() =>
  !!props.user && !!cls.value && (props.user.role === 'admin' || cls.value.teacher_id === props.user.id))

const baseUrl = window.location.origin
function studentLink(token) { return `${baseUrl}/#/enter?token=${token}` }

async function load() {
  error.value = ''
  try {
    const data = await api.get(`/api/classes/${props.classId}`)
    cls.value = data.class
    students.value = data.students
    sessions.value = data.sessions
    openSession.value = data.sessions.find(s => s.status === 'open') || null
    if (canManageAccess.value) await loadAccess()
    if (openSession.value) await loadAttendance()
  } catch (e) {
    error.value = e.message
  }
}

async function loadAccess() {
  try {
    const data = await api.get(`/api/classes/${props.classId}/access`)
    grants.value = data.grants
  } catch { grants.value = [] }
}

async function addAccess() {
  const username = newAccessUser.value.trim()
  if (!username) return
  error.value = ''
  try {
    await api.post(`/api/classes/${props.classId}/access`, { username })
    newAccessUser.value = ''
    notice.value = `دسترسی «${username}» اضافه شد ✅`
    await loadAccess()
  } catch (e) { error.value = e.message }
}

async function revokeAccess(u) {
  if (!confirm(`دسترسی ${u.full_name} (${u.username}) از این کلاس گرفته شود؟`)) return
  try { await api.del(`/api/classes/${props.classId}/access/${u.id}`); await loadAccess() } catch (e) { error.value = e.message }
}

async function loadAttendance() {
  if (!openSession.value) return
  try {
    const data = await api.get(`/api/sessions/${openSession.value.id}/attendance`)
    attendance.value = data.attendance
  } catch { /* ignore */ }
}

async function startSession() {
  error.value = ''
  try {
    const data = await api.post(`/api/classes/${props.classId}/start`, {})
    notice.value = data.already_open ? 'جلسه از قبل باز است.' : 'کلاس شروع شد ✅'
    await load()
  } catch (e) { error.value = e.message }
}

async function endSession() {
  if (!openSession.value) return
  if (!confirm('کلاس بسته شود؟ همه از اتاق خارج می‌شوند.')) return
  try {
    await api.post(`/api/sessions/${openSession.value.id}/end`, {})
    notice.value = 'کلاس بسته شد و حضور ثبت شد.'
    await load()
  } catch (e) { error.value = e.message }
}

async function addStudent() {
  const code = newCode.value.trim(), name = newName.value.trim()
  if (!code || !name) return
  try {
    await api.post(`/api/classes/${props.classId}/students`, { student_code: code, full_name: name })
    newCode.value = ''; newName.value = ''
    await load()
  } catch (e) { error.value = e.message }
}

async function removeStudent(s) {
  if (!confirm(`${s.full_name} از این کلاس حذف شود؟`)) return
  try { await api.del(`/api/classes/${props.classId}/students/${s.id}`); await load() } catch (e) { error.value = e.message }
}

async function copyLink(token) {
  try { await navigator.clipboard.writeText(studentLink(token)); copied.value = token; setTimeout(() => (copied.value = ''), 1500) }
  catch { window.prompt('لینک را کپی کنید:', studentLink(token)) }
}

const present = computed(() => attendance.value.filter(a => a.online).length)

onMounted(async () => {
  await load()
  timer = setInterval(async () => {
    if (openSession.value) await loadAttendance()
  }, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <a class="btn sm ghost" href="#/">→ بازگشت به کلاس‌ها</a>
  <h1 style="margin-top:10px">📚 {{ cls?.title || '…' }}</h1>
  <div v-if="error" class="error">{{ error }}</div>
  <div v-if="notice" class="notice">{{ notice }}</div>

  <!-- شروع/پایان جلسه -->
  <div class="card" style="margin:12px 0">
    <div class="row">
      <div>
        <h3 style="margin-bottom:2px">جلسه‌ی کلاس</h3>
        <p v-if="openSession" class="muted small">شروع: {{ openSession.started_at }} — وضعیت: <span class="pill ok">🟢 باز</span></p>
        <p v-else class="muted small">کلاس هنوز باز نشده است.</p>
      </div>
      <div class="spacer"></div>
      <button v-if="!openSession" class="btn primary" @click="startSession">▶️ شروع کلاس</button>
      <template v-else>
        <a class="btn primary" :href="`#/room/${openSession.id}`">🎥 ورود به اتاق</a>
        <button class="btn danger" @click="endSession">⏹ بستن کلاس (خروج همه)</button>
      </template>
    </div>
  </div>

  <!-- حضور زنده -->
  <div v-if="openSession" class="card" style="margin-bottom:12px">
    <div class="row">
      <h3 style="margin:0">حضور زنده — {{ present }} نفر حاضر</h3>
      <div class="spacer"></div>
      <span class="muted small">به‌روزرسانی خودکار هر ۱۰ ثانیه</span>
    </div>
    <table class="table" style="margin-top:10px">
      <thead><tr><th>کد</th><th>نام</th><th>ورود</th><th>آخرین حضور</th><th>مدت (ثانیه)</th><th>وضعیت</th></tr></thead>
      <tbody>
        <tr v-for="a in attendance" :key="a.student_id">
          <td>{{ a.student_code }}</td><td>{{ a.full_name }}</td>
          <td>{{ a.joined_at }}</td><td>{{ a.left_at || a.last_heartbeat || '—' }}</td><td>{{ a.seconds }}</td>
          <td><span class="pill" :class="a.online ? 'ok' : 'warn'">{{ a.online ? '🟢 آنلاین' : '⚪ خارج شده' }}</span></td>
        </tr>
        <tr v-if="!attendance.length"><td colspan="6" class="muted">هنوز کسی وارد نشده است.</td></tr>
      </tbody>
    </table>
    <a class="btn sm" style="margin-top:10px" :href="`/api/sessions/${openSession.id}/attendance.csv`">⬇️ خروجی CSV حضور</a>
  </div>

  <!-- دانش‌آموزان -->
  <div class="card">
    <div class="row">
      <h3 style="margin:0">دانش‌آموزان ({{ students.length }})</h3>
      <div class="spacer"></div>
      <form class="row" @submit.prevent="addStudent">
        <input class="input" style="width:110px" v-model="newCode" placeholder="کد دانش‌آموز" required />
        <input class="input" style="width:150px" v-model="newName" placeholder="نام و نام خانوادگی" required />
        <button class="btn sm primary" type="submit">+ افزودن</button>
      </form>
    </div>
    <table class="table" style="margin-top:10px">
      <thead><tr><th>کد</th><th>نام</th><th>لینک اختصاصی ورود</th><th></th></tr></thead>
      <tbody>
        <tr v-for="s in students" :key="s.id">
          <td>{{ s.student_code }}</td>
          <td>{{ s.full_name }}</td>
          <td><div class="row" style="gap:6px; flex-wrap:nowrap"><code>{{ s.token ? studentLink(s.token).slice(0, 56) + '…' : '—' }}</code>
            <button v-if="s.token" class="btn sm ghost" @click="copyLink(s.token)">{{ copied === s.token ? '✓ کپی شد' : '📋' }}</button></div></td>
          <td><button class="btn sm danger" @click="removeStudent(s)">حذف</button></td>
        </tr>
        <tr v-if="!students.length"><td colspan="4" class="muted">هنوز دانش‌آموزی ثبت نشده است.</td></tr>
      </tbody>
    </table>
  </div>

  <!-- دسترسی‌های کلاس (کاربر مهمان با دسترسی دبیر) -->
  <div v-if="canManageAccess" class="card" style="margin-top:12px">
    <div class="row">
      <h3 style="margin:0">🔐 دسترسی‌های کلاس</h3>
      <div class="spacer"></div>
      <form class="row" @submit.prevent="addAccess" style="flex-wrap:nowrap">
        <input class="input" style="width:150px" v-model="newAccessUser" placeholder="نام کاربری (مثل teacher2)" required />
        <button class="btn sm primary" type="submit">+ افزودن دسترسی</button>
      </form>
    </div>
    <p class="muted small" style="margin-top:6px">این کاربران دقیقاً مثل دبیر به این کلاس دسترسی دارند: شروع/بستن کلاس، حضور، اخراج دانش‌آموز، تخته و PDF و ضبط. برای «کاربر مهمان» یک حساب با نقش مهمان از «👥 کاربران» بسازید.</p>
    <table class="table" style="margin-top:8px">
      <thead><tr><th>نام کاربری</th><th>نام</th><th>نقش</th><th></th></tr></thead>
      <tbody>
        <tr v-for="u in grants" :key="u.id">
          <td><code>{{ u.username }}</code></td>
          <td>{{ u.full_name }}</td>
          <td>{{ u.role === 'guest' ? 'مهمان' : 'دبیر' }}</td>
          <td><button class="btn sm danger" @click="revokeAccess(u)">حذف دسترسی</button></td>
        </tr>
        <tr v-if="!grants.length"><td colspan="4" class="muted">دسترسی اضافی ثبت نشده است.</td></tr>
      </tbody>
    </table>
  </div>

  <!-- تاریخچه جلسات -->
  <div v-if="sessions.length > 1 || (sessions[0] && sessions[0].status === 'closed')" class="card" style="margin-top:12px">
    <h3>جلسات گذشته</h3>
    <table class="table"><thead><tr><th>#</th><th>شروع</th><th>پایان</th><th>وضعیت</th><th>خروجی</th></tr></thead>
      <tbody>
        <tr v-for="s in sessions" :key="s.id">
          <td>{{ s.id }}</td><td>{{ s.started_at }}</td><td>{{ s.closed_at || '—' }}</td>
          <td>{{ s.status === 'open' ? 'باز' : 'بسته' }}</td>
          <td><a class="btn sm ghost" :href="`/api/sessions/${s.id}/attendance.csv`">CSV</a>
            <a v-if="s.record_status" class="btn sm ghost" :href="`/api/sessions/${s.id}/record/download`" title="دانلود ضبط جلسه">⬇️ ضبط</a></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
