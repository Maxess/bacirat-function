<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ user: Object })
const classes = ref([])
const teachers = ref([])
const newTitle = ref('')
const newTeacher = ref('')
const error = ref('')
const loading = ref(true)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/api/my/classes')
    classes.value = data.classes
    if (props.user?.role === 'admin') {
      const t = await api.get('/api/teachers')
      teachers.value = t.teachers
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function createClass() {
  const title = newTitle.value.trim()
  if (!title) return
  error.value = ''
  try {
    await api.post('/api/classes', { title, teacher_id: newTeacher.value || undefined })
    newTitle.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <h1>کلاس‌های من</h1>

  <form v-if="user?.role !== 'guest'" class="card row" @submit.prevent="createClass" style="margin-bottom:16px">
    <input class="input" style="flex:2; min-width:200px" v-model="newTitle" placeholder="عنوان کلاس جدید (مثلاً ریاضی هفتم)" required />
    <select v-if="user?.role === 'admin'" class="input" style="flex:1; min-width:160px" v-model="newTeacher">
      <option value="">دبیر… (خودم)</option>
      <option v-for="t in teachers" :key="t.id" :value="t.id">{{ t.full_name }} ({{ t.username }})</option>
    </select>
    <button class="btn primary" type="submit">+ ساخت کلاس</button>
  </form>
  <div v-else class="card muted" style="margin-bottom:16px">کاربر مهمان هستید — فقط کلاس‌هایی که به شما دسترسی داده شده را می‌بینید.</div>
  <div v-if="error" class="error">{{ error }}</div>

  <div v-if="loading" class="card muted">در حال بارگذاری…</div>
  <div v-else-if="!classes.length" class="card muted">هنوز کلاسی ندارید. از بالا یکی بسازید.</div>
  <div v-else class="grid cards">
    <a v-for="c in classes" :key="c.id" class="card" :href="`#/class/${c.id}`" style="display:block">
      <h3>📚 {{ c.title }} <span v-if="c.guest_access" class="pill warn" style="font-size:11px">دسترسی مهمان</span></h3>
      <p class="muted small">دبیر: {{ c.teacher_name }}</p>
      <p class="muted small">ساخته شده: {{ c.created_at }}</p>
      <span class="btn sm primary" style="margin-top:10px">ورود به کلاس ←</span>
    </a>
  </div>
</template>
