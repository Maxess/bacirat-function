<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ brand: Object })
const emit = defineEmits(['logged-in'])
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.post('/api/auth/login', { username: username.value.trim(), password: password.value })
    emit('logged-in', data.user)
  } catch (e) {
    error.value = e.status === 401 ? 'نام کاربری یا رمز اشتباه است.' : `خطا: ${e.message}`
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="card login-card" @submit.prevent="submit">
      <img v-if="brand?.has_logo" class="logo" :src="`/api/brand/logo?v=${brand?.brand_version || 1}`" alt="logo" />
      <div v-else class="brand-mark">🎓</div>
      <h1>{{ brand?.site_name || 'LiveClass' }}</h1>
      <p class="muted">{{ brand?.tagline || 'ورود دبیران و مدیران' }}</p>

      <label class="label" for="u">نام کاربری</label>
      <input id="u" class="input" v-model="username" autocomplete="username" required autofocus />

      <label class="label" for="p">رمز عبور</label>
      <input id="p" class="input" v-model="password" type="password" autocomplete="current-password" required />

      <div class="error" v-if="error">{{ error }}</div>
      <button class="btn primary" type="submit" :disabled="loading" style="width:100%; justify-content:center; margin-top:16px">
        {{ loading ? 'در حال ورود…' : 'ورود' }}
      </button>
    </form>
  </div>
</template>
