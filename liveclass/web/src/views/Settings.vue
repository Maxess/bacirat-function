<script setup>
/* تنظیمات حساب: تغییر رمز + کیفیت ویدیو (۳ حالت × فریم‌ریت ۱۵/۲۰/۲۴) + (ادمین) بکاپ کامل سرور */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import { loadPrefs, savePrefsLocal, PRESETS, FPS_OPTIONS } from '../prefs.js'

const props = defineProps({ brand: Object, user: Object })
const isAdmin = () => props.user?.role === 'admin'

const current = ref(''), next1 = ref(''), next2 = ref('')
const passMsg = ref(''), passErr = ref(''), passLoading = ref(false)

const prefs = ref(loadPrefs())
const prefMsg = ref('')

async function changePassword() {
  passMsg.value = ''; passErr.value = ''
  if (next1.value.length < 6) { passErr.value = 'رمز جدید حداقل ۶ کاراکتر باشد.'; return }
  if (next1.value !== next2.value) { passErr.value = 'تکرار رمز یکسان نیست.'; return }
  passLoading.value = true
  try {
    await api.post('/api/me/password', { current_password: current.value, new_password: next1.value })
    passMsg.value = 'رمز شما با موفقیت تغییر کرد ✅'
    current.value = next1.value = next2.value = ''
  } catch (e) {
    passErr.value = e.message
  } finally {
    passLoading.value = false
  }
}

async function savePrefs() {
  savePrefsLocal(prefs.value)
  prefMsg.value = ''
  try {
    await api.put('/api/me/preferences', prefs.value)
    prefMsg.value = 'ذخیره شد ✅ در جلسه‌ی بعدی اتاق اعمال می‌شود.'
  } catch (e) {
    prefMsg.value = 'در این مرورگر ذخیره شد (حساب شما آفلاین است) — برای اتاق اعمال می‌شود.'
  }
}

onMounted(async () => {
  try {
    const d = await api.get('/api/me/preferences')
    prefs.value = { video_preset: d.video_preset, video_fps: d.video_fps }
    savePrefsLocal(prefs.value)
  } catch { /* دانش‌آموز/مهمان بدون کوکی — همان localStorage */ }
})
</script>

<template>
  <h1>⚙️ تنظیمات</h1>

  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); margin-top:12px; align-items:start">

    <form class="card" @submit.prevent="changePassword">
      <h3>🔑 تغییر رمز عبور</h3>
      <label class="label" for="cur">رمز فعلی</label>
      <input id="cur" class="input" type="password" v-model="current" autocomplete="current-password" required />
      <label class="label" for="n1">رمز جدید</label>
      <input id="n1" class="input" type="password" v-model="next1" autocomplete="new-password" required minlength="6" />
      <label class="label" for="n2">تکرار رمز جدید</label>
      <input id="n2" class="input" type="password" v-model="next2" autocomplete="new-password" required minlength="6" />
      <div v-if="passErr" class="error">{{ passErr }}</div>
      <div v-if="passMsg" class="notice">{{ passMsg }}</div>
      <button class="btn primary" type="submit" :disabled="passLoading" style="margin-top:12px">{{ passLoading ? 'در حال ذخیره…' : 'تغییر رمز' }}</button>
    </form>

    <div class="card">
      <h3>🎥 کیفیت تصویر ویدیو</h3>
      <p class="muted small">دقت تصویر در سه حالت بهینه + فریم‌ریت ۱۵/۲۰/۲۴. این تنظیم برای جلسه‌های بعدی اتاق شما اعمال می‌شود.</p>
      <div class="quality-options">
        <label v-for="(p, key) in PRESETS" :key="key" class="quality-option" :class="{ active: prefs.video_preset === key }">
          <input type="radio" :value="key" v-model="prefs.video_preset" />
          <div>
            <div class="qo-title">{{ p.label }}</div>
            <div class="muted small">{{ p.desc }}</div>
          </div>
        </label>
      </div>
      <label class="label" for="fps">فریم‌ریت (فریم بر ثانیه)</label>
      <select id="fps" class="input" v-model.number="prefs.video_fps">
        <option v-for="f in FPS_OPTIONS" :key="f" :value="f">{{ f }} فریم — {{ f === 15 ? 'بهینه برای سرور ضعیف' : f === 20 ? 'متعادل' : 'روان‌تر' }}</option>
      </select>
      <div v-if="prefMsg" class="notice">{{ prefMsg }}</div>
      <button class="btn primary" style="margin-top:12px" @click="savePrefs">ذخیره‌ی کیفیت</button>
    </div>

    <div v-if="isAdmin()" class="card">
      <h3>🗄️ بکاپ کامل سرور</h3>
      <p class="muted small">یک فایل zip شامل: کپی سازگار دیتابیس (<code>liveclass.db</code>) + خروجی JSON همه‌ی داده‌ها (کاربران، کلاس‌ها، جلسات برگزارشده، حضور و غیاب، توکن‌ها، تخته) + فایل‌های برند.</p>
      <a class="btn primary" style="margin-top:8px" href="/api/admin/backup">⬇️ دانلود بکاپ (.zip)</a>
      <p class="muted small" style="margin-top:10px">⚠️ این فایل شامل داده‌های کامل مدرسه است؛ آن را در جای امن نگه دارید. برای بازگردانی، فایل README داخل zip را ببینید.</p>
      <hr class="sep" />
      <a class="btn ghost sm" href="#/admin/brand">🎨 تنظیمات برند ←</a>
      <a class="btn ghost sm" href="#/admin/users" style="margin-inline-start:6px">👥 مدیریت کاربران ←</a>
    </div>
  </div>
</template>
