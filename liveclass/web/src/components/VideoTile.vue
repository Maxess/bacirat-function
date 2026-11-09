<script setup>
/* کاشی ویدیو: اتصال ترک LiveKit به <video> و آواتار حرف اول هنگام خاموش بودن دوربین */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Track } from 'livekit-client'

const props = defineProps({
  track: { type: Object, default: null },   // Track نشر‌شده (یا null)
  name: { type: String, default: '' },
  speaking: { type: Boolean, default: false },
  camMuted: { type: Boolean, default: false },
  micMuted: { type: Boolean, default: true },
  isLocal: { type: Boolean, default: false },
})

const videoEl = ref(null)
const showVideo = computed(() => !!props.track && !props.camMuted)

function attach() {
  const el = videoEl.value
  if (!el) return
  props.track?.attach(el)
}
function detach() {
  const el = videoEl.value
  if (el && props.track) props.track.detach(el)
}
watch(() => props.track, (nw, old) => {
  if (old && old !== nw) old.detach(videoEl.value)
  attach()
})

onBeforeUnmount(detach)
watch(showVideo, (v) => { if (v) requestAnimationFrame(attach) })
</script>

<template>
  <div class="tile" :class="{ speaking, 'tile-local': isLocal }">
    <video v-show="showVideo" ref="videoEl" autoplay playsinline :muted="isLocal"></video>
    <div v-if="!showVideo" class="tile-avatar">
      <div class="circle">{{ (name || '؟').trim().charAt(0) }}</div>
    </div>
    <span class="tile-name">{{ isLocal ? name + ' (شما)' : name }}</span>
    <span v-if="micMuted" class="tile-mic-off">🔇</span>
  </div>
</template>
