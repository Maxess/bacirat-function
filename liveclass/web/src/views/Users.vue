<script setup>
/* مدیریت کاربران (فقط ادمین): ساخت دبیر/مهمان، ریست رمز، حذف */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ user: Object })
const users = ref([])
const error = ref('')
const notice = ref('')
const newUser = ref({ username: '', full_name: '', password: '', role: 'teacher' })
const newPassword = ref({})   // user_id -> رمز جدید تایپ‌شده

async function load() {
  try { users.value = (await api.get('/api/users')).users } catch (e) { error.value = e.message }
}

async function createUser() {
  error.value = ''; notice.value = ''
  try {
    await api.post('/api/users', { ...newUser.value })
    notice.value = `کاربر ${newUser.value.username} ساخته شد ✅`
    newUser.value = { username: '', full_name: '', password: '', role: 'teacher' }
    await load()
  } catch (e) { error.value = e.message }
}

async function resetPassword(u) {
  const pw = (newPassword.value[u.id] || '').trim()
  if (pw.length < 6) { error.value = 'رمز حداقل ۶ کاراکتر باشد.'; return }
  try {
    await api.put(`/api/users/${u.id}/password`, { password: pw })
    newPassword.value[u.id] = ''
    notice.value = `رمز ${u.username} تغییر کرد ✅`
  } catch (e) { error.value = e.message }
}

async function removeUser(u) {
  if (!confirm(`کاربر «${u.full_name}» (${u.username}) حذف شود؟`)) return
  error.value = ''
  try { await api.del(`/api/users/${u.id}`); await load() } catch (e) { error.value = e.message }
}

const roleLabel = (r) => ({ admin: 'ادمین', teacher: 'دبیر', guest: 'مهمان' }[r] || r)
onMounted(load)
</script>

<template>
  <h1>👥 مدیریت کاربران</h1>
  <div v-if="error" class="error">{{ error }}</div>
  <div v-if="notice" class="notice">{{ notice }}</div>

  <form class="card row" @submit.prevent="createUser" style="margin:12px 0">
    <input class="input" style="flex:1.2; min-width:130px" v-model="newUser.username" placeholder="نام کاربری" required minlength="3" />
    <input class="input" style="flex:1.5; min-width:150px" v-model="newUser.full_name" placeholder="نام و نام خانوادگی" />
    <input class="input" style="flex:1.2; min-width:130px" v-model="newUser.password" type="password" placeholder="رمز (حداقل ۶)" required minlength="6" />
    <select class="input" style="flex:0.8; min-width:110px" v-model="newUser.role">
      <option value="teacher">دبیر</option>
      <option value="guest">مهمان (فقط کلاس‌های مجاز)</option>
    </select>
    <button class="btn primary" type="submit">+ ساخت کاربر</button>
  </form>

  <div class="card">
    <table class="table">
      <thead><tr><th>نام کاربری</th><th>نام</th><th>نقش</th><th>ساخته شده</th><th>ریست رمز</th><th></th></tr></thead>
      <tbody>
        <tr v-for="u in users" :key="u.id">
          <td><code>{{ u.username }}</code></td>
          <td>{{ u.full_name }}</td>
          <td><span class="pill" :class="u.role === 'admin' ? 'ok' : ''">{{ roleLabel(u.role) }}</span></td>
          <td class="muted small">{{ u.created_at }}</td>
          <td>
            <div class="row" style="gap:6px; flex-wrap:nowrap">
              <input class="input" style="width:130px; padding:7px 10px" type="text" v-model="newPassword[u.id]" placeholder="رمز جدید" />
              <button class="btn sm" @click="resetPassword(u)">ذخیره</button>
            </div>
          </td>
          <td><button v-if="u.role !== 'admin'" class="btn sm danger" @click="removeUser(u)">حذف</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
