<template>
  <div>
    <div class="bars">
      <div
        v-for="point in points"
        :key="point.key"
        class="bar"
        :class="{ err: point.errors > 0 }"
        :style="{ height: point.height }"
      >
        <span class="tip">{{ point.tip }}</span>
      </div>
      <div v-if="!points.length" class="muted small" style="align-self: center">还没有请求</div>
    </div>
    <div class="legend">
      <span><i style="background: var(--chart-1)"></i>成功</span>
      <span><i style="background: var(--warning)"></i>含错误</span>
      <span v-if="unit" class="muted">{{ unit }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  labelKey: { type: String, default: 'bucket' },
  unit: { type: String, default: '' },
})

const points = computed(() => {
  const max = Math.max(...props.data.map((item) => item.requests || 0), 1)
  return props.data.map((item) => {
    const requests = item.requests || 0
    const errors = item.errors || 0
    const label = String(item[props.labelKey] ?? '').replace('T', ' ')
    return {
      key: item[props.labelKey],
      height: `${Math.max((requests / max) * 100, requests ? 3 : 0)}%`,
      errors,
      tip: `${label} · ${requests} 次${errors ? ` · ${errors} 错误` : ''}`,
    }
  })
})
</script>
