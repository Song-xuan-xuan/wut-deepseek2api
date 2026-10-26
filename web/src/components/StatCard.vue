<template>
  <div class="card" :class="{ accent }">
    <div class="label">
      <span v-if="icon" class="icon-chip">{{ icon }}</span>
      <span>{{ label }}</span>
      <span v-if="hint" class="pill" :class="tone" style="margin-left: auto">{{ hint }}</span>
    </div>
    <div class="value" :class="[{ sm: small }, accent ? '' : tone]">{{ value }}</div>
    <div v-if="sub" class="sub">{{ sub }}</div>
    <svg
      v-if="spark && spark.length > 1"
      class="spark"
      :viewBox="`0 0 ${spark.length - 1} 34`"
      preserveAspectRatio="none"
    >
      <polyline
        :points="sparkPoints"
        fill="none"
        :stroke="accent ? 'currentColor' : `var(--chart-${colorIndex})`"
        stroke-width="1.6"
        stroke-linejoin="round"
        stroke-linecap="round"
        vector-effect="non-scaling-stroke"
        opacity="0.85"
      />
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  sub: { type: String, default: '' },
  hint: { type: String, default: '' },
  icon: { type: String, default: '' },
  tone: { type: String, default: '' }, // ok / warn / bad
  small: { type: Boolean, default: false },
  accent: { type: Boolean, default: false },
  spark: { type: Array, default: () => [] },
  colorIndex: { type: Number, default: 1 },
})

const sparkPoints = computed(() => {
  const values = props.spark
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  return values
    .map((value, index) => `${index},${(34 - ((value - min) / span) * 30 - 2).toFixed(2)}`)
    .join(' ')
})
</script>
