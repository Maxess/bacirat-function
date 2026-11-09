// ترجیحات کیفیت ویدیو — سمت مرورگر (localStorage) + همگام با سرور برای حساب‌های لاگین
const KEY = 'lcVideoPrefs'
export const DEFAULT_PREFS = { video_preset: 'optimized', video_fps: 15 }

export const PRESETS = {
  optimized: { label: 'اقتصادی (Optimized)', short: 'اقتصادی', desc: '۶۴۰×۳۶۰ — برای سرور و اینترنت ضعیف؛ کمترین مصرف پهنای باند', w: 640, h: 360 },
  balanced:  { label: 'متعادل (Balanced)',  short: 'متعادل',  desc: '۹۶۰×۵۴۰ — تعادل کیفیت و مصرف؛ مناسب اکثر کلاس‌ها',        w: 960, h: 540 },
  quality:   { label: 'کیفیت بالا (Quality)', short: 'کیفیت بالا', desc: '۱۲۸۰×۷۲۰ — بهترین تصویر؛ به پهنای باند خوب نیاز دارد',  w: 1280, h: 720 },
}
export const FPS_OPTIONS = [15, 20, 24]

export function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}')
    const preset = PRESETS[raw.video_preset] ? raw.video_preset : DEFAULT_PREFS.video_preset
    const fps = FPS_OPTIONS.includes(raw.video_fps) ? raw.video_fps : DEFAULT_PREFS.video_fps
    return { video_preset: preset, video_fps: fps }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function savePrefsLocal(p) {
  localStorage.setItem(KEY, JSON.stringify({ video_preset: p.video_preset, video_fps: p.video_fps }))
}
