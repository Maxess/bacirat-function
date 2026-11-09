<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ brand: Object })
const emit = defineEmits(['saved'])
const siteName = ref('')
const siteNameShort = ref('')
const tagline = ref('')
const color = ref('#2563eb')
const logoFile = ref(null)
const iconFile = ref(null)
const logoPreview = ref('')
const iconPreview = ref('')
const error = ref('')
const notice = ref('')
const saving = ref(false)

const importKind = ref('students')
const importFile = ref(null)
const importResult = ref(null)

function bindPreview(input, which) {
  const f = input.files?.[0]
  if (!f) return
  if (f.size > 2 * 1024 * 1024) { error.value = 'حجم فایل بیش از ۲MB است'; input.value = ''; return }
  const reader = new FileReader()
  reader.onload = () => which === 'logo' ? (logoPreview.value = reader.result) : (iconPreview.value = reader.result)
  reader.readAsDataURL(f)
}

async function save() {
  error.value = ''; notice.value = ''
  saving.value = true
  try {
    const form = new FormData()
    form.append('site_name', siteName.value.trim())
    form.append('site_name_short', siteNameShort.value.trim())
    form.append('tagline', tagline.value.trim())
    form.append('primary_color', color.value)
    if (logoFile.value?.files?.[0]) form.append('logo', logoFile.value.files[0])
    if (iconFile.value?.files?.[0]) form.append('icon', iconFile.value.files[0])
    const data = await api.putForm('/api/admin/brand', form)
    notice.value = 'برند ذخیره شد ✅'
    emit('saved', data.brand)
    document.documentElement.style.setProperty('--brand', data.brand.primary_color)
    document.title = data.brand.site_name
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function doImport() {
  error.value = ''; importResult.value = null
  const f = importFile.value?.files?.[0]
  if (!f) { error.value = 'فایل CSV را انتخاب کنید'; return }
  const form = new FormData()
  form.append('file', f)
  try {
    const data = await api.postForm(`/api/admin/import/${importKind.value}`, form)
    importResult.value = data.report
  } catch (e) {
    error.value = e.message
  }
}

onMounted(() => {
  siteName.value = props.brand?.site_name || ''
  siteNameShort.value = props.brand?.site_name_short || ''
  tagline.value = props.brand?.tagline || ''
  color.value = props.brand?.primary_color || '#2563eb'
})
</script>

<template>
  <h1>⚙️ تنظیمات برند پلتفرم</h1>
  <p class="muted">این تنظیمات بومیِ پلتفرم شماست — بدون هیچ وصله‌ای، مستقیماً در دیتابیس و ظاهر ذخیره می‌شود.</p>

  <div class="card" style="margin:14px 0; max-width:640px">
    <label class="label">نام پلتفرم *</label>
    <input class="input" v-model="siteName" maxlength="64" />

    <label class="label">نام کوتاه (برای آیکون اپ)</label>
    <input class="input" v-model="siteNameShort" maxlength="24" />

    <label class="label">توضیح کوتاه / تگ‌لاین</label>
    <input class="input" v-model="tagline" maxlength="140" />

    <label class="label">رنگ اصلی</label>
    <div class="row"><input class="input" type="color" style="width:64px" v-model="color" /><code>{{ color }}</code></div>

    <div class="row" style="align-items:stretch; margin-top:10px">
      <div style="flex:1; min-width:220px">
        <label class="label">لوگو (PNG/JPEG/WEBP/SVG — حداکثر ۲MB)</label>
        <input ref="logoFile" type="file" accept="image/*" @change="bindPreview($event.target, 'logo')" />
        <div class="card" style="min-height:72px; display:grid; place-items:center; margin-top:8px; padding:8px">
          <img v-if="logoPreview" :src="logoPreview" alt="logo" style="max-height:64px; max-width:100%" />
          <img v-else-if="brand?.has_logo" :src="`/api/brand/logo?v=${brand?.brand_version || 1}`" alt="logo" style="max-height:64px; max-width:100%" />
          <span v-else class="muted small">لوگویی نیست</span>
        </div>
      </div>
      <div style="flex:1; min-width:220px">
        <label class="label">آیکون (مربع)</label>
        <input ref="iconFile" type="file" accept="image/*" @change="bindPreview($event.target, 'icon')" />
        <div class="card" style="min-height:72px; display:grid; place-items:center; margin-top:8px; padding:8px">
          <img v-if="iconPreview" :src="iconPreview" alt="icon" style="height:56px; width:56px; object-fit:contain" />
          <img v-else-if="brand?.has_icon" :src="`/api/brand/icon?v=${brand?.brand_version || 1}`" alt="icon" style="height:56px; width:56px; object-fit:contain" />
          <span v-else class="muted small">آیکونی نیست</span>
        </div>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="notice" class="notice">{{ notice }}</div>
    <button class="btn primary" style="margin-top:14px" :disabled="saving" @click="save">
      {{ saving ? 'در حال ذخیره…' : 'ذخیره برند' }}
    </button>
  </div>

  <div class="card" style="max-width:640px">
    <h2>📥 ایمپورت CSV</h2>
    <p class="muted small">ستون‌ها: دبیران <code>username,password,full_name</code> — کلاس‌ها <code>class_title,teacher_username</code> — دانش‌آموزان <code>student_code,full_name,class_title</code> (سرستون فارسی هم پشتیبانی می‌شود)</p>
    <div class="row">
      <select class="input" style="width:160px" v-model="importKind">
        <option value="teachers">دبیران</option>
        <option value="classes">کلاس‌ها</option>
        <option value="students">دانش‌آموزان</option>
      </select>
      <input ref="importFile" type="file" accept=".csv,text/csv" />
      <button class="btn primary" @click="doImport">ایمپورت</button>
    </div>
    <div v-if="importResult" class="notice" style="direction:ltr; text-align:left">
      <pre style="margin:0; white-space:pre-wrap">{{ JSON.stringify(importResult, null, 2) }}</pre>
    </div>
  </div>
</template>
