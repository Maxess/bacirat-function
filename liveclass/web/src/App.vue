<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from './api.js'
import Login from './views/Login.vue'
import Dashboard from './views/Dashboard.vue'
import ClassDetail from './views/ClassDetail.vue'
import Room from './views/Room.vue'
import AdminBrand from './views/AdminBrand.vue'
import Enter from './views/Enter.vue'
import Settings from './views/Settings.vue'
import Users from './views/Users.vue'

const user = ref(null)
const config = ref(null)
const route = reactive({ name: 'dashboard', param: null })
const toast = ref('')

function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [path, query = ''] = raw.split('?')
  const q = Object.fromEntries(new URLSearchParams(query))
  const seg = path.split('/').filter(Boolean)
  if (seg[0] === 'login') return { name: 'login' }
  if (seg[0] === 'class' && seg[1]) return { name: 'class', param: Number(seg[1]) }
  if (seg[0] === 'room' && seg[1]) return { name: 'room', param: seg[1] === 'student' ? 'student' : Number(seg[1]) }
  if (seg[0] === 'admin' && seg[1] === 'brand') return { name: 'admin-brand' }
  if (seg[0] === 'admin' && seg[1] === 'users') return { name: 'admin-users' }
  if (seg[0] === 'settings') return { name: 'settings' }
  if (seg[0] === 'enter') return { name: 'enter', query: q }
  return { name: 'dashboard' }
}
function applyRoute() { Object.assign(route, parseHash()) }

function showToast(msg) {
  toast.value = msg
  setTimeout(() => (toast.value = ''), 3500)
}

async function loadConfig() {
  try { config.value = await api.get('/api/config') } catch { config.value = { brand: { site_name: 'LiveClass' } } }
  const brand = config.value?.brand || {}
  if (brand.site_name) document.title = brand.site_name
  if (brand.primary_color) document.documentElement.style.setProperty('--brand', brand.primary_color)
  if (brand.tagline) document.querySelector('meta[name="description"]')?.setAttribute('content', brand.tagline)
}

async function loadMe() {
  try { const data = await api.get('/api/me'); user.value = data.user } catch { user.value = null }
}

async function logout() {
  try { await api.post('/api/auth/logout') } catch {}
  user.value = null
  location.hash = '#/login'
}

function onLoggedIn(u) {
  user.value = u
  location.hash = '#/'
}

const isTeacherView = computed(() => ['dashboard', 'class', 'admin-brand', 'admin-users', 'settings'].includes(route.name))
const needsUser = computed(() => route.name !== 'enter' && route.name !== 'login')

onMounted(async () => {
  applyRoute()
  window.addEventListener('hashchange', applyRoute)
  await loadConfig()
  await loadMe()
})
onUnmounted(() => window.removeEventListener('hashchange', applyRoute))
</script>

<template>
  <header class="navbar" v-if="user && isTeacherView">
    <a class="brand" href="#/">
      <img v-if="config?.brand?.has_logo" :src="`/api/brand/logo?v=${config.brand.brand_version}`" alt="logo" />
      <span>{{ config?.brand?.site_name || 'LiveClass' }}</span>
    </a>
    <div class="actions">
      <a class="btn sm ghost" href="#/">🏠 کلاس‌ها</a>
      <a v-if="user.role === 'admin'" class="btn sm ghost" href="#/admin/users">👥 کاربران</a>
      <a v-if="user.role === 'admin'" class="btn sm ghost" href="#/admin/brand">🎨 برند</a>
      <a class="btn sm ghost" href="#/settings">⚙️ تنظیمات</a>
      <button class="btn sm danger" @click="logout">خروج ({{ user.full_name }})</button>
    </div>
  </header>

  <Login v-if="route.name === 'login' || (needsUser && !user)" :brand="config?.brand" @logged-in="onLoggedIn" />

  <template v-else-if="route.name === 'enter'">
    <Enter :query="route.query || {}" :brand="config?.brand" />
  </template>

  <main class="container" v-else-if="route.name === 'dashboard'">
    <Dashboard :user="user" />
  </main>

  <main class="container" v-else-if="route.name === 'class'">
    <ClassDetail :class-id="route.param" :user="user" />
  </main>

  <Room v-else-if="route.name === 'room'" :mode="route.param === 'student' ? 'student' : 'teacher'" :session-id="route.param" :brand="config?.brand" />

  <main class="container" v-else-if="route.name === 'admin-brand'">
    <AdminBrand v-if="user?.role === 'admin'" :brand="config?.brand" @saved="loadConfig" />
    <div v-else class="card"><p>فقط ادمین دسترسی دارد.</p></div>
  </main>

  <main class="container" v-else-if="route.name === 'admin-users'">
    <Users v-if="user?.role === 'admin'" :user="user" />
    <div v-else class="card"><p>فقط ادمین دسترسی دارد.</p></div>
  </main>

  <main class="container" v-else-if="route.name === 'settings'">
    <Settings :brand="config?.brand" :user="user" />
  </main>

  <footer class="footer" v-if="isTeacherView">{{ config?.brand?.site_name || 'LiveClass' }} — پلتفرم اختصاصی شما</footer>
  <div v-if="toast" class="toast">{{ toast }}</div>
</template>
