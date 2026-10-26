<template>
  <div>
    <div class="section-row" :class="{ open }" @click="open = !open">
      <div class="icon-chip">{{ icon }}</div>
      <div>
        <div class="title">{{ title }}</div>
        <div v-if="desc" class="desc">{{ desc }}</div>
      </div>
      <span class="chev">›</span>
    </div>
    <div v-if="open" style="margin-top: 14px">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  desc: { type: String, default: '' },
  icon: { type: String, default: '▤' },
  defaultOpen: { type: Boolean, default: false },
})

const STORE_KEY = `whut-section-${props.title}`
const saved = localStorage.getItem(STORE_KEY)
const open = ref(saved ? saved === 'open' : props.defaultOpen)

watch(open, (value) => localStorage.setItem(STORE_KEY, value ? 'open' : 'closed'))
</script>
