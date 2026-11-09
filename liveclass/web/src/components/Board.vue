<script setup>
/* تخته‌ی تعاملی LiveClass — سبک، آفلاین و بدون وابستگی خارجی.
   تمام مختصات به‌صورت نرمال‌شده (۰..۱) ذخیره می‌شوند تا روی هر اندازه‌ی صفحه یکسان رندر شوند. */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

const props = defineProps({
  canClear: { type: Boolean, default: false },   // فقط دبیر
  canDraw: { type: Boolean, default: true },
  pdf: { type: Object, default: null },           // {id, page, name, url} | null
})
const emit = defineEmits(['op', 'pdf-loaded'])    // op لوکال برای broadcast بیرون؛ تعداد صفحات بعد از load

const surface = ref(null)
const canvas = ref(null)
const pdfCanvas = ref(null)
const pdfLoading = ref(false)
const pdfError = ref('')
const pdfTotal = ref(0)
let pdfjsLib = null
let pdfDoc = null
let pdfDocId = null
const tool = ref('pen')          // pen | eraser
const color = ref('#1f2a44')
const size = ref(4)

const PALETTE = ['#1f2a44', '#e11d48', '#2563eb', '#059669', '#d97706', '#7c3aed', '#ffffff']
const SIZES = [2, 4, 8]

let ctx = null
let ops = []                       // همه‌ی عملیات (برای replay بعد از ریسایز)
const seen = new Set()             // op_idها برای جلوگیری از تکرار
let drawing = false
let current = null                 // stroke در حال کشیدن
let resizeObs = null

function cssSize() {
  const el = surface.value
  return { w: el?.clientWidth || 800, h: el?.clientHeight || 500 }
}
function setupCanvas() {
  const c = canvas.value
  const { w, h } = cssSize()
  const dpr = Math.min(window.devicePixelRatio || 1, 2)   // روی گوشی‌های ضعیف بیش از ۲ لازم نیست
  c.width = Math.round(w * dpr)
  c.height = Math.round(h * dpr)
  ctx = c.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  replay()
}
function normPoint(e) {
  const rect = canvas.value.getBoundingClientRect()
  const x = (e.clientX - rect.left) / rect.width
  const y = (e.clientY - rect.top) / rect.height
  return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))]
}
function paintStroke(s) {
  if (!s || s.t !== 'stroke') return
  const { w, h } = cssSize()
  const pts = s.pts || []
  if (pts.length < 4) return
  ctx.save()
  if (s.tool === 'eraser') {
    ctx.globalCompositeOperation = 'destination-out'
    ctx.strokeStyle = 'rgba(0,0,0,1)'
    ctx.lineWidth = (s.size || 4) * 4
  } else {
    ctx.globalCompositeOperation = 'source-over'
    ctx.strokeStyle = s.color || '#1f2a44'
    ctx.lineWidth = s.size || 4
  }
  ctx.beginPath()
  ctx.moveTo(pts[0] * w, pts[1] * h)
  for (let i = 2; i < pts.length; i += 2) ctx.lineTo(pts[i] * w, pts[i + 1] * h)
  ctx.stroke()
  ctx.restore()
}
function replay() {
  if (!ctx) return
  const { w, h } = cssSize()
  ctx.clearRect(0, 0, w, h)
  for (const s of ops) paintStroke(s)
}
async function ensurePdfjs() {
  if (pdfjsLib) return pdfjsLib
  pdfjsLib = await import('pdfjs-dist')
  const workerUrl = (await import('pdfjs-dist/build/pdf.worker.min.mjs?url')).default
  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl
  return pdfjsLib
}

function clearPdfCanvas() {
  pdfTotal.value = 0
  pdfDocId = null
  if (pdfDoc) { pdfDoc.destroy().catch(() => {}); pdfDoc = null }
  const c = pdfCanvas.value
  const ctx2 = c?.getContext('2d')
  if (c && ctx2) ctx2.clearRect(0, 0, c.width, c.height)
}

async function renderPdf() {
  const pdf = props.pdf
  if (!pdf) { clearPdfCanvas(); return }
  const canvasEl = pdfCanvas.value
  if (!canvasEl) return
  pdfLoading.value = true
  pdfError.value = ''
  try {
    const lib = await ensurePdfjs()
    if (!pdfDoc || pdfDocId !== pdf.id) {
      if (pdfDoc) { pdfDoc.destroy().catch(() => {}); pdfDoc = null }
      const resp = await fetch(pdf.url)
      if (!resp.ok) throw new Error(`بارگذاری PDF ناموفق بود (${resp.status})`)
      pdfDoc = await lib.getDocument({ data: await resp.arrayBuffer() }).promise
      pdfDocId = pdf.id
      pdfTotal.value = pdfDoc.numPages
      emit('pdf-loaded', { total: pdfDoc.numPages, name: pdf.name })
    }
    const pageNum = Math.min(Math.max(1, pdf.page || 1), pdfDoc.numPages)
    const page = await pdfDoc.getPage(pageNum)
    const cssW = Math.max(80, surface.value?.clientWidth || 800)
    const vp1 = page.getViewport({ scale: 1 })
    const viewport = page.getViewport({ scale: cssW / vp1.width })
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvasEl.width = Math.floor(viewport.width * dpr)
    canvasEl.height = Math.floor(viewport.height * dpr)
    const ctx2 = canvasEl.getContext('2d')
    ctx2.setTransform(dpr, 0, 0, dpr, 0, 0)
    await page.render({ canvasContext: ctx2, viewport }).promise
  } catch (e) {
    pdfError.value = e.message || 'خطا در نمایش PDF'
  } finally {
    pdfLoading.value = false
  }
}

function applyRemote(op) {
  if (!op || !op.id) return
  if (op.t === 'clear') {
    ops = []                  // seen دست‌نخورده می‌ماند تا strokeهای قبلی دیررسیده دوباره نرسند
    if (ctx) {
      const { w, h } = cssSize()
      ctx.clearRect(0, 0, w, h)
    }
    return
  }
  if (op.t === 'stroke') {
    if (seen.has(op.id)) return
    seen.add(op.id)
    ops.push(op)
    if (ctx) paintStroke(op)
  }
}
function loadInitial(list) {
  ops = []
  seen.clear()
  for (const op of list || []) {
    if (op.t === 'clear') { ops = []; seen.clear(); continue }
    if (op.t === 'stroke') { ops.push(op); seen.add(op.id) }
  }
  replay()
}
function newOpId() {
  return (crypto.randomUUID ? crypto.randomUUID() : `op-${Date.now()}-${Math.random().toString(36).slice(2)}`)
}

function onPointerDown(e) {
  if (!props.canDraw || !ctx) return
  e.preventDefault()
  canvas.value.setPointerCapture(e.pointerId)
  drawing = true
  const [x, y] = normPoint(e)
  current = { id: newOpId(), t: 'stroke', tool: tool.value, color: color.value, size: size.value, pts: [x, y] }
}
function onPointerMove(e) {
  if (!drawing || !current) return
  const [x, y] = normPoint(e)
  const pts = current.pts
  const n = pts.length
  const dx = x - pts[n - 2], dy = y - pts[n - 1]
  if (dx * dx + dy * dy < 0.00004) return   // تقریبِ آستانه‌ی پیکسلی — فیلتر نقاط ریز
  pts.push(x, y)
  paintStroke({ ...current, pts: pts.slice(-4) })   // فقط آخرین قطعه
}
function onPointerUp() {
  if (!drawing || !current) return
  drawing = false
  if (current.pts.length >= 4) {
    ops.push(current)
    seen.add(current.id)
    emit('op', { ...current })
  } else {
    replay()   // ضربه‌ی تکی نادیده گرفته شد؛ بازنقاشی
  }
  current = null
}

async function clearBoard() {
  if (!props.canClear) return
  const op = { t: 'clear', id: newOpId() }
  applyRemote(op)
  emit('op', { ...op })
}

onMounted(() => {
  setupCanvas()
  let rt = null
  resizeObs = new ResizeObserver(() => {
    setupCanvas()
    clearTimeout(rt)
    rt = setTimeout(() => { if (props.pdf) renderPdf() }, 150)
  })
  if (surface.value) resizeObs.observe(surface.value)
  if (props.pdf) renderPdf()
})
watch(() => [props.pdf?.id, props.pdf?.page], renderPdf)
onBeforeUnmount(() => { resizeObs?.disconnect(); clearPdfCanvas() })

defineExpose({ applyRemote, loadInitial, clearBoard })
</script>

<template>
  <div class="board-wrap">
    <div ref="surface" class="board-surface">
      <canvas v-show="pdf" ref="pdfCanvas" class="pdf-layer"></canvas>
      <div v-if="pdf && pdfLoading" class="pdf-loading">در حال بارگذاری کتاب…</div>
      <div v-if="pdf && pdfError" class="pdf-error">⚠️ {{ pdfError }}</div>
      <canvas ref="canvas" class="stroke-layer"
              @pointerdown="onPointerDown" @pointermove="onPointerMove"
              @pointerup="onPointerUp" @pointercancel="onPointerUp"></canvas>
    </div>
    <div class="board-tools">
      <button class="board-tool" :class="{ active: tool === 'pen' }" title="قلم" @click="tool = 'pen'">✏️</button>
      <button class="board-tool" :class="{ active: tool === 'eraser' }" title="پاک‌کن" @click="tool = 'eraser'">🧽</button>
      <span class="board-sep"></span>
      <button v-for="c in PALETTE" :key="c" class="board-color" :class="{ active: color === c }"
              :style="{ background: c }" :title="c" @click="color = c; tool = 'pen'"></button>
      <span class="board-sep"></span>
      <button v-for="s in SIZES" :key="s" class="board-tool" :class="{ active: size === s }"
              :title="'ضخامت ' + s" @click="size = s">
        <span :style="{ display: 'inline-block', width: (s + 3) + 'px', height: (s + 3) + 'px', borderRadius: '50%', background: 'currentColor' }"></span>
      </button>
      <span class="board-sep"></span>
      <button v-if="canClear" class="board-tool" title="پاک‌سازی کامل تخته (فقط دبیر)" @click="clearBoard">🗑️</button>
    </div>
  </div>
</template>
