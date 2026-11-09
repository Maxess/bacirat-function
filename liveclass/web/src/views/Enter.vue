<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ query: Object, brand: Object })
const status = ref('connecting')   // connecting | ready | error
const message = ref('')

onMounted(async () => {
  const token = props.query?.token || ''
  if (!token) { status.value = 'error'; message.value = 'لینک نامعتبر است (توکن ندارد).'; return }
  try {
    const data = await api.post('/api/student/enter', { token })
    sessionStorage.setItem('lcStudent', JSON.stringify({
      token, session_id: data.session_id, room: data.room,
      livekit_url: data.livekit_url, livekit_token: data.token,
      student_name: data.student_name, class_title: data.class_title,
      can_publish: data.can_publish !== false,
    }))
    location.hash = '#/room/student'
  } catch (e) {
    status.value = 'error'
    message.value = e.status === 409 ? 'کلاس هنوز شروع نشده؛ منتظر بمانید تا دبیر آن را شروع کند.' :
                    e.status === 404 ? 'این لینک معتبر نیست.' : `خطا: ${e.message}`
  }
})
</script>

<template>
  <div class="login-wrap">
    <div class="card login-card">
      <div v-if="status === 'connecting'">
        <div class="brand-mark">🎓</div>
        <h2>در حال ورود به کلاس…</h2>
        <p class="muted">کمی صبر کنید</p>
      </div>
      <div v-else>
        <div class="brand-mark" style="background:#3a1518">⚠️</div>
        <h2>ورود ممکن نشد</h2>
        <p class="muted">{{ message }}</p>
        <button class="btn" style="margin-top:12px" @click="location.reload()">تلاش دوباره</button>
      </div>
    </div>
  </div>
</template>
