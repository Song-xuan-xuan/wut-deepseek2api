<template>
  <div class="hbars">
    <div v-for="(item, index) in items" :key="item.name" class="hbar-row">
      <div class="name" :title="item.name">{{ item.name }}</div>
      <div class="hbar-track">
        <div
          class="hbar-fill"
          :style="{ width: `${item.percent}%`, background: `var(--chart-${(index % 6) + 1})` }"
        />
      </div>
      <div class="num">{{ item.display }}</div>
    </div>
    <div v-if="!items.length" class="muted small">暂无数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** [{ name, value, display? }] */
  data: { type: Array, default: () => [] },
})

const items = computed(() => {
  const total = props.data.reduce((sum, item) => sum + (item.value || 0), 0) || 1
  return props.data.map((item) => ({
    name: item.name,
    percent: Math.max(((item.value || 0) / total) * 100, item.value ? 2 : 0),
    display: item.display ?? String(item.value ?? 0),
  }))
})
</script>
